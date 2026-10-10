import httpx
import pytest

from bookpipe.client import Client, ContextFull
from bookpipe.contracts import preflight_measurement, preflight_metadata
from bookpipe.evidence import AttemptRecorder
from bookpipe.util import PipelineError, read_json


def client_for(handler):
    client = Client({"model": "test", "context_size": 32768}, None)
    client.http.close()
    client.http = httpx.Client(base_url=client.base, transport=httpx.MockTransport(handler))
    return client


@pytest.mark.parametrize("status", [404, 405, 501])
def test_missing_tokenizer_uses_bytes_without_reporting_tokens(tmp_path, status):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(status, json={"error": "unsupported"})

    client = client_for(handler)
    recorder = AttemptRecorder(tmp_path / "attempt", {"provider": "llamacpp"})
    body = {"messages": [{"role": "user", "content": "Zażółć 🐈"}], "max_tokens": 32,
            "response_format": {"type": "json_object", "schema": {"type": "object"}}}
    try:
        count = client.preflight(body, recorder)
        assert client.count("Zażółć 🐈") == len("Zażółć 🐈".encode())
        measurement = preflight_measurement(client, count)
        assert measurement["unit"] == "utf8_bytes"
        assert measurement["quality"] == "conservative_upper_bound"
        assert "input_tokens_preflight" not in preflight_metadata(measurement)
        assert client.tokenizer_identity["unit"] == "utf8_bytes"
        context = read_json(tmp_path / "attempt/context.json")
        assert "input_tokens" not in context
        assert context["input_upper_bound"] == count
        assert client.preflight(body) == count
        assert paths == ["/v1/chat/completions/input_tokens", "/apply-template", "/tokenize"]
        client.context = count + body["max_tokens"] + 1023
        with pytest.raises(ContextFull):
            client.preflight(body)
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_tokenizer_errors_are_not_hidden(status):
    client = client_for(lambda request: httpx.Response(status, json={"error": "failed"}))
    try:
        with pytest.raises(PipelineError, match=f"HTTP {status}"):
            client.count("test")
        assert client.tokenize_endpoint is None
        with pytest.raises(PipelineError, match=f"HTTP {status}"):
            client.preflight({"messages": [], "max_tokens": 32})
    finally:
        client.close()


def test_exact_request_count_still_works_without_text_tokenizer():
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 17})
        return httpx.Response(404, json={"error": "unsupported"})

    client = client_for(handler)
    try:
        assert client.count("test") == 4
        assert client.preflight({"messages": [], "max_tokens": 32}) == 17
        assert preflight_measurement(client, 17)["unit"] == "tokens"
        assert client.preflight_input_quality == "provider_exact"
    finally:
        client.close()


def test_malformed_tokenizer_response_still_fails():
    client = client_for(lambda request: httpx.Response(200, json={"tokens": None}))
    try:
        with pytest.raises(PipelineError, match="did not return a token list"):
            client.count("test")
    finally:
        client.close()


@pytest.mark.parametrize("build", ["Strata 0.1.39", "llama.cpp"])
def test_strata_gets_its_supported_schema_format(build):
    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "test"}]})
        return httpx.Response(200, json={"build_info": build, "default_generation_settings": {"n_ctx": 32768}})

    client = client_for(handler)
    client.settings["passes"] = {"1": {"temperature": 0.1, "max_tokens": 100}}
    try:
        client.discover()
        schema = {"type": "object"}
        format = client.body("Return JSON", {}, schema, 1)["response_format"]
        if build.startswith("Strata"):
            assert format == {"type": "json_schema", "json_schema": {
                "name": "intelitex_pass1", "strict": True, "schema": schema,
            }}
        else:
            assert format == {"type": "json_object", "schema": schema}
    finally:
        client.close()
