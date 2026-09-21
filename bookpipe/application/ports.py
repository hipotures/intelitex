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
    def copy_prompts(self, source: Path, destination: Path) -> None: ...
    def write_bytes(self, path: Path, raw: bytes) -> None: ...


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
