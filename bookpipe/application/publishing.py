"""Application orchestration for final EPUB publication."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..engine import source_blocks, validate_result
from ..progress import ProgressEvent
from ..util import PipelineError, digest
from .commands import PublicationStatusCommand, PublishCommand
from .ports import (
    ApplicationDependencies,
    ProgressSink,
    PublicationBlock,
    PublicationRequest,
    PublicationSourceFile,
)
from .projects import load_valid_book
from .results import PublicationStatus, PublishResult
from .sessions import OperationScope


PUBLICATION_RECORD_VERSION = 1
PUBLICATION_FORMAT = "intelitex-epub-v1"
GENERATED_BY = "Intelitex"


def _target_language(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
        raise PipelineError("target_language must be a valid BCP 47-style language tag.")
    return value.lower()


def _safe_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "book")[:120].rstrip(" .")


def _record_path(root: Path) -> Path:
    return root / "publication.json"


def _record_output(root: Path, value) -> Path | None:
    if not isinstance(value, str):
        return None
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root):
        raise PipelineError("publication.json contains an output path outside the project.")
    return candidate


class PublishingService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    @property
    def builder(self):
        if self.dependencies.publication_builder is None:
            raise PipelineError("No EPUB publication builder is configured.")
        return self.dependencies.publication_builder

    def _read_record(self, root: Path) -> dict:
        path = _record_path(root)
        if not self.dependencies.files.is_file(path):
            return {"format_version": PUBLICATION_RECORD_VERSION}
        value = self.dependencies.files.read_json(path)
        if not isinstance(value, dict) or value.get("format_version") != PUBLICATION_RECORD_VERSION:
            raise PipelineError("publication.json has an unsupported or invalid format.")
        return value

    def _write_record(self, root: Path, record: dict) -> None:
        self.dependencies.files.write_json(_record_path(root), record)

    def _prepare(self, root: Path, book: dict, store, target_language: str):
        if not store.get("analysis_done"):
            raise PipelineError("Publishing requires completed P1 analysis.")
        if not store.get("approved"):
            raise PipelineError("Publishing requires approved terminology.")
        states = [(chunk, store.chunk(chunk["id"])) for chunk in book["chunks"]]
        stale = [chunk["id"] for chunk, state in states if state["status"] == "stale"]
        pending = [chunk["id"] for chunk, state in states if state["status"] == "pending"]
        other = [chunk["id"] for chunk, state in states if state["status"] not in {"done", "stale", "pending"}]
        if stale:
            raise PipelineError(
                f"Publishing refused: {len(stale)} translation chunk(s) are stale; "
                f"retranslate them first ({', '.join(stale[:5])})."
            )
        if pending:
            raise PipelineError(
                f"Publishing refused: {len(pending)} translation chunk(s) are pending; "
                f"finish translation first ({', '.join(pending[:5])})."
            )
        if other or not states:
            raise PipelineError("Publishing requires every translation chunk to be done.")

        all_blocks = []
        for section in (*book.get("chapters", []), *book.get("non_narrative_sections", [])):
            all_blocks.extend(section.get("blocks", []))
        all_blocks.sort(key=lambda block: block["order"])
        ordinals: dict[str, tuple[str, int]] = {}
        source_by_id: dict[str, dict] = {}
        per_file: dict[str, int] = {}
        for block in all_blocks:
            relative = block["file"]
            ordinal = per_file.get(relative, 0)
            ordinals[block["id"]] = (relative, ordinal)
            source_by_id[block["id"]] = block
            per_file[relative] = ordinal + 1

        translated_parts: dict[str, list[tuple[int, int, str]]] = {}
        artifact_inputs = []
        for chunk, state in states:
            relative = state.get("final_path")
            if not isinstance(relative, str) or not relative:
                raise PipelineError(f"Done chunk {chunk['id']} has no final P5 artifact reference.")
            final = store.checked_result(relative)
            validate_result(5, final, {"SOURCE_BLOCKS": source_blocks(chunk["blocks"])})
            artifact_inputs.append({"chunk": chunk["id"], "artifact": digest(final)})
            translated = {item["id"]: item["text"] for item in final["translations"]}
            for block in chunk["blocks"]:
                parent_id = block.get("parent_id", block["id"])
                if parent_id not in ordinals:
                    raise PipelineError(
                        f"Translated block {block['id']} has no deterministic source XHTML mapping."
                    )
                parent = source_by_id[parent_id]
                start = block.get("start", 0)
                end = block.get("end", len(parent["text"]))
                if (
                    type(start) is not int or type(end) is not int
                    or parent["text"][start:end] != block["text"]
                ):
                    raise PipelineError(
                        f"Translated piece {block['id']} cannot be mapped safely to parent block {parent_id}."
                    )
                translated_parts.setdefault(parent_id, []).append(
                    (start, end, translated[block["id"]].strip()),
                )

        bindings: list[PublicationBlock] = []
        for parent_id, parts in translated_parts.items():
            parent = source_by_id[parent_id]
            parts.sort(key=lambda item: item[0])
            cursor = 0
            for start, end, _ in parts:
                if start != cursor or end <= start:
                    raise PipelineError(
                        f"Translated pieces do not cover parent block {parent_id} exactly once."
                    )
                cursor = end
            if cursor != len(parent["text"]):
                raise PipelineError(
                    f"Translated pieces do not cover parent block {parent_id} completely."
                )
            source_file, ordinal = ordinals[parent_id]
            bindings.append(PublicationBlock(
                id=parent_id, source_file=source_file, source_ordinal=ordinal,
                source_text=parent["text"], translated_text=" ".join(item[2] for item in parts),
            ))

        metadata = book.get("metadata", {})
        package_document = metadata.get("opf")
        if not isinstance(package_document, str) or not package_document:
            raise PipelineError(
                "Publishing requires an imported EPUB container/OPF package; "
                "this project came from a generic HTML directory."
            )
        source_files = tuple(PublicationSourceFile(
            path=item["file"], sha256=item["sha256"], encoding=item["encoding"],
        ) for item in book["files"])
        source_root = Path(book["source_root"])
        source_info = self.builder.inspect(source_root, package_document, source_files)
        title = source_info.title or metadata.get("title") or source_root.name
        publication_fingerprint = digest({
            "format": PUBLICATION_FORMAT,
            "source_fingerprint": book["source_fingerprint"],
            "content_fingerprint": book["content_fingerprint"],
            "source_package_fingerprint": source_info.package_fingerprint,
            "target_language": target_language,
            "artifacts": artifact_inputs,
        })
        output = root / "published" / f"{_safe_title(title)} [{target_language.upper()}].epub"
        request = PublicationRequest(
            source_root=source_root, package_document=package_document,
            output_path=output, source_fingerprint=book["source_fingerprint"],
            package_fingerprint=source_info.package_fingerprint,
            target_language=target_language, title=title,
            source_files=source_files, blocks=tuple(bindings),
        )
        return publication_fingerprint, request

    def publish(self, command: PublishCommand) -> PublishResult:
        root = command.project.resolve()
        target = _target_language(command.target_language)
        with OperationScope(self.dependencies, root, self.progress) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            record = self._read_record(root)
            fingerprint = None
            try:
                fingerprint, request = self._prepare(root, book, scope.store, target)
                success = record.get("last_success") or {}
                existing_path = _record_output(root, success.get("output_path"))
                if (
                    success.get("publication_fingerprint") == fingerprint
                    and existing_path is not None
                    and self.dependencies.files.is_file(existing_path)
                    and success.get("output_sha256") == digest(self.dependencies.files.read_bytes(existing_path))
                ):
                    status = self._status_from(root, book, scope.store, target, record, prepared=(fingerprint, request))
                    return PublishResult(root, status, built=False)
                self.progress.emit(ProgressEvent(kind="publication_started", values={
                    "project": str(root), "target_language": target,
                    "output_path": str(request.output_path),
                }))
                result = self.builder.build(request)
                relative_output = str(result.output_path.relative_to(root))
                success = {
                    "publication_fingerprint": fingerprint,
                    "output_path": relative_output,
                    "output_sha256": digest(self.dependencies.files.read_bytes(result.output_path)),
                    "target_language": target,
                    "generated_by": GENERATED_BY,
                    "title": result.title,
                    "creators": list(result.creators),
                    "source_language": result.source_language,
                    "generated_at": result.generated_at,
                    "validation": list(result.validation),
                }
                record = {
                    "format_version": PUBLICATION_RECORD_VERSION,
                    "last_success": success,
                    "last_attempt": {
                        "status": "published", "publication_fingerprint": fingerprint,
                        "target_language": target,
                    },
                }
                self._write_record(root, record)
                self.progress.emit(ProgressEvent(kind="publication_completed", values={
                    "output_path": str(result.output_path), "target_language": target,
                    "publication_fingerprint": fingerprint,
                }))
                status = self._status_from(root, book, scope.store, target, record, prepared=(fingerprint, request))
                return PublishResult(root, status, built=True)
            except Exception as exc:
                record["format_version"] = PUBLICATION_RECORD_VERSION
                record["last_attempt"] = {
                    "status": "failed", "publication_fingerprint": fingerprint,
                    "target_language": target, "error": str(exc),
                }
                self._write_record(root, record)
                self.progress.emit(ProgressEvent(kind="publication_failed", values={
                    "target_language": target, "error": str(exc),
                }))
                raise PipelineError(f"EPUB publishing failed: {exc}") from exc

    def status(self, command: PublicationStatusCommand) -> PublicationStatus:
        root = command.project.resolve()
        target = _target_language(command.target_language)
        with OperationScope(self.dependencies, root, self.progress) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            record = self._read_record(root)
            return self._status_from(root, book, scope.store, target, record)

    def _status_from(self, root: Path, book: dict, store, target: str, record: dict,
                     *, prepared=None) -> PublicationStatus:
        statuses = [store.chunk(chunk["id"])["status"] for chunk in book["chunks"]]
        translation_complete = bool(statuses) and all(value == "done" for value in statuses)
        success = record.get("last_success") or {}
        output_path = None
        if isinstance(success.get("output_path"), str):
            candidate = _record_output(root, success["output_path"])
            assert candidate is not None
            if self.dependencies.files.is_file(candidate):
                output_path = candidate
        validation = tuple(success.get("validation") or ())
        current_fingerprint = None
        readiness_error = None
        if translation_complete and store.get("analysis_done") and store.get("approved"):
            try:
                current_fingerprint, _ = prepared or self._prepare(root, book, store, target)
            except PipelineError as exc:
                readiness_error = str(exc)
        else:
            if not store.get("analysis_done"):
                readiness_error = "P1 analysis is not complete."
            elif not store.get("approved"):
                readiness_error = "Terminology is not approved."
            elif "stale" in statuses:
                readiness_error = "One or more translation chunks are stale."
            else:
                readiness_error = "One or more translation chunks are pending."

        output_valid = bool(output_path)
        if output_valid and success.get("output_sha256"):
            output_valid = success["output_sha256"] == digest(self.dependencies.files.read_bytes(output_path))
        current = bool(
            current_fingerprint
            and success.get("publication_fingerprint") == current_fingerprint
            and success.get("target_language") == target
            and output_valid
        )
        attempt = record.get("last_attempt") or {}
        failed_current = (
            attempt.get("status") == "failed"
            and (attempt.get("publication_fingerprint") in {None, current_fingerprint})
            and attempt.get("target_language") == target
            and translation_complete
        )
        if current:
            state, last_error = "published", None
        elif failed_current:
            state, last_error = "failed", attempt.get("error") or readiness_error
        elif output_path is not None:
            state, last_error = "stale", readiness_error
        elif readiness_error:
            state, last_error = "not_ready", readiness_error
        else:
            state, last_error = "ready", None
        return PublicationStatus(
            state=state, translation_complete=translation_complete, current=current,
            output_path=output_path, target_language=target,
            publication_fingerprint=success.get("publication_fingerprint"),
            generated_by=success.get("generated_by"), title=success.get("title"),
            creators=tuple(success.get("creators") or ()),
            source_language=success.get("source_language"),
            generated_at=success.get("generated_at"), last_error=last_error,
            last_failure=attempt.get("error") if attempt.get("status") == "failed" else None,
            validation=validation,
        )
