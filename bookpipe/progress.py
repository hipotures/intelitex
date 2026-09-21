"""Presentation-neutral progress values shared by application and runtime code."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: str
    message: str = ""
    current: int | None = None
    total: int | None = None
    values: Mapping[str, Any] = field(default_factory=dict)
