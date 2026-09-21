"""Small capabilities consumed by application services."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: str
    message: str = ""
    current: int | None = None
    total: int | None = None
    values: Mapping[str, str | int | bool | None] = field(default_factory=dict)


class ProgressSink(Protocol):
    def emit(self, event: ProgressEvent) -> None: ...

    # Compatibility surface for the current engine while its human labels stay
    # presentation output rather than persisted state.
    def overall(self, label: str, completed: int, total: int) -> None: ...
    def chapter(self, label: str, completed: int, total: int) -> None: ...
    def phase(self, text: str) -> None: ...
    def message(self, text: str) -> None: ...
    def received(self, answer_chars: int, reasoning_chars: int) -> None: ...
    def stop_progress(self) -> None: ...


class NullProgress:
    def emit(self, event: ProgressEvent) -> None:
        return

    def overall(self, label: str, completed: int, total: int) -> None:
        return

    def chapter(self, label: str, completed: int, total: int) -> None:
        return

    def phase(self, text: str) -> None:
        return

    def message(self, text: str) -> None:
        return

    def received(self, answer_chars: int, reasoning_chars: int) -> None:
        return

    def stop_progress(self) -> None:
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
