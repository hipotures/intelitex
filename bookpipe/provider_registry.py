from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import Client
from .codex_transport import CodexAppServerClient
from .openai_transport import OpenAIResponsesClient
from .profiles import resolve_profile
from .ui import Display
from .util import PipelineError, digest


class ProviderPool:
    """Resolve profile precedence and reuse only transport processes/clients, never conversations."""

    def __init__(
        self,
        settings: dict[str, Any],
        ui: Display,
        project: Path,
        *,
        command_profile: str | None = None,
        command_pass_profiles: dict[int, str] | None = None,
    ):
        self.settings = settings
        self.ui = ui
        self.project = project
        self.command_profile = command_profile
        self.command_pass_profiles = command_pass_profiles or {}
        self.clients: dict[str, Any] = {}
        self.pass_clients: dict[int, Any] = {}
        self.identity: dict[str, Any] = {}
        self.planning_pass = 1

    def _llama_settings(self, profile: dict[str, Any], name: str) -> dict[str, Any]:
        options = profile.get("options", {})
        value = dict(self.settings)
        value.update({
            "host": profile.get("endpoint") or self.settings.get("host", "127.0.0.1"),
            "port": options.get("port", self.settings.get("port", 8080)),
            "model": profile.get("model"),
            "context_size": profile.get("context_size"),
            "request_timeout": profile.get("request_timeout", self.settings.get("request_timeout", 1200)),
            "credential_env": profile.get("credential_env", "LLAMA_API_KEY"),
            "thinking": options.get("thinking", self.settings.get("thinking", "off")),
            "seed": profile.get("seed", self.settings.get("seed", 42)),
            "request_extra": options.get("request_extra", self.settings.get("request_extra", {})),
            "profile_name": name,
            "resolved_profile": profile,
        })
        return value

    def for_pass(self, pass_no: int):
        if pass_no in self.pass_clients:
            return self.pass_clients[pass_no]
        name, profile, provenance = resolve_profile(
            self.settings, pass_no, command_profile=self.command_profile,
            command_pass_profiles=self.command_pass_profiles, project=self.project,
        )
        profile.update({"profile_name": name, "resolved_profile": {**profile, "selection_provenance": provenance},
                        "project_root": str(self.project)})
        key = digest({k: v for k, v in profile.items() if k != "profile_name"})
        client = self.clients.get(key)
        if client is None:
            provider = profile["provider"]
            if provider == "llamacpp":
                client = Client(self._llama_settings(profile, name), self.ui)
            elif provider == "openai":
                client = OpenAIResponsesClient(profile, self.ui)
            elif provider == "codex":
                client = CodexAppServerClient(profile, self.ui)
            else:
                raise PipelineError(f"Unknown provider: {provider}")
            self.clients[key] = client
        self.pass_clients[pass_no] = client
        return client

    def discover(self, pass_no: int = 1) -> dict[str, Any]:
        client = self.for_pass(pass_no)
        self.identity = client.discover()
        return self.identity

    @property
    def context(self) -> int:
        return self.for_pass(self.planning_pass).context

    def count(self, text: str) -> int:
        return self.for_pass(self.planning_pass).count(text)

    @property
    def tokenizer_identity(self) -> dict[str, Any]:
        return self.for_pass(self.planning_pass).tokenizer_identity

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
