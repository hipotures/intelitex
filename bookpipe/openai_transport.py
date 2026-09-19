from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .contracts import normalized_usage
from .evidence import AttemptRecorder
from .ui import Display
from .util import PipelineError, dumps


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(self, profile: dict[str, Any], ui: Display):
        self.settings = profile
        self.ui = ui
        self.profile_name = profile["profile_name"]
        self.resolved_profile = profile["resolved_profile"]
        endpoint = profile.get("endpoint") or "https://api.openai.com/v1"
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PipelineError("OpenAI endpoint must be an absolute HTTP(S) URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PipelineError("Credentials, query strings and fragments are forbidden in OpenAI endpoint URLs.")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise PipelineError("OpenAI credentials may only be sent over HTTPS (except explicit loopback test endpoints).")
        path = parsed.path.rstrip("/")
        if not path.endswith("/v1"):
            path += "/v1"
        self.base = urlunsplit((parsed.scheme, parsed.netloc, path + "/", "", ""))
        self.timeout = float(profile.get("request_timeout", 1200))
        env_name = profile.get("credential_env") or "OPENAI_API_KEY"
        key = os.environ.get(env_name)
        if not key:
            raise PipelineError(f"OpenAI profile requires credential environment variable {env_name}.")
        self.http = httpx.Client(
            base_url=self.base,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(self.timeout, connect=15),
            trust_env=False,
            follow_redirects=False,
        )
        self.model = profile.get("model")
        self.context = int(profile["context_size"])
        self.identity = {"requested_model": self.model, "provider": "openai"}
        self._text_counts: dict[str, int] = {}

    def close(self) -> None:
        self.http.close()

    @property
    def tokenizer_identity(self) -> dict[str, Any]:
        return {"provider": "openai", "model": self.model, "method": "utf8_byte_upper_bound"}

    def discover(self) -> dict[str, Any]:
        try:
            response = self.http.get("models")
            response.raise_for_status()
            entries = response.json().get("data", [])
            match = next((x for x in entries if x.get("id") == self.model), None)
            if match is None:
                raise PipelineError(f"Requested OpenAI model {self.model!r} was not returned by /v1/models.")
            self.identity = {"requested_model": self.model, "reported_model": match.get("id"), "metadata": match}
            return self.identity
        except httpx.HTTPStatusError as exc:
            raise PipelineError(f"OpenAI discovery HTTP {exc.response.status_code}.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise PipelineError(f"OpenAI discovery failed: {exc}") from exc

    def count(self, text: str) -> int:
        # A byte upper bound is intentionally conservative and tokenizer-agnostic.
        # It is not presented as measured tokens and is only used for local packing.
        return len(text.encode("utf-8"))

    def body(self, prompt: str, inputs: dict, schema: dict, pass_no: int) -> dict[str, Any]:
        name = re.sub(r"[^A-Za-z0-9_-]", "_", f"intelitex_pass_{pass_no}")
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": prompt,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": dumps(inputs)}]}],
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            "stream": True,
            "store": False,
            "truncation": "disabled",
            "tools": [],
        }
        cap = self.settings.get("max_output_tokens")
        if cap is not None:
            body["max_output_tokens"] = int(cap)
        effort = self.settings.get("reasoning_effort")
        if effort is not None:
            body["reasoning"] = {"effort": effort}
        options = self.settings.get("options", {})
        reserved = {"model", "instructions", "input", "text", "stream", "store", "truncation", "tools", "previous_response_id", "conversation", "background"}
        for key, value in options.items():
            if key in reserved:
                raise PipelineError(f"OpenAI profile options cannot override reserved key {key!r}.")
            body[key] = value
        return body

    def preflight(self, body: dict, recorder: AttemptRecorder | None = None) -> int:
        count_body = {k: v for k, v in body.items() if k not in {"stream", "store", "truncation", "max_output_tokens", "tools"}}
        if recorder:
            recorder.event("outbound", "openai_input_tokens_request", {"method": "POST", "path": "/v1/responses/input_tokens", "body": count_body})
        try:
            response = self.http.post("responses/input_tokens", json=count_body)
            if response.status_code in {404, 405, 501}:
                raise PipelineError("OpenAI /v1/responses/input_tokens is unavailable for this endpoint/profile; no zero or guessed count was substituted.")
            response.raise_for_status()
            payload = response.json()
            count = payload.get("input_tokens")
            if not isinstance(count, int):
                raise PipelineError("OpenAI input-token response omitted integer input_tokens.")
        except httpx.HTTPStatusError as exc:
            if recorder:
                recorder.event("inbound", "openai_input_tokens_error", {"status": exc.response.status_code, "body": exc.response.text[:1500]})
            raise PipelineError(f"OpenAI token preflight HTTP {exc.response.status_code}: {exc.response.text[:600]}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise PipelineError(f"OpenAI token preflight failed: {exc}") from exc
        reserve = int(self.settings["planning_output_reserve"])
        required = count + reserve
        if recorder:
            recorder.event("inbound", "openai_input_tokens_result", payload)
            recorder.context({
                "method": "POST /v1/responses/input_tokens", "quality": "provider_exact",
                "tokenizer_identity": {"provider": "openai", "model": self.model},
                "input_tokens": count, "capacity_tokens": self.context,
                "planning_output_reserve": reserve,
                "enforced_output_cap": body.get("max_output_tokens"),
                "required_tokens": required, "silent_truncation": False,
            })
        if required > self.context:
            raise PipelineError(f"OpenAI request needs {required:,} planned tokens but profile capacity is {self.context:,}; source was not truncated.")
        return count

    @staticmethod
    def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
        allowed = {"x-request-id", "openai-processing-ms", "retry-after"}
        return {key: value for key, value in headers.items()
                if key.lower() in allowed or key.lower().startswith("x-ratelimit-")}

    def generate(self, body: dict, directory: Path, recorder: AttemptRecorder | None = None) -> tuple[str, dict]:
        if recorder is None:
            raise PipelineError("OpenAI generation requires an active evidence recorder.")
        recorder.transport_request(body, body["text"]["format"]["schema"])
        answer: list[str] = []
        reasoning: list[str] = []
        terminal: dict[str, Any] | None = None
        usage_events: list[Any] = []
        started = time.monotonic()
        expired = threading.Event()
        response_ref: list[httpx.Response] = []

        def cancel() -> None:
            expired.set()
            if response_ref:
                response_ref[0].close()

        timer = threading.Timer(self.timeout, cancel)
        timer.daemon = True
        timer.start()
        try:
            with self.http.stream("POST", "responses", json=body) as response:
                response_ref.append(response)
                recorder.event("inbound", "http_headers", {"status": response.status_code, "headers": self._safe_headers(response.headers)})
                if not response.is_success:
                    response.read()
                    raise PipelineError(f"OpenAI Responses HTTP {response.status_code}: {response.text[:1500]}")
                event_name: str | None = None
                data_lines: list[str] = []
                for line in response.iter_lines():
                    if expired.is_set():
                        raise PipelineError("OpenAI request timed out; partial output retained and remote outcome may be unknown.")
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line or not data_lines:
                        continue
                    raw = "\n".join(data_lines)
                    data_lines = []
                    if raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        recorder.event("inbound", "malformed_sse", raw)
                        raise PipelineError(f"Malformed OpenAI SSE JSON: {exc}") from exc
                    kind = event.get("type") or event_name or "unknown"
                    recorder.event("inbound", kind, event)
                    if kind == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if not isinstance(delta, str):
                            raise PipelineError("OpenAI output delta was not text.")
                        answer.append(delta)
                        recorder.append_text("answer.partial.txt", delta)
                        self.ui.received(sum(map(len, answer)), sum(map(len, reasoning)))
                    elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                        delta = event.get("delta", "")
                        if isinstance(delta, str):
                            reasoning.append(delta)
                            recorder.append_text("reasoning.partial.txt", delta)
                    if event.get("response", {}).get("usage") is not None:
                        usage_events.append(event["response"]["usage"])
                    if kind == "error":
                        raise PipelineError(f"OpenAI streaming error: {event.get('error') or event}")
                    if kind in {"response.completed", "response.failed", "response.incomplete"}:
                        terminal = event.get("response") or event
                if terminal is None:
                    raise PipelineError("OpenAI stream ended without a terminal response event.")
            status = terminal.get("status")
            usage = terminal.get("usage") or (usage_events[-1] if usage_events else {})
            if terminal.get("usage") is not None:
                usage_events.append(terminal["usage"])
            input_details = usage.get("input_tokens_details") or {}
            output_details = usage.get("output_tokens_details") or {}
            normalized = normalized_usage(
                input_tokens=usage.get("input_tokens"),
                cached_input_tokens=input_details.get("cached_tokens"),
                cache_write_input_tokens=input_details.get("cache_write_tokens"),
                output_tokens=usage.get("output_tokens"),
                reasoning_output_tokens=output_details.get("reasoning_tokens"),
                total_tokens=usage.get("total_tokens"),
                source="openai_terminal_response", scope="response",
                status="reported" if usage else "unavailable",
            )
            recorder.usage(usage_events, normalized)
            for item in terminal.get("output", []):
                if item.get("type") == "message":
                    for content in item.get("content", []):
                        if content.get("type") == "refusal":
                            raise PipelineError(f"OpenAI refused the request: {content.get('refusal')}")
            if status != "completed":
                detail = terminal.get("error") or terminal.get("incomplete_details") or status
                raise PipelineError(f"OpenAI response terminal status is {status!r}: {detail}")
            raw_answer = "".join(answer).strip()
            if not raw_answer:
                # Terminal response can carry a complete output without deltas.
                for item in terminal.get("output", []):
                    if item.get("type") == "message":
                        for content in item.get("content", []):
                            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                                raw_answer += content["text"]
            if not raw_answer:
                raise PipelineError("OpenAI completed without visible output text.")
            recorder.write_answer(raw_answer, "".join(reasoning))
            meta = {
                "provider": "openai", "response_id": terminal.get("id"),
                "requested_model": self.model, "reported_model": terminal.get("model"),
                "status": status, "finish_reason": "stop",
                "incomplete_details": terminal.get("incomplete_details"),
                "error": terminal.get("error"), "usage_status": normalized["status"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "terminal_response": terminal,
            }
            return raw_answer, meta
        except (httpx.HTTPError, OSError) as exc:
            raise PipelineError(f"OpenAI stream interrupted: {exc}") from exc
        finally:
            timer.cancel()
            if response_ref:
                response_ref[0].close()
