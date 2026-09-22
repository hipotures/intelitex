"""Analysis and translation use-case orchestration."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..engine import Runner, analysis_plan, export_text, previous_context, source_blocks
from ..progress import ProgressEvent
from ..util import PipelineError, atomic_json, digest, read_json
from .commands import AnalyzeCommand, TranslateCommand
from .ports import ApplicationDependencies, ProgressSink
from .projects import effective_settings, load_valid_book, validate_pass_profiles
from .results import PipelineResult
from .sessions import OperationScope


def check_model(client: Any, book: dict, allowed: bool, progress: ProgressSink) -> None:
    old = book.get("model_identity", {})
    if old and digest(old) != digest(client.identity):
        if not allowed:
            raise PipelineError(
                "The server model differs from the import model. Use --allow-model-change intentionally. "
                "Existing chunk boundaries/checkpoints will be preserved."
            )
        progress.emit(ProgressEvent(kind="model_changed", values={
            "fixed_chunk_boundaries": True,
            "recalculate_request_tokens": True,
            "replace_successful_outputs": False,
        }))


def execute_analyze(store: Any, book: dict, client: Any, settings: dict,
                    progress: ProgressSink, files=None) -> PipelineResult:
    plan = analysis_plan(store, book, client, settings)
    runner = Runner(store, client, settings, progress)
    done = sum(bool(store.get("analysis:" + unit["id"])) for unit in plan)
    for unit in plan:
        progress.emit(ProgressEvent(
            kind="analysis_progress", current=done, total=len(plan), values={"pass_no": 1},
        ))
        progress.emit(ProgressEvent(
            kind="analysis_unit_progress", current=unit["part"] - 1, total=unit["parts"],
            values={
                "pass_no": 1, "chapter_id": unit["chapter_id"],
                "chapter_number": unit["chapter_number"], "chapter_total": len(book["chapters"]),
                "unit_id": unit["id"], "part": unit["part"], "parts": unit["parts"],
            },
        ))
        receipt = store.get("analysis:" + unit["id"])
        if receipt:
            if not store.job(receipt["key"], receipt["fingerprint"]):
                raise PipelineError("Analysis receipt has no valid checkpoint.")
            continue
        snapshot_path = store.root / "analysis_inputs" / (unit["id"] + ".json")
        if (files.exists(snapshot_path) if files else snapshot_path.exists()):
            inputs = files.read_json(snapshot_path) if files else read_json(snapshot_path)
        else:
            text = "\n\n".join(block["text"] for block in unit["blocks"])
            memory = store.analysis_memory(text, client.count, settings["memory_tokens"])
            inputs = {
                "SECTION_ID": unit["id"], "SOURCE_BLOCKS": source_blocks(unit["blocks"]),
                "EXISTING_MEMORY": memory,
            }
            (files.write_json(snapshot_path, inputs) if files else atomic_json(snapshot_path, inputs))
        key = "pass1/" + unit["id"]
        progress.emit(ProgressEvent(kind="pass_started", values={
            "pass_no": 1, "task_key": key, "chapter_id": unit["chapter_id"],
            "unit_id": unit["id"],
        }))
        value, path, fingerprint = runner.run(1, key, inputs)
        store.merge_analysis(key, fingerprint, value, unit["blocks"], unit["chapter_id"])
        store.save_analysis_receipt(
            unit["id"], {"key": key, "fingerprint": fingerprint, "path": path},
        )
        done += 1
        progress.emit(ProgressEvent(
            kind="analysis_progress", current=done, total=len(plan), values={"pass_no": 1},
        ))
        progress.emit(ProgressEvent(
            kind="analysis_unit_progress", current=unit["part"], total=unit["parts"],
            values={
                "pass_no": 1, "chapter_id": unit["chapter_id"],
                "chapter_number": unit["chapter_number"], "chapter_total": len(book["chapters"]),
                "unit_id": unit["id"], "part": unit["part"], "parts": unit["parts"],
            },
        ))
    store.finish_analysis()
    path = store.write_review(book["source_fingerprint"])
    progress.emit(ProgressEvent(kind="analysis_completed", values={
        "project": str(store.root), "review_path": str(path),
        "prose_translation_generated": False,
    }))
    return PipelineResult(store.root, review_path=path)


def execute_translate(store: Any, book: dict, client: Any, settings: dict,
                      progress: ProgressSink, limit: int) -> PipelineResult:
    if not store.get("analysis_done"):
        raise PipelineError("Run analyze to completion first. Translation does not trigger analysis implicitly.")
    if not store.get("approved"):
        raise PipelineError("Review terms.review.json and run approve before translation.")
    if type(limit) is not int or limit < 0:
        raise PipelineError("--continue must be 0 (all) or a positive number of unfinished chunks.")
    runner = Runner(store, client, settings, progress)
    pending = [chunk for chunk in book["chunks"] if store.chunk(chunk["id"])["status"] != "done"]
    todo = pending[:limit] if limit else pending
    done = len(book["chunks"]) - len(pending)
    by_chapter = {chapter["id"]: chapter for chapter in book["chapters"]}
    for run_index, chunk in enumerate(todo, 1):
        chapter = by_chapter[chunk["chapter_id"]]
        progress.emit(ProgressEvent(
            kind="translation_progress", current=done, total=len(book["chunks"]),
            values={"run_current": run_index - 1, "run_total": len(todo)},
        ))
        progress.emit(ProgressEvent(
            kind="translation_unit_progress", current=0, total=4,
            values={
                "chapter_id": chapter["id"], "chapter_number": chapter["number"],
                "chapter_total": len(book["chapters"]), "chunk_id": chunk["id"],
                "chunk_index": chunk["index_in_chapter"], "chunk_total": len(chapter["chunk_ids"]),
            },
        ))
        context = previous_context(store, book, chunk, client, settings["continuity_tokens"])
        memory, dependencies = store.translation_memory(
            chunk, context["english"], client.count, settings["memory_tokens"],
        )
        blocks = source_blocks(chunk["blocks"])
        common = {
            "CHUNK_ID": chunk["id"], "SOURCE_BLOCKS": blocks, **memory,
            "PREVIOUS_CONTEXT": context,
        }
        key = f"pass2/{chunk['id']}"
        progress.emit(ProgressEvent(kind="pass_started", values={
            "pass_no": 2, "task_key": key, "chapter_id": chapter["id"], "chunk_id": chunk["id"],
        }))
        p2, _, _ = runner.run(2, key, {
            **common, "SOURCE_SENTENCES": chunk["sentences"],
        })
        progress.emit(ProgressEvent(kind="translation_unit_progress", current=1, total=4, values={
            "chapter_id": chapter["id"], "chapter_number": chapter["number"],
            "chapter_total": len(book["chapters"]), "chunk_id": chunk["id"],
            "chunk_index": chunk["index_in_chapter"], "chunk_total": len(chapter["chunk_ids"]),
        }))
        key = f"pass3/{chunk['id']}"
        progress.emit(ProgressEvent(kind="pass_started", values={
            "pass_no": 3, "task_key": key, "chapter_id": chapter["id"], "chunk_id": chunk["id"],
        }))
        p3, _, _ = runner.run(3, key, {**common, "SEMANTIC_AUDIT": p2})
        progress.emit(ProgressEvent(kind="translation_unit_progress", current=2, total=4, values={
            "chapter_id": chapter["id"], "chapter_number": chapter["number"],
            "chapter_total": len(book["chapters"]), "chunk_id": chunk["id"],
            "chunk_index": chunk["index_in_chapter"], "chunk_total": len(chapter["chunk_ids"]),
        }))
        key = f"pass4/{chunk['id']}"
        progress.emit(ProgressEvent(kind="pass_started", values={
            "pass_no": 4, "task_key": key, "chapter_id": chapter["id"], "chunk_id": chunk["id"],
        }))
        p4, _, _ = runner.run(4, key, {
            **common, "SOURCE_SENTENCES": chunk["sentences"],
            "POLISH_DRAFT": p3, "SEMANTIC_AUDIT": p2,
        })
        progress.emit(ProgressEvent(kind="translation_unit_progress", current=3, total=4, values={
            "chapter_id": chapter["id"], "chapter_number": chapter["number"],
            "chapter_total": len(book["chapters"]), "chunk_id": chunk["id"],
            "chunk_index": chunk["index_in_chapter"], "chunk_total": len(chapter["chunk_ids"]),
        }))
        key = f"pass5/{chunk['id']}"
        progress.emit(ProgressEvent(kind="pass_started", values={
            "pass_no": 5, "task_key": key, "chapter_id": chapter["id"], "chunk_id": chunk["id"],
        }))
        _, final_path, _ = runner.run(5, key, {
            **common, "POLISH_DRAFT": p3, "CORRECTION_LEDGER": p4,
        })
        store.finish_chunk(chunk["id"], final_path, dependencies, digest(memory["APPROVED_LEXICON"]))
        export_text(store, book)
        done += 1
        progress.emit(ProgressEvent(
            kind="translation_progress", current=done, total=len(book["chunks"]),
            values={"run_current": run_index, "run_total": len(todo)},
        ))
        progress.emit(ProgressEvent(kind="translation_unit_progress", current=4, total=4, values={
            "chapter_id": chapter["id"], "chapter_number": chapter["number"],
            "chapter_total": len(book["chapters"]), "chunk_id": chunk["id"],
            "chunk_index": chunk["index_in_chapter"], "chunk_total": len(chapter["chunk_ids"]),
        }))
    export_text(store, book)
    progress.emit(ProgressEvent(kind="translation_stopped", values={"completed_units": len(todo)}))
    return PipelineResult(store.root, completed_units=len(todo))


class PipelineService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink,
                 publishing=None):
        self.dependencies, self.progress = dependencies, progress
        self.publishing = publishing

    def _resources(self, command: AnalyzeCommand | TranslateCommand):
        root = command.project.resolve()
        scope = OperationScope(self.dependencies, root, self.progress)
        return root, scope

    def analyze(self, command: AnalyzeCommand) -> PipelineResult:
        root, scope = self._resources(command)
        with scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            settings = effective_settings(
                self.dependencies.bundle, root, command, files=self.dependencies.files,
            )
            client = scope.providers(
                settings, profile=command.profile,
                pass_profiles=validate_pass_profiles(command.pass_profiles),
            )
            client.planning_pass = 1
            selected = client.for_pass(1)
            if getattr(selected, "provider", "llamacpp") == "llamacpp":
                client.discover(1)
            else:
                client.identity = {
                    "provider": selected.provider, "requested_model": selected.model,
                    "profile": selected.profile_name,
                }
            check_model(client, book, command.allow_model_change, self.progress)
            return execute_analyze(
                scope.store, book, client, settings, self.progress, self.dependencies.files,
            )

    def translate(self, command: TranslateCommand) -> PipelineResult:
        root, scope = self._resources(command)
        became_complete = False
        with scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            settings = effective_settings(
                self.dependencies.bundle, root, command, files=self.dependencies.files,
            )
            client = scope.providers(
                settings, profile=command.profile,
                pass_profiles=validate_pass_profiles(command.pass_profiles),
            )
            client.planning_pass = 2
            selected = client.for_pass(2)
            if getattr(selected, "provider", "llamacpp") == "llamacpp":
                client.discover(2)
            else:
                client.identity = {
                    "provider": selected.provider, "requested_model": selected.model,
                    "profile": selected.profile_name,
                }
            check_model(client, book, command.allow_model_change, self.progress)
            result = execute_translate(
                scope.store, book, client, settings, self.progress, command.chunk_limit,
            )
            became_complete = result.completed_units > 0 and all(
                scope.store.chunk(chunk["id"])["status"] == "done" for chunk in book["chunks"]
            )
        if became_complete and self.publishing is not None:
            from .commands import PublicationStatusCommand, PublishCommand
            try:
                published = self.publishing.publish(PublishCommand(root))
                return replace(result, publication=published.status)
            except PipelineError:
                # Translation checkpoints are already committed.  Publication
                # records the failure and remains independently retryable.
                status = self.publishing.status(PublicationStatusCommand(root))
                return replace(result, publication=status)
        return result
