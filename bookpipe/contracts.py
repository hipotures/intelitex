from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticRequest:
    """Provider-neutral description of one physical inference attempt."""

    task_key: str
    task_fingerprint: str
    pass_no: int
    attempt_no: int
    trusted_instructions: str
    input_payload: dict[str, Any]
    output_schema: dict[str, Any]
    schema_version: int
    profile: str
    provider: str
    requested_model: str | None
    reasoning_effort: str | None
    planning_output_reserve: int
    enforced_output_cap: int | None
    timeout_seconds: float
    resolved_profile: dict[str, Any]
    retry_additions: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def preflight_measurement(provider: Any, value: int) -> dict[str, Any]:
    """Describe a provider's pre-submission size measurement without lying about units."""
    unit = getattr(provider, "preflight_input_unit", "tokens")
    if unit not in {"tokens", "utf8_bytes"}:
        raise ValueError(f"Unsupported preflight input unit: {unit!r}")
    return {
        "value": value,
        "unit": unit,
        "quality": getattr(provider, "preflight_input_quality", "provider_or_tokenizer_count"),
        "method": getattr(provider, "preflight_input_method", "provider preflight"),
    }


def preflight_display(measurement: dict[str, Any]) -> str:
    value = int(measurement["value"])
    if measurement["unit"] == "utf8_bytes":
        return f"input upper bound {value:,} UTF-8 bytes (not tokens)"
    return f"input {value:,} tokens"


def preflight_metadata(measurement: dict[str, Any]) -> dict[str, Any]:
    result = {"input_preflight": measurement}
    # Preserve the established field only when its name is truthful. Old Codex
    # attempts may contain it, but new byte estimates must not masquerade as tokens.
    if measurement["unit"] == "tokens":
        result["input_tokens_preflight"] = measurement["value"]
    return result


def normalized_usage(
    *,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_write_input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
    total_tokens: int | None = None,
    source: str,
    scope: str,
    status: str = "reported",
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
        "source": source,
        "scope": scope,
        "status": status,
        "note": "Cached input and reasoning output may overlap parent categories; total is never recomputed by summing displayed fields.",
    }
