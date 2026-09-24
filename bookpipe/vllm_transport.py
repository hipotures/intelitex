from __future__ import annotations

from typing import Any

import httpx

from .client import Client, ContextFull
from .evidence import AttemptRecorder
from .util import PipelineError, dumps


class VLLMClient(Client):
    """vLLM Chat Completions transport with its native chat tokenizer."""

    provider = "vllm"
    preflight_input_unit = "tokens"
    preflight_input_quality = "provider_exact"
    preflight_input_method = "vLLM POST /tokenize with chat messages"

    def __init__(self, profile: dict[str, Any], ui: Any, settings: dict[str, Any]):
        endpoint = profile.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise PipelineError("vLLM profile requires an HTTP(S) endpoint.")
        self.profile = profile
        self.passes = settings["passes"]
        self.diffusion = profile.get("options", {}).get("diffusion", profile.get("model") == "diffusiongemma")
        super().__init__({
            "host": endpoint,
            "model": profile["model"],
            "context_size": profile.get("context_size"),
            "request_timeout": profile.get("request_timeout", settings.get("request_timeout", 1200)),
            "credential_env": profile.get("credential_env") or "",
            "profile_name": profile["profile_name"],
            "resolved_profile": profile["resolved_profile"],
        }, ui)

    def discover(self) -> dict[str, Any]:
        try:
            response = self.http.get("v1/models")
            response.raise_for_status()
            entries = response.json().get("data", [])
            model = next((row for row in entries if isinstance(row, dict) and row.get("id") == self.model), None)
            if model is None:
                raise PipelineError(f"Requested vLLM model {self.model!r} was not returned by /v1/models.")
            capacity = model.get("max_model_len")
            if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0:
                self.context = min(self.context, capacity) if self.context else capacity
            if not self.context:
                raise PipelineError("vLLM did not report max_model_len; configure context_size in the profile.")
            self.identity = {"provider": self.provider, "id": self.model, "max_model_len": capacity}
            return self.identity
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise PipelineError(f"Cannot discover vLLM at {self.base}: {exc}") from exc

    def _tokenize(self, body: dict[str, Any], recorder: AttemptRecorder | None = None) -> int:
        if recorder:
            recorder.event("outbound", "http_request", {"method": "POST", "path": "/tokenize", "body": body})
        response = self.post("/tokenize", body)
        if recorder:
            recorder.event("inbound", "http_response", {"path": "/tokenize", "body": {
                "count": response.get("count"), "max_model_len": response.get("max_model_len"),
            }})
        count = response.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise PipelineError("vLLM /tokenize omitted a nonnegative integer count.")
        capacity = response.get("max_model_len")
        if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0:
            self.context = min(self.context, capacity) if self.context else capacity
        return count

    def count(self, text: str, recorder: AttemptRecorder | None = None) -> int:
        return self._tokenize({"model": self.model, "prompt": text, "add_special_tokens": False}, recorder)

    def body(self, prompt: str, inputs: dict, schema: dict, pass_no: int) -> dict[str, Any]:
        cfg = self.passes[str(pass_no)]
        cap = self.profile.get("max_output_tokens") or cfg["max_tokens"]
        # vLLM's diffusion sampler cannot use guided decoding. Keep the
        # canonical schema in the prompt; Runner still validates the answer.
        instructions = prompt + ("\n\nReturn only JSON matching this schema:\n" + dumps(schema) if self.diffusion else "")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": dumps(inputs)}],
            "max_tokens": int(cap),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if not self.diffusion:
            body["temperature"] = (self.profile.get("temperature") if self.profile.get("temperature") is not None
                                   else cfg["temperature"])
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": f"intelitex_pass_{pass_no}", "strict": True, "schema": schema,
            }}
        if self.profile.get("seed") is not None:
            body["seed"] = int(self.profile["seed"])
        extra = self.profile.get("options", {}).get("request_extra", {})
        if not isinstance(extra, dict):
            raise PipelineError("vLLM options.request_extra must be an object.")
        for key, value in extra.items():
            if key in body or key in {"tools", "tool_choice", "n", "best_of", "max_completion_tokens",
                                      "truncate_prompt_tokens", "chat_template_kwargs", "structured_outputs",
                                      "guided_json", "guided_regex", "guided_choice", "guided_grammar"} or (
                self.diffusion and key in {"temperature", "seed", "min_p", "min_tokens", "logit_bias",
                                           "bad_words", "allowed_token_ids", "response_format"}
            ):
                raise PipelineError(f"vLLM request_extra cannot override reserved key {key!r}.")
            body[key] = value
        return body

    def preflight(self, body: dict, recorder: AttemptRecorder | None = None) -> int:
        count = self._tokenize({"model": self.model, "messages": body["messages"]}, recorder)
        reserve = body["max_tokens"]
        required = count + reserve + 1024
        if not self.context:
            raise PipelineError("vLLM context capacity is unknown; configure context_size or expose max_model_len.")
        if recorder:
            recorder.context({
                "method": self.preflight_input_method,
                "quality": self.preflight_input_quality,
                "tokenizer_identity": {"provider": self.provider, "model": self.model},
                "input_tokens": count,
                "capacity_tokens": self.context,
                "planning_output_reserve": reserve,
                "enforced_output_cap": reserve,
                "safety_margin": 1024,
                "required_tokens": required,
                "silent_truncation": False,
            })
        if required > self.context:
            raise ContextFull(
                f"vLLM request needs {required:,} tokens including output reserve and margin; "
                f"server permits {self.context:,}. No source text was truncated."
            )
        return count
