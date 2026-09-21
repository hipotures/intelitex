"""Text product and strict-copy orchestration."""
from __future__ import annotations

from ..engine import export_text as rebuild_text
from ..util import PipelineError
from .commands import ExportCommand
from .ports import ApplicationDependencies, ProgressSink
from .projects import load_valid_book
from .results import ExportResult
from .sessions import OperationScope


class ExportsService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def export_text(self, command: ExportCommand) -> ExportResult:
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            rebuild_text(scope.store, book)
            destination = command.output.resolve() if command.output else None
            normalized_encoding = command.encoding.lower()
            if destination:
                if destination.is_relative_to(root) and not destination.is_relative_to(root / "exports"):
                    raise PipelineError(
                        "Additional exports inside a project must be placed under its exports/ directory; "
                        "internal files are protected."
                    )
                text = self.dependencies.files.read_text(root / "translation.txt", encoding="utf-8")
                try:
                    raw = text.encode(command.encoding, errors="strict")
                except (UnicodeError, LookupError) as exc:
                    raise PipelineError(
                        f"Cannot export to {command.encoding} without loss: {exc}. UTF-8 originals remain unchanged."
                    ) from exc
                if destination == root / "translation.txt" and normalized_encoding not in {"utf-8", "utf8"}:
                    raise PipelineError("Do not overwrite the internal UTF-8 translation with a legacy encoding.")
                self.dependencies.files.write_bytes(destination, raw)
            elif normalized_encoding not in {"utf-8", "utf8"}:
                raise PipelineError("Supply --output for a legacy-encoding copy.")
            return ExportResult(root / "translation.txt", destination, command.encoding)
