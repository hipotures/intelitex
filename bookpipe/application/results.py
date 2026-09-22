"""Detached results returned by application operations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..usage import (
    AttemptUsage, CostEstimate, PassUsage, PreflightInput, UnitUsage, UsageAggregate,
    UsageByUnitResult,
)


@dataclass(frozen=True, slots=True)
class ImportResult:
    project: Path
    narrative_sections: int
    translation_units: int
    excluded_sections: int
    warnings: tuple[str, ...]
    series_id: str | None = None
    series_volume: int | None = None
    inherited_terms: int = 0
    inherited_observations: int = 0


@dataclass(frozen=True, slots=True)
class ChunkStatus:
    id: str
    status: str


@dataclass(frozen=True, slots=True)
class ChapterStatus:
    id: str
    title: str
    completed: int
    total: int


@dataclass(frozen=True, slots=True)
class StatusResult:
    project: Path
    title: str
    series_id: str | None
    series_volume: int | None
    narrative_sections: int
    chunks: tuple[ChunkStatus, ...]
    analysis_complete: bool
    approved: bool
    term_count: int
    retained_candidates: int
    chapters: tuple[ChapterStatus, ...]
    readable_output: Path
    translation_complete: bool = False
    publication: PublicationStatus | None = None


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    approved_terms: int
    stale_chunks: int


@dataclass(frozen=True, slots=True)
class ExportResult:
    internal_output: Path
    copy_output: Path | None
    encoding: str


@dataclass(frozen=True, slots=True)
class PublicationStatus:
    state: str
    translation_complete: bool
    current: bool
    output_path: Path | None
    target_language: str
    publication_fingerprint: str | None = None
    generated_by: str | None = None
    title: str | None = None
    creators: tuple[str, ...] = ()
    source_language: str | None = None
    generated_at: str | None = None
    last_error: str | None = None
    last_failure: str | None = None
    validation: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishResult:
    project: Path
    status: PublicationStatus
    built: bool


@dataclass(frozen=True, slots=True)
class PipelineResult:
    project: Path
    completed_units: int = 0
    review_path: Path | None = None
    publication: PublicationStatus | None = None


@dataclass(frozen=True, slots=True)
class ReportResult:
    """A defensively detached existing JSON report."""

    value: dict[str, Any]
