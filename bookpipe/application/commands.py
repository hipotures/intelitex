"""Explicit application operation inputs.

The CLI and HTTP adapters translate their own syntax into these values.  Keeping
the omitted values as ``None`` is significant for configuration inheritance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelOptions:
    host: str | None = None
    port: int | None = None
    model: str | None = None
    context_size: int | None = None
    thinking: str | None = None
    allow_model_change: bool = False
    profile: str | None = None
    pass_profiles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportBookCommand(ModelOptions):
    project: Path
    source: Path
    previous_volume: Path | None = None
    opf: Path | None = None
    input_encoding: str | None = None
    chapter_mode: str = "auto"
    chapter_selector: str | None = None
    include_glob: str | None = None
    sidecar_txt: bool = False
    whole_section_limit: int | None = None


@dataclass(frozen=True, slots=True)
class AnalyzeCommand(ModelOptions):
    project: Path


@dataclass(frozen=True, slots=True)
class TranslateCommand(ModelOptions):
    project: Path
    chunk_limit: int = 5


@dataclass(frozen=True, slots=True)
class ReviewSessionCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class ReaderSessionCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class ApproveCommand:
    project: Path
    accept_defaults: bool = False


@dataclass(frozen=True, slots=True)
class StatusCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class ExportCommand:
    project: Path
    encoding: str = "utf-8"
    output: Path | None = None


@dataclass(frozen=True, slots=True)
class ProfilesCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class DoctorCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class AttemptsCommand:
    project: Path
    attempt: str | None = None


@dataclass(frozen=True, slots=True)
class UsageCommand:
    project: Path


@dataclass(frozen=True, slots=True)
class CatalogImportCommand:
    project: Path
    source: Path


@dataclass(frozen=True, slots=True)
class DiscoverCommand:
    project: Path
    profile: str | None = None
    pass_no: int = 1


@dataclass(frozen=True, slots=True)
class SmokeCommand:
    project: Path
    live: bool = False
    profile: str | None = None
    pass_no: int = 1
