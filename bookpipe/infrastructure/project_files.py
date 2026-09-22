"""Filesystem capability preserving Intelitex paths and atomic byte semantics."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ..util import atomic_json, read_json


class LocalProjectFiles:
    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def read_json(self, path: Path) -> Any:
        return read_json(path)

    def write_json(self, path: Path, value: Any) -> None:
        atomic_json(path, value)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def copy_prompts(self, source: Path, destination: Path) -> None:
        destination.mkdir(exist_ok=True)
        for prompt in source.glob("*.txt"):
            shutil.copy2(prompt, destination / prompt.name)

    def write_bytes(self, path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            name = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
