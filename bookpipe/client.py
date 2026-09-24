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
from .progress import ProgressEvent
from .util import PipelineError, atomic_json, atomic_text, digest, dumps


class ContextFull(PipelineError):
    pass


class Client:
    provider = "llamacpp"
    preflight_input_unit = "tokens"
    preflight_input_quality = "native_or_tokenizer_count"
    preflight_input_method = "llama.cpp complete-request token count"

    def __init__(self, settings: dict[str, Any], ui: Any):
        self.settings, self.ui = settings, ui
        host = settings.get("host", "127.0.0.1")
        if "://" not in host:
            host = "http://" + (f"[{host}]" if host.count(":") > 1 and not host.startswith("[") else host)
        url = urlsplit(host)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise PipelineError("Host must be an IP address or an http(s) URL.")
        if url.username or url.password or url.query or url.fragment:
            raise PipelineError("Credentials, query strings and fragments are not accepted in --host.")
        hostname = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        # Legacy bare hosts keep port 8080. Explicit URLs use their normal
        # scheme default unless a port was explicitly configured.
        explicit_url = "://" in settings.get("host", "127.0.0.1")
        port = url.port or (settings.get("port") if not explicit_url else None)
        port = port or (443 if url.scheme == "https" else 80)
        prefix = url.path.rstrip("/")
        if prefix.endswith("/v1"):
            prefix = prefix[:-3]
        self.base = urlunsplit((url.scheme, f"{hostname}:{port}", prefix, "", ""))
        self.timeout = float(settings.get("request_timeout", 1200))
        headers = {}
        key = os.environ.get(settings.get("credential_env", "LLAMA_API_KEY"))
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=self.base + "/", headers=headers,
                                 timeout=httpx.Timeout(self.timeout, connect=15), trust_env=False)
        self._tokens: dict[str, int] = {}
        self.model = settings.get("model")
        self.context = int(settings.get("context_size") or 0)
        self.identity: dict[str, Any] = {}
        self.count_endpoint: bool | None = None
        self.profile_name = settings.get("profile_name", "local")
        self.resolved_profile = settings.get("resolved_profile", {})

    def close(self):
        self.http.close()

    @property
    def tokenizer_identity(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "model_identity": self.identity}

    def post(self, path: str, body: dict) -> dict:
        try:
            response = self.http.post(path.lstrip("/"), json=body)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise PipelineError(f"Unexpected response from {path}.")
            return value
        except httpx.HTTPStatusError as exc:
            raise PipelineError(f"{path}: HTTP {exc.response.status_code}: {exc.response.text[:1500]}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise PipelineError(f"{path}: {exc}") from exc

    def discover(self):
        try:
            models = self.http.get("v1/models")
            models.raise_for_status()
            entries = models.json().get("data", [])
            chosen = next((m for m in entries if m["id"] == self.model), None) if self.model else None
            if chosen is None and self.model:
                raise PipelineError(f"Requested model {self.model!r} is not in /v1/models.")
            if chosen is None:
                if len(entries) != 1:
                    raise PipelineError("Select one loaded model with --model.")
                chosen = entries[0]
            self.model = chosen["id"]
            self.identity = {"id": self.model, "meta": chosen.get("meta")}
            props = self.http.get("props")
            detected = 0
            if props.is_success:
                data = props.json()
                gen = data.get("default_generation_settings", {})
                detected = int(gen.get("n_ctx") or data.get("n_ctx") or 0)
                self.identity["model_path"] = data.get("model_path")
            if self.context and detected:
                self.context = min(self.context, detected)
            elif not self.context:
                self.context = detected
            if not self.context:
                raise PipelineError("Cannot determine per-request context capacity. Set --context-size explicitly.")
            return self.identity
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise PipelineError(f"Cannot discover llama.cpp at {self.base}: {exc}") from exc

    def count(self, text: str, recorder: AttemptRecorder | None = None) -> int:
        key = digest(text)
        if key not in self._tokens:
            if recorder:
                recorder.event("outbound", "http_request", {"method": "POST", "path": "/tokenize",
                                                              "body": {"content": text, "add_special": False, "parse_special": False}})
            data = self.post("/tokenize", {"content": text, "add_special": False, "parse_special": False})
            if recorder:
                recorder.event("inbound", "http_response", {"path": "/tokenize", "body": data})
            tokens = data.get("tokens")
            if not isinstance(tokens, list):
                raise PipelineError("/tokenize did not return a token list. No estimated fallback is used.")
            self._tokens[key] = len(tokens)
        return self._tokens[key]

    def count_request(self, body: dict, recorder: AttemptRecorder | None = None) -> int:
        # Recent llama.cpp can count the exact rendered prompt including the template.
        if self.count_endpoint is not False:
            if recorder:
                recorder.event("outbound", "http_request", {"method": "POST", "path": "/v1/chat/completions/input_tokens", "body": body})
            response = self.http.post("v1/chat/completions/input_tokens", json=body)
            if recorder:
                recorder.event("inbound", "http_response", {"path": "/v1/chat/completions/input_tokens",
                                                              "status": response.status_code,
                                                              "body": response.text[:2000]})
            if response.is_success:
                n = response.json().get("input_tokens")
                if isinstance(n, int):
                    self.count_endpoint = True
                    self.preflight_input_quality = "provider_exact"
                    self.preflight_input_method = "llama.cpp POST /v1/chat/completions/input_tokens"
                    return n
                raise PipelineError("Unexpected input_tokens response.")
            if response.status_code not in (404, 405, 501):
                raise PipelineError(f"Token preflight failed: HTTP {response.status_code}: {response.text[:600]}")
            self.count_endpoint = False
        # /apply-template has been available for longer. A margin covers auxiliary
        # grammar/reasoning/template data on versions that do not count it exactly.
        template_body = {"messages": body["messages"], "chat_template_kwargs": body.get("chat_template_kwargs", {})}
        if recorder:
            recorder.event("outbound", "http_request", {"method": "POST", "path": "/apply-template", "body": template_body})
        response = self.http.post("apply-template", json=template_body)
        if recorder:
            recorder.event("inbound", "http_response", {"path": "/apply-template", "status": response.status_code,
                                                          "body": response.text[:2000]})
        if response.is_success and isinstance(response.json().get("prompt"), str):
            self.preflight_input_quality = "verified_tokenizer_with_estimated_wrapper"
            self.preflight_input_method = "llama.cpp /apply-template then /tokenize plus wrapper margin"
            return self.count(response.json()["prompt"], recorder) + 512
        # This fallback still tokenizes real input; it is conservative, not chars/4.
        self.preflight_input_quality = "verified_tokenizer_with_estimated_wrapper"
        self.preflight_input_method = "llama.cpp serialized messages /tokenize plus wrapper margin"
        return self.count(dumps(body["messages"]), recorder) + 2048

    def body(self, prompt: str, inputs: dict, schema: dict, pass_no: int) -> dict:
        cfg = self.settings["passes"][str(pass_no)]
        on = self.settings.get("thinking", "off") == "analysis" and pass_no in (1, 2, 4)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": dumps(inputs)},
            ],
            "temperature": float(cfg["temperature"]),
            "max_tokens": int(cfg["max_tokens"]),
            "seed": int(self.settings.get("seed", 42)),
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": on},
            "reasoning_format": "deepseek",
            "response_format": {"type": "json_object", "schema": schema},
        }
        if not on:
            body["reasoning_effort"] = "none"
        # Do not pretend a textual "low budget" is an enforced reasoning limit.
        # max_tokens and the wall deadline are the actual bounds in this client.
        for key, value in self.settings.get("request_extra", {}).items():
            if key in {"model", "messages", "stream", "response_format", "max_tokens"}:
                raise PipelineError(f"request_extra cannot override reserved key {key}.")
            body[key] = value
        return body

    def preflight(self, body: dict, recorder: AttemptRecorder | None = None) -> int:
        count = self.count_request(body, recorder)
        required = count + body["max_tokens"] + 1024
        if required > self.context:
            raise ContextFull(
                f"Request needs {required:,} tokens including output reserve and margin; "
                f"server permits {self.context:,}. No source text was truncated."
            )
        if recorder:
            recorder.event("inbound", "token_count_result", {"input_tokens": count})
            recorder.context({
                "method": self.preflight_input_method,
                "quality": self.preflight_input_quality,
                "tokenizer_identity": {"provider": "llamacpp", "model": self.model},
                "input_tokens": count,
                "capacity_tokens": self.context,
                "planning_output_reserve": body["max_tokens"],
                "safety_margin": 1024,
                "required_tokens": required,
                "silent_truncation": False,
            })
        return count

    def generate(self, body: dict, directory: Path, recorder: AttemptRecorder | None = None) -> tuple[str, dict]:
        """Read SSE incrementally, persist partial output, accept only complete EOS."""
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "request.json", body)
        if recorder:
            response_format = body.get("response_format", {})
            schema = response_format.get("schema") or response_format.get("json_schema", {}).get("schema", {})
            recorder.transport_request(body, schema)
        answer, thought = [], []
        finish = None
        info: dict[str, Any] = {"model": self.model, "events": 0}
        deadline = time.monotonic() + self.timeout
        response_ref: list[httpx.Response] = []
        expired = threading.Event()
        started = time.monotonic()

        def cancel():
            expired.set()
            if response_ref:
                response_ref[0].close()

        timer = threading.Timer(self.timeout, cancel)
        timer.daemon = True
        timer.start()
        n_answer = n_thought = 0
        last_emitted_usage: tuple[Any, ...] | None = None
        try:
            with (directory / "stream.jsonl").open("w", encoding="utf-8") as events, \
                 (directory / "answer.partial.txt").open("w", encoding="utf-8") as visible, \
                 (directory / "reasoning.partial.txt").open("w", encoding="utf-8") as reasoning:
                with self.http.stream("POST", "v1/chat/completions", json=body) as response:
                    response_ref.append(response)
                    if not response.is_success:
                        response.read()
                        if recorder:
                            recorder.event("inbound", "http_error", {
                                "status": response.status_code,
                                "headers": {k: v for k, v in response.headers.items()
                                            if k.lower() in {"x-request-id", "retry-after"}},
                                "body": response.text[:1500],
                            })
                        raise PipelineError(f"Completion HTTP {response.status_code}: {response.text[:1500]}")
                    buffer = []
                    for line in response.iter_lines():
                        if expired.is_set() or time.monotonic() > deadline:
                            raise PipelineError("Request time limit reached; partial output saved, stage remains incomplete.")
                        if line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            buffer.append(line[5:].lstrip())
                            continue
                        if line or not buffer:
                            continue
                        payload, buffer = "\n".join(buffer), []
                        if payload == "[DONE]":
                            break
                        if recorder:
                            recorder.event("inbound", "sse_frame", payload)
                        event = json.loads(payload)
                        if "error" in event:
                            raise PipelineError(f"Server stream error: {event['error']}")
                        events.write(dumps(event) + "\n")
                        events.flush()
                        info["events"] += 1
                        for field in ("usage", "timings", "id"):
                            if event.get(field) is not None:
                                info[field] = event[field]
                        if isinstance(event.get("model"), str):
                            info["reported_model"] = event["model"]
                        event_usage = event.get("usage")
                        if isinstance(event_usage, dict) and event_usage:
                            details = event_usage.get("prompt_tokens_details") or {}
                            output_details = event_usage.get("completion_tokens_details") or {}
                            signature = tuple(event_usage.get(key) for key in (
                                "prompt_tokens", "completion_tokens", "total_tokens",
                            )) + (details.get("cached_tokens"), details.get("cache_write_tokens"),
                                  output_details.get("reasoning_tokens"))
                            if signature != last_emitted_usage:
                                last_emitted_usage = signature
                                context = dict(recorder.progress_values) if recorder else {}
                                self.ui.emit(ProgressEvent(kind="provider_usage_update", values={
                                    **context,
                                    "input_tokens": event_usage.get("prompt_tokens"),
                                    "cached_input_tokens": details.get("cached_tokens"),
                                    "cache_write_input_tokens": details.get("cache_write_tokens"),
                                    "output_tokens": event_usage.get("completion_tokens"),
                                    "reasoning_output_tokens": output_details.get("reasoning_tokens"),
                                    "total_tokens": event_usage.get("total_tokens"),
                                    "source": f"{self.provider}_sse_usage", "status": "reported",
                                    "cumulative": True,
                                }))
                        for choice in event.get("choices", []):
                            if choice.get("index", 0) != 0:
                                continue
                            if choice.get("finish_reason") is not None:
                                finish = choice["finish_reason"]
                            delta = choice.get("delta") or choice.get("message") or {}
                            for key, parts, file in (("content", answer, visible),
                                                     ("reasoning_content", thought, reasoning),
                                                     ("reasoning", thought, reasoning)):
                                text = delta.get(key) or ""
                                if not isinstance(text, str):
                                    raise PipelineError(f"Unsupported streamed {key} format.")
                                if text:
                                    parts.append(text)
                                    file.write(text)
                                    file.flush()
                                    if key == "content":
                                        n_answer += len(text)
                                    else:
                                        n_thought += len(text)
                            self.ui.emit(ProgressEvent(kind="generation_progress", values={
                                **(dict(recorder.progress_values) if recorder else {}),
                                "answer_chars": n_answer, "reasoning_chars": n_thought,
                            }))
            usage = info.get("usage") or {}
            if recorder:
                normalized = normalized_usage(
                    input_tokens=usage.get("prompt_tokens"),
                    cached_input_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    reasoning_output_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    source=f"{self.provider}_sse_usage", scope="attempt",
                    status="reported" if usage else "unavailable",
                )
                recorder.usage([usage] if usage else [], normalized)
            if finish != "stop":
                raise PipelineError(f"Incomplete completion (finish_reason={finish!r}). Saved partial files; not a checkpoint.")
            raw = "".join(answer).strip()
            if not raw:
                raise PipelineError("Model returned no final answer. Reasoning was saved separately.")
            # Older templates may keep thought text in content. Never accept a
            # trailing incomplete thought as final JSON.
            if "<think>" in raw:
                if "</think>" not in raw:
                    raise PipelineError("Unclosed reasoning block; no final answer accepted.")
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
            atomic_text(directory / "answer.txt", raw + "\n")
            atomic_text(directory / "reasoning.txt", "".join(thought))
            info.update(finish_reason=finish, elapsed_seconds=round(time.monotonic() - started, 3))
            info["usage_status"] = "reported" if info.get("usage") else "unavailable"
            atomic_json(directory / "response_meta.json", info)
            if recorder:
                recorder.write_answer(raw, "".join(thought))
            return raw, info
        except (httpx.HTTPError, json.JSONDecodeError, OSError) as exc:
            raise PipelineError(f"Stream interrupted: {exc}. This stage was not completed.") from exc
        finally:
            timer.cancel()
            if response_ref:
                response_ref[0].close()
