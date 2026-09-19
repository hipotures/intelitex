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
