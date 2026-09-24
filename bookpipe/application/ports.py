"""Small capabilities consumed by application services."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..progress import ProgressEvent


class ProgressSink(Protocol):
    def emit(self, event: ProgressEvent) -> None: ...


class NullProgress:
    def emit(self, event: ProgressEvent) -> None:
        return


class Closeable(Protocol):
    def close(self) -> None: ...


class ProjectFiles(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def read_json(self, path: Path) -> Any: ...
    def write_json(self, path: Path, value: Any) -> None: ...
    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str: ...
    def read_bytes(self, path: Path) -> bytes: ...
    def copy_prompts(self, source: Path, destination: Path) -> None: ...
    def write_bytes(self, path: Path, raw: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class PublicationBlock:
    """Detached source-to-translation binding consumed by a publisher adapter."""

    id: str
    source_file: str
    source_ordinal: int
    source_text: str
    translated_text: str


@dataclass(frozen=True, slots=True)
class PublicationSourceFile:
    path: str
    sha256: str
    encoding: str


@dataclass(frozen=True, slots=True)
class PublicationSourceInfo:
    package_fingerprint: str
    title: str
    creators: tuple[str, ...]
    source_language: str | None


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    source_root: Path
    package_document: str
    output_path: Path
    source_fingerprint: str
    package_fingerprint: str
    target_language: str
    title: str
    source_files: tuple[PublicationSourceFile, ...]
    blocks: tuple[PublicationBlock, ...]
    excluded_source_files: tuple[str, ...] = ()
    format_version: int = 1


@dataclass(frozen=True, slots=True)
class PublicationBuildResult:
    output_path: Path
    title: str
    creators: tuple[str, ...]
    source_language: str | None
    target_language: str
    validation: tuple[str, ...]
    generated_at: str


class PublicationBuilder(Protocol):
    def inspect(self, source_root: Path, package_document: str,
                source_files: tuple[PublicationSourceFile, ...]) -> PublicationSourceInfo: ...
    def build(self, request: PublicationRequest) -> PublicationBuildResult: ...


LockFactory = Callable[[Path], AbstractContextManager[None]]
StoreFactory = Callable[[Path], Any]
ProviderFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    project_lock: LockFactory
    reader_lock: LockFactory
    store_factory: StoreFactory
    provider_factory: ProviderFactory
    bundle: Path
    plan_fingerprint: Callable[[dict], str]
    files: ProjectFiles
    publication_builder: PublicationBuilder | None = None
    read_store_factory: StoreFactory | None = None
