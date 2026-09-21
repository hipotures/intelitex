"""Checkpoint-verified reader queries and marker operations."""
from __future__ import annotations

import copy
import re
import threading
from pathlib import Path

from ..reader_context import ReaderContext
from ..util import PipelineError, atomic_json, digest, read_json
from .commands import ReaderSessionCommand
from .ports import ApplicationDependencies, ProgressSink
from .projects import load_valid_book
from .sessions import ReaderScope


class MarkerConflict(PipelineError):
    """Marker state changed since the caller loaded it."""


class MarkerRepository:
    def __init__(self, root: Path, context: ReaderContext | None = None):
        self.root = root.resolve()
        self.path = self.root / "translation.review.json"
        self.context = context or ReaderContext(self.root)
        self.lock = threading.Lock()

    def _empty(self) -> dict:
        return {"format_version": 1, "book_fingerprint": self.context.metadata()["book_fingerprint"], "markers": []}

    def _read(self) -> dict:
        state = read_json(self.path) if self.path.is_file() else self._empty()
        if not isinstance(state, dict) or state.get("format_version") != 1:
            raise PipelineError("translation.review.json has an unsupported format version.")
        expected = self.context.metadata()["book_fingerprint"]
        if state.get("book_fingerprint") != expected:
            raise PipelineError(
                "translation.review.json belongs to a different book fingerprint; move it aside before using Reader."
            )
        markers = state.get("markers")
        if not isinstance(markers, list):
            raise PipelineError("translation.review.json has no valid markers array.")
        seen: set[str] = set()
        for marker in markers:
            self._validate_marker_structure(marker)
            if marker["id"] in seen:
                raise PipelineError(f"Duplicate marker ID: {marker['id']}.")
            seen.add(marker["id"])
        return state

    def _validate_marker_structure(self, marker: dict) -> None:
        if not isinstance(marker, dict):
            raise PipelineError("Marker must be a JSON object.")
        if not isinstance(marker.get("id"), str) or not re.fullmatch(r"M\d{6,}", marker["id"]):
            raise PipelineError("Marker has an invalid ID.")
        if not isinstance(marker.get("chapter_id"), str) or not isinstance(marker.get("block_id"), str):
            raise PipelineError("Marker chapter_id and block_id must be strings.")
        if not self.context.has_canonical_block(marker["chapter_id"], marker["block_id"]):
            raise PipelineError(
                f"Marker references unknown block {marker['block_id']} in chapter {marker['chapter_id']}."
            )
        start, end = marker.get("start"), marker.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise PipelineError("Marker start and end must be ordered non-negative integer offsets.")
        if not isinstance(marker.get("text"), str):
            raise PipelineError("Marker text must be a string.")

    def _validate_location(self, marker: dict) -> None:
        chapter_id, block_id = marker.get("chapter_id"), marker.get("block_id")
        if not isinstance(chapter_id, str) or not isinstance(block_id, str):
            raise PipelineError("chapter_id and block_id must be strings.")
        start, end = marker.get("start"), marker.get("end")
        if type(start) is not int or type(end) is not int:
            raise PipelineError("Marker start and end must be integer Unicode code-point offsets.")
        submitted = marker.get("text")
        if not isinstance(submitted, str):
            raise PipelineError("Marker text must be a string.")
        text = self.context.block_text(chapter_id, block_id)
        if not 0 <= start < end <= len(text):
            raise PipelineError("Marker offsets are outside the translated block.")
        if text[start:end] != submitted:
            raise MarkerConflict("Marker text no longer matches the checkpoint-verified translated block.")

    @staticmethod
    def _check_revision(state: dict, expected: object) -> None:
        if not isinstance(expected, str) or not expected:
            raise PipelineError("Marker write requires the loaded revision.")
        if expected != digest(state):
            raise MarkerConflict("Marker data changed in another tab or process. Reload before saving.")

    def load(self) -> dict:
        with self.lock:
            state = self._read()
            return copy.deepcopy({**state, "_revision": digest(state)})

    def create(self, payload: dict, expected_revision: object) -> dict:
        allowed = {"chapter_id", "block_id", "start", "end", "text"}
        if set(payload) != allowed:
            raise PipelineError("Marker request must contain only chapter_id, block_id, start, end, and text.")
        with self.lock:
            state = self._read()
            self._check_revision(state, expected_revision)
            self._validate_location(payload)
            duplicate = next((marker for marker in state["markers"] if all(
                marker[key] == payload[key] for key in ("chapter_id", "block_id", "start", "end")
            )), None)
            if duplicate is not None:
                return copy.deepcopy({"marker": duplicate, "revision": digest(state), "created": False})
            number = max((int(marker["id"][1:]) for marker in state["markers"]), default=0) + 1
            marker = {"id": f"M{number:06d}", **payload}
            state["markers"].append(marker)
            atomic_json(self.path, state)
            return copy.deepcopy({"marker": marker, "revision": digest(state), "created": True})

    def delete(self, marker_id: str, expected_revision: object) -> dict:
        if not re.fullmatch(r"M\d{6,}", marker_id):
            raise PipelineError("Invalid marker ID.")
        with self.lock:
            state = self._read()
            self._check_revision(state, expected_revision)
            index = next((index for index, marker in enumerate(state["markers"])
                          if marker["id"] == marker_id), None)
            if index is None:
                raise PipelineError(f"Unknown marker ID: {marker_id}.")
            state["markers"].pop(index)
            atomic_json(self.path, state)
            return {"deleted": marker_id, "revision": digest(state)}


class ReaderSession:
    def __init__(self, dependencies: ApplicationDependencies, command: ReaderSessionCommand,
                 progress: ProgressSink):
        self.dependencies, self.command, self.progress = dependencies, command, progress
        self.project = command.project.resolve()
        self._scope: ReaderScope | None = None
        self._context: ReaderContext | None = None
        self._markers: MarkerRepository | None = None

    def __enter__(self) -> ReaderSession:
        scope = ReaderScope(self.dependencies, self.project, self.progress)
        scope.__enter__()
        try:
            load_valid_book(self.project, self.dependencies.plan_fingerprint)
            context = ReaderContext(self.project)
            markers = MarkerRepository(self.project, context)
            markers.load()
        except BaseException:
            import sys
            scope.__exit__(*sys.exc_info())
            raise
        self._scope, self._context, self._markers = scope, context, markers
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._scope is not None
        self._scope.__exit__(exc_type, exc, traceback)
        self._scope = self._context = self._markers = None

    @property
    def path(self): return self._repo.path
    @property
    def context_service(self): return self._ctx
    @property
    def _ctx(self):
        if self._context is None:
            raise RuntimeError("ReaderSession must be entered before use.")
        return self._context
    @property
    def _repo(self):
        if self._markers is None:
            raise RuntimeError("ReaderSession must be entered before use.")
        return self._markers

    def load(self): return self._repo.load()
    def metadata(self): return copy.deepcopy(self._ctx.metadata())
    def progress_snapshot(self): return copy.deepcopy(self._ctx.progress())
    def progress(self): return self.progress_snapshot()
    def chapter(self, chapter_id): return copy.deepcopy(self._ctx.chapter(chapter_id))
    def context(self, chapter_id, block_id, position):
        return copy.deepcopy(self._ctx.context(chapter_id, block_id, position))
    def create_marker(self, payload, expected_revision): return self._repo.create(payload, expected_revision)
    def delete_marker(self, marker_id, expected_revision): return self._repo.delete(marker_id, expected_revision)
    # Structural compatibility used by the unchanged HTTP adapter.
    def create(self, payload, expected_revision): return self.create_marker(payload, expected_revision)
    def delete(self, marker_id, expected_revision): return self.delete_marker(marker_id, expected_revision)


class ReaderService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def open_session(self, command: ReaderSessionCommand | Path) -> ReaderSession:
        if isinstance(command, Path):
            command = ReaderSessionCommand(command)
        return ReaderSession(self.dependencies, command, self.progress)
