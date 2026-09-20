from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class PipelineError(Exception):
    """An actionable error that must not silently invalidate completed work."""


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else (value if isinstance(value, str) else dumps(value)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip().casefold()


def occurs(text: str, phrase: str) -> bool:
    phrase = normalized(phrase)
    if not phrase:
        return False
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized(text)) is not None


def unique(items: list[Any]) -> list[Any]:
    result, seen = [], set()
    for item in items:
        key = dumps(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def natural_key(value: str) -> list[Any]:
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r"(\d+)", value)]


def inside(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise PipelineError(f"Path escapes input directory: {path}")
    return path


@contextmanager
def file_lock(root: Path, name: str, conflict_message: str) -> Iterator[None]:
    """Exclusive named lock released by the kernel after crashes."""
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineError(conflict_message) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def project_lock(root: Path) -> Iterator[None]:
    with file_lock(root, ".lock", "Another process is using this project."):
        yield


@contextmanager
def reader_lock(root: Path) -> Iterator[None]:
    with file_lock(root, ".reader.lock", "Another Reader process is using this project's marker file."):
        yield


def plan_fingerprint(book: dict) -> str:
    """Source/chunk structure is immutable; display titles and thread tags may vary."""
    return digest({
        "files": book["files"],
        "chapters": [{k: c[k] for k in ("id", "number", "source_file", "blocks", "pieces", "chunk_ids")} for c in book["chapters"]],
        "chunks": book["chunks"],
    })
