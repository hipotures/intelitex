"""Filesystem capability preserving Intelitex paths and atomic byte semantics."""
from __future__ import annotations

import os
import shutil
import copy
import threading
from collections import OrderedDict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ..util import atomic_json, read_json


class LocalProjectFiles:
    def __init__(self):
        self._books = OrderedDict()
        self._books_lock = threading.Lock()

    def validated_book(self, path, fingerprint, *, readonly=False):
        """Bounded immutable-source snapshot, invalidated by replacement or edit.

        Mutable application callers receive their own copy. Only explicitly
        read-only projections borrow the parsed manifest; no workflow state is cached.
        """
        from ..util import PipelineError
        with self._books_lock:
            stat = path.stat()
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            key = (path, fingerprint)
            cached = self._books.get(key)
            if cached is None or cached[0] != stamp:
                book = self.read_json(path)
                if book.get('content_fingerprint') != fingerprint(book):
                    raise PipelineError('The frozen source/chunk manifest was modified. Restore book.json or import into a new project.')
                after = path.stat()
                if stamp != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise PipelineError('Source manifest changed while reading. Retry the request.')
                self._books[key] = (stamp, book)
                while len(self._books) > 4:
                    self._books.popitem(last=False)
            else:
                book = cached[1]
            self._books.move_to_end(key)
        return book if readonly else copy.deepcopy(book)

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
