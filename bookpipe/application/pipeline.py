"""Analysis and translation use-case orchestration."""
from __future__ import annotations

from typing import Any

from ..engine import Runner, analysis_plan, export_text, previous_context, source_blocks
from ..util import PipelineError, atomic_json, digest, read_json
from .commands import AnalyzeCommand, TranslateCommand
from .ports import ApplicationDependencies, ProgressSink
from .projects import effective_settings, load_valid_book, parse_pass_profiles
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
        progress.message(
            "WARNING: different model. Fixed chunk boundaries remain; per-request token counts are recalculated. "
            "Old successful outputs are not replaced."
        )


def execute_analyze(store: Any, book: dict, client: Any, settings: dict,
                    progress: ProgressSink) -> PipelineResult:
    plan = analysis_plan(store, book, client, settings)
    runner = Runner(store, client, settings, progress)
    done = sum(bool(store.get("analysis:" + unit["id"])) for unit in plan)
    for unit in plan:
        progress.overall("P1/5 | analysis sections", done, len(plan))
        progress.chapter(
            f"Chapter/section {unit['chapter_number']}/{len(book['chapters'])} | analysis part",
            unit["part"] - 1, unit["parts"],
        )
        receipt = store.get("analysis:" + unit["id"])
        if receipt:
            if not store.job(receipt["key"], receipt["fingerprint"]):
                raise PipelineError("Analysis receipt has no valid checkpoint.")
            continue
        snapshot_path = store.root / "analysis_inputs" / (unit["id"] + ".json")
        if snapshot_path.exists():
            inputs = read_json(snapshot_path)
        else:
            text = "\n\n".join(block["text"] for block in unit["blocks"])
            memory = store.analysis_memory(text, client.count, settings["memory_tokens"])
            inputs = {
                "SECTION_ID": unit["id"], "SOURCE_BLOCKS": source_blocks(unit["blocks"]),
                "EXISTING_MEMORY": memory,
            }
            atomic_json(snapshot_path, inputs)
        key = "pass1/" + unit["id"]
        value, path, fingerprint = runner.run(1, key, inputs)
        store.merge_analysis(key, fingerprint, value, unit["blocks"], unit["chapter_id"])
        store.save_analysis_receipt(
            unit["id"], {"key": key, "fingerprint": fingerprint, "path": path},
        )
        done += 1
        progress.overall("P1/5 | analysis sections", done, len(plan))
        progress.chapter(
            f"Chapter/section {unit['chapter_number']}/{len(book['chapters'])} | analysis part",
            unit["part"], unit["parts"],
        )
    store.finish_analysis()
    path = store.write_review(book["source_fingerprint"])
    progress.phase("Analysis complete; human terminology review required. No prose translation generated.")
    progress.message(f"Review data: {path}\nNext: review --project {store.root}")
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
        label = (f"Chapter {chapter['number']}/{len(book['chapters'])} | "
                 f"unit {chunk['index_in_chapter']}/{len(chapter['chunk_ids'])} | passes P2-P5")
        progress.overall(
            f"Translation | book units | this run {run_index - 1}/{len(todo)}", done, len(book["chunks"]),
        )
        progress.chapter(label, 0, 4)
        context = previous_context(store, book, chunk, client, settings["continuity_tokens"])
        memory, dependencies = store.translation_memory(
            chunk, context["english"], client.count, settings["memory_tokens"],
        )
        blocks = source_blocks(chunk["blocks"])
        common = {
            "CHUNK_ID": chunk["id"], "SOURCE_BLOCKS": blocks, **memory,
            "PREVIOUS_CONTEXT": context,
        }
        p2, _, _ = runner.run(2, f"pass2/{chunk['id']}", {
            **common, "SOURCE_SENTENCES": chunk["sentences"],
        })
        progress.chapter(label, 1, 4)
        p3, _, _ = runner.run(3, f"pass3/{chunk['id']}", {**common, "SEMANTIC_AUDIT": p2})
        progress.chapter(label, 2, 4)
        p4, _, _ = runner.run(4, f"pass4/{chunk['id']}", {
            **common, "SOURCE_SENTENCES": chunk["sentences"],
            "POLISH_DRAFT": p3, "SEMANTIC_AUDIT": p2,
        })
        progress.chapter(label, 3, 4)
        _, final_path, _ = runner.run(5, f"pass5/{chunk['id']}", {
            **common, "POLISH_DRAFT": p3, "CORRECTION_LEDGER": p4,
        })
        store.finish_chunk(chunk["id"], final_path, dependencies, digest(memory["APPROVED_LEXICON"]))
        export_text(store, book)
        done += 1
        progress.overall(
            f"Translation | book units | this run {run_index}/{len(todo)}", done, len(book["chunks"]),
        )
        progress.chapter(label, 4, 4)
    export_text(store, book)
    progress.phase(f"Stopped after {len(todo)} completed unit(s). Rerun translate --continue N to proceed.")
    return PipelineResult(store.root, completed_units=len(todo))


class PipelineService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def _resources(self, command: AnalyzeCommand | TranslateCommand):
        root = command.project.resolve()
        scope = OperationScope(self.dependencies, root, self.progress)
        return root, scope

    def analyze(self, command: AnalyzeCommand) -> PipelineResult:
        root, scope = self._resources(command)
        with scope:
            book = load_valid_book(root)
            settings = effective_settings(self.dependencies.bundle, root, command)
            client = scope.providers(
                settings, profile=command.profile,
                pass_profiles=parse_pass_profiles(command.pass_profiles),
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
            return execute_analyze(scope.store, book, client, settings, self.progress)

    def translate(self, command: TranslateCommand) -> PipelineResult:
        root, scope = self._resources(command)
        with scope:
            book = load_valid_book(root)
            settings = effective_settings(self.dependencies.bundle, root, command)
            client = scope.providers(
                settings, profile=command.profile,
                pass_profiles=parse_pass_profiles(command.pass_profiles),
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
            return execute_translate(scope.store, book, client, settings, self.progress, command.chunk_limit)
