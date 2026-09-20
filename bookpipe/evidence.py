from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import PipelineError, atomic_json, atomic_text


ARTIFACT_FORMAT_VERSION = 2
_SECRET_KEYS = {
    "authorization", "proxy-authorization", "api_key", "apikey", "access_token",
    "refresh_token", "id_token", "cookie", "set-cookie", "password", "secret",
    "client_secret", "auth", "auth_json",
}
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+\-/=]+")
_OPENAI_KEY = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def redact(value: Any, *, key: str | None = None) -> Any:
    """Remove credentials while retaining ordinary prompts and source payloads."""
    if key and key.casefold() in _SECRET_KEYS:
        return "[REDACTED credential]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER.sub(r"\1[REDACTED]", value)
        return _OPENAI_KEY.sub("[REDACTED OpenAI key]", value)
    return value


class EvidenceError(PipelineError):
    pass


class AttemptRecorder:
    """Crash-tolerant, append-first communication evidence for one attempt."""

    def __init__(self, directory: Path, identity: dict[str, Any]):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        self.started_monotonic = time.monotonic()
        self.order = 0
        self.identity = redact(identity)
        self.manifest: dict[str, Any] = {
            "format_version": ARTIFACT_FORMAT_VERSION,
            "identity": self.identity,
            "lifecycle": {
                "created_at": utc_now(),
                "generation": "not_submitted",
                "validation": "not_run",
                "evidence": "recording",
                "acceptance": "not_accepted",
            },
            "evidence_complete": False,
            "artifacts": [],
        }
        self._write_json("attempt.json", self.manifest)
        # Prove that append recording is writable before any provider contact.
        try:
            with self._open_append("transport.jsonl") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise EvidenceError(f"Cannot create attempt evidence: {exc}") from exc

    def _path(self, name: str) -> Path:
        path = self.directory / name
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise EvidenceError(f"Evidence path escapes attempt directory: {name}")
        return path

    def _secure(self, path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _write_json(self, name: str, value: Any) -> None:
        try:
            path = self._path(name)
            atomic_json(path, redact(value))
            self._secure(path)
        except OSError as exc:
            raise EvidenceError(f"Cannot write evidence {name}: {exc}") from exc

    def _write_text(self, name: str, value: str) -> None:
        try:
            path = self._path(name)
            atomic_text(path, redact(value))
            self._secure(path)
        except OSError as exc:
            raise EvidenceError(f"Cannot write evidence {name}: {exc}") from exc

    def _open_append(self, name: str):
        path = self._path(name)
        handle = path.open("a", encoding="utf-8", newline="\n")
        self._secure(path)
        return handle

    def _remember(self, *names: str) -> None:
        known = set(self.manifest["artifacts"])
        for name in names:
            if self._path(name).exists() and name not in known:
                self.manifest["artifacts"].append(name)
                known.add(name)

    def semantic(self, request: dict[str, Any], canonical_schema: dict[str, Any]) -> None:
        self._write_json("request.semantic.json", request)
        self._write_json("schema.canonical.json", canonical_schema)
        self._remember("request.semantic.json", "schema.canonical.json")

    def transport_request(self, request: dict[str, Any], transport_schema: dict[str, Any]) -> None:
        self._write_json("request.transport.json", request)
        # Keep the v1 filename for narrow recovery compatibility.
        self._write_json("request.json", request)
        self._write_json("schema.transport.json", transport_schema)
        self._remember("request.transport.json", "request.json", "schema.transport.json")
        self.event("outbound", "request", request)

    def decoded_canonical(self, value: dict[str, Any]) -> None:
        self._write_json("decoded.canonical.json", value)
        self._remember("decoded.canonical.json")

    def context(self, value: dict[str, Any]) -> None:
        self._write_json("context.json", value)
        self._remember("context.json")

    def event(
        self,
        direction: str,
        kind: str,
        payload: Any,
        correlation: dict[str, Any] | None = None,
    ) -> None:
        self.order += 1
        row = {
            "order": self.order,
            "timestamp": utc_now(),
            "monotonic_ms": round((time.monotonic() - self.started_monotonic) * 1000, 3),
            "direction": direction,
            "kind": kind,
            "correlation": correlation or {},
            "payload": redact(payload),
        }
        try:
            with self._open_append("transport.jsonl") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise EvidenceError(f"Cannot append transport evidence: {exc}") from exc
        self._remember("transport.jsonl")

    def append_text(self, name: str, text: str) -> None:
        try:
            with self._open_append(name) as handle:
                handle.write(redact(text))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise EvidenceError(f"Cannot append evidence {name}: {exc}") from exc
        self._remember(name)

    def usage(self, raw_events: list[Any], normalized: dict[str, Any]) -> None:
        self._write_json("usage.raw.json", raw_events)
        self._write_json("usage.json", normalized)
        self._remember("usage.raw.json", "usage.json")

    def pricing(self, snapshot: dict[str, Any]) -> None:
        self._write_json("pricing.json", snapshot)
        self._remember("pricing.json")

    def finish(
        self,
        *,
        generation: str,
        validation: str,
        metadata: dict[str, Any],
        error: dict[str, Any] | None = None,
        evidence_complete: bool = True,
    ) -> None:
        self._write_json("response_meta.json", metadata)
        self._remember("response_meta.json", "answer.partial.txt", "answer.txt",
                       "reasoning.partial.txt", "reasoning.txt", "context.json")
        if error is not None:
            self._write_json("error.json", error)
            self._remember("error.json")
        self.manifest["lifecycle"].update({
            "terminal_at": utc_now(),
            "generation": generation,
            "validation": validation,
            "evidence": "complete" if evidence_complete else "incomplete",
        })
        self.manifest["evidence_complete"] = evidence_complete
        self.manifest["response"] = redact(metadata)
        self._write_json("attempt.json", self.manifest)

    def mark_accepted(self) -> None:
        self.manifest["lifecycle"]["acceptance"] = "checkpointed"
        self.manifest["lifecycle"]["accepted_at"] = utc_now()
        self._write_json("attempt.json", self.manifest)

    def write_answer(self, text: str, reasoning: str = "") -> None:
        self._write_text("answer.txt", text.rstrip() + "\n")
        self._write_text("reasoning.txt", reasoning)
        self._remember("answer.txt", "reasoning.txt")
