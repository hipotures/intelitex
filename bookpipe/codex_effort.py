"""Codex-owned effort updates; no prompt, session, or provider request construction."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import PipelineError, digest


# These are Codex's direct backend modes, not a list of supported model names.
# Aliases/custom modes keep the existing request-level normalization in Codex.
UPDATE_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max", "disabled", "persistent"})


@dataclass(frozen=True)
class EffortPlan:
    supported: bool
    mode: str
    baseline: str | None
    requested: str | None
    feature_available: bool
    reason: str

    def thread_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"model_reasoning_effort": self.baseline}
        if self.feature_available:
            # A session-only standard config override, never a user setting.
            config["features.reasoning_effort_override"] = self.mode == "configuration_update"
        return config

    def metadata(self) -> dict[str, Any]:
        return {
            "requested_effort": self.requested,
            "requested_effective_effort": self.requested,
            "effective_effort_requested": self.requested,
            "reasoning_effort_update_supported": self.supported,
            "reasoning_effort_update_mode": self.mode,
            "request_effort_baseline": self.baseline,
            "reasoning_effort_update_reason": self.reason,
            # Intent is not proof of a generated item or a provider request.
            "configuration_update_observed": None,
            "configuration_update_effort": None,
            "provider_request_effort": None,
        }


def resolve_effort_plan(requested: str | None, metadata: dict | None, *, feature_available: bool) -> EffortPlan:
    """Use only the selected runtime model's advertised capability and default."""
    supported = bool(feature_available and metadata and metadata.get("supports_reasoning_effort_updates") is True)
    fallback = EffortPlan(supported, "request_level", requested, requested, feature_available,
                          "model capability or installed override feature unavailable")
    if not supported:
        return fallback
    baseline = metadata.get("default_reasoning_level")
    levels = metadata.get("supported_reasoning_levels")
    advertised = {entry["effort"] for entry in levels
                  if isinstance(entry, dict) and isinstance(entry.get("effort"), str)} if isinstance(levels, list) else set()
    if not isinstance(baseline, str) or baseline not in UPDATE_EFFORTS or baseline not in advertised:
        return EffortPlan(supported, "request_level", requested, requested, feature_available,
                          "no trustworthy direct model-default effort")
    if requested is not None and (not isinstance(requested, str) or requested not in UPDATE_EFFORTS or requested not in advertised):
        # Codex/profile validation remains authoritative for aliases/custom values.
        # Never clamp an unknown selection to the baseline.
        return EffortPlan(supported, "request_level", requested, requested, feature_available,
                          "requested effort requires ordinary Codex normalization/validation")
    return EffortPlan(supported, "configuration_update", baseline, requested, feature_available,
                      "installed model catalog advertises updates and a stable default")


def runtime_model_metadata(home: Path, model: str, cli_version: str | None, listed: dict | None) -> tuple[dict | None, dict]:
    """Read the catalog just written by this private app-server, never global state.

    model/list currently drops the update flag. Cross-check its picker metadata
    against the full version-scoped ModelsCacheEntry from the same process.
    Absent/malformed/conflicting data is an optimization miss, not permission to
    infer capabilities from a model name or IntelliTex's pricing catalog.
    """
    evidence: dict[str, Any] = {"source": "private CODEX_HOME/models_cache.json", "trusted": False}
    path = home / "models_cache.json"
    try:
        if not path.resolve().is_relative_to(home.resolve()):
            return None, dict(evidence, reason="catalog path outside private home")
        raw = path.read_bytes()
        catalog = json.loads(raw)
        version = cli_version.removeprefix("codex-cli ").strip() if isinstance(cli_version, str) else None
        if not isinstance(catalog, dict) or not version or catalog.get("client_version") != version:
            return None, dict(evidence, reason="missing/mismatched CLI catalog version")
        models = catalog.get("models")
        if not isinstance(models, list) or not isinstance(listed, dict) or model not in (listed.get("id"), listed.get("model")):
            return None, dict(evidence, reason="selected model not advertised by this process")
        matches = [m for m in models if isinstance(m, dict) and m.get("slug") == model]
        if len(matches) != 1:
            return None, dict(evidence, reason="missing/ambiguous raw model entry")
        model_data = matches[0]
        raw_levels = model_data.get("supported_reasoning_levels")
        picker_levels = listed.get("supportedReasoningEfforts")
        if not isinstance(raw_levels, list) or not isinstance(picker_levels, list):
            return None, dict(evidence, reason="missing reasoning presets")
        if model_data.get("default_reasoning_level") != listed.get("defaultReasoningEffort") or (
            [e.get("effort") for e in raw_levels if isinstance(e, dict)] !=
            [e.get("reasoningEffort") for e in picker_levels if isinstance(e, dict)]
        ):
            return None, dict(evidence, reason="raw catalog/picker disagreement")
        selected = {key: model_data.get(key) for key in (
            "slug", "supports_reasoning_effort_updates", "default_reasoning_level", "supported_reasoning_levels",
        )}
        return selected, dict(evidence, trusted=True, cli_version=version, catalog_sha256=digest(raw), model=selected)
    except (OSError, ValueError, TypeError):
        return None, dict(evidence, reason="catalog unavailable or malformed")


def rollout_effort_evidence(path: Path, turn_id: str) -> dict[str, Any]:
    """Observe harness rollout items, never inspect JSON inside user/model text."""
    result: dict[str, Any] = {"configuration_update_observed": None, "configuration_update_effort": None,
                              "provider_request_effort": None, "rollout_model": None, "rollout_effort": None}
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    complete, session_seen, updates = bool(files), False, []
    for file in files:
        try:
            with file.open(encoding="utf-8") as stream:
                for line in stream:
                    event = json.loads(line)
                    payload = event.get("payload", {})
                    if not isinstance(payload, dict):
                        continue
                    if event.get("type") == "session_meta":
                        session_seen = True
                    elif event.get("type") == "turn_context" and payload.get("turn_id") == turn_id:
                        result.update(rollout_model=payload.get("model"), rollout_effort=payload.get("effort"))
                    elif event.get("type") == "response_item" and payload.get("type") == "configuration_update":
                        updates.append((payload.get("reasoning") or {}).get("effort"))
        except (OSError, ValueError, AttributeError, TypeError):
            complete = False
    if updates:
        result.update(configuration_update_observed=True, configuration_update_effort=updates[-1])
    elif complete and session_seen:
        result["configuration_update_observed"] = False
    return result


def verify_reported_selection(model: str, requested: str | None, reported_model: str | None, reported_effort: str | None) -> None:
    if reported_model is not None and reported_model != model:
        raise PipelineError(f"Codex reported model {reported_model!r}, requested {model!r}; result rejected.")
    if requested is not None and reported_effort is not None and reported_effort != requested:
        raise PipelineError(f"Codex reported effective effort {reported_effort!r}, requested {requested!r}; result rejected.")
