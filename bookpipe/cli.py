from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .application import (
    AnalyzeCommand, ApproveCommand, AttemptsCommand, CatalogImportCommand, DiscoverCommand,
    DoctorCommand, ExportCommand, ImportBookCommand, ProfilesCommand, ReaderSessionCommand,
    PublishCommand, ReviewSessionCommand, SmokeCommand, StatusCommand, TranslateCommand, UsageCommand,
)
from .bootstrap import create_application
from .provider_registry import ProviderPool
from .reader import run_reader_server
from .review import run_review_server
from .store import Store
from .ui import Display
from .util import PipelineError, plan_fingerprint

BUNDLE = Path(__file__).resolve().parent.parent


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Persistent five-pass literary translation. Import -> analyze -> human review -> approve -> translate -> publish.")
    sub = p.add_subparsers(dest="command", required=True)
    pipeline_commands = (
        ("import", "Import an unpacked EPUB/HTML folder; plan chapters and chunks without translating."),
        ("analyze", "P1 over the whole book with cumulative memory; then stop for review."),
        ("review", "Open the local terminology review application backed by terms.review.json."),
        ("reader", "Read checkpointed Polish P5 text and capture lightweight prose markers."),
        ("approve", "Commit human choices. No model call. Required before translation."),
        ("translate", "Run P2-P5 for the next N unfinished chunks; resume checkpoints automatically."),
        ("status", "Show local progress without contacting the server."),
        ("export", "Rebuild completed text; optionally produce a strict legacy-encoding copy."),
        ("publish", "Build or retry the final translated EPUB without contacting a model."),
    )
    for name, help_text in pipeline_commands:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--quiet", action="store_true", help="Disable progress display.")
        if name in {"import", "analyze", "translate"}:
            command.add_argument("--host", help="IP address or http(s) URL; saved during import.")
            command.add_argument("--port", type=int)
            command.add_argument("--model", help="Model ID from /v1/models.")
            command.add_argument("--context-size", type=int, help="Optional context ceiling; never exceeds detected server capacity.")
            command.add_argument("--thinking", choices=["off", "analysis"], help="off is default; analysis enables model thinking in P1/P2/P4.")
            command.add_argument("--allow-model-change", action="store_true", help="Explicitly permit a different model; preserve the existing chunk manifest/checkpoints.")
            command.add_argument("--profile", help="Use one named profile for this command without saving it.")
            command.add_argument("--pass-profile", action="append", default=[], metavar="P=PROFILE",
                                 help="Override a pass profile for this command; repeatable.")
        if name == "import":
            command.add_argument("folder", type=Path)
            command.add_argument("--previous-volume", type=Path,
                                 help="Seed this import from the immediately preceding approved Intelitex project.")
            command.add_argument("--opf", type=Path, help="Select an OPF package if several exist.")
            command.add_argument("--input-encoding", help="Explicit HTML decoding override; default reads declared encoding/BOM.")
            command.add_argument("--chapter-mode", choices=["auto", "file", "headings"], default="auto")
            command.add_argument("--chapter-selector", help="Optional CSS selector for nonstandard chapter headings, e.g. p.chapter.")
            command.add_argument("--include", dest="include_glob", help="Optional path glob, e.g. '*split_*.html'.")
            command.add_argument("--sidecar-txt", action="store_true", help="Also write UTF-8 .txt beside input HTML; never overwrite different text.")
            command.add_argument("--whole-section-limit", type=int, default=None,
                                 help="Keep a section intact at or below this visible-text character count; larger sections split only at natural scene boundaries. Default: inherited for continuations, otherwise 10000.")
        if name == "review":
            command.add_argument("--bind", default="127.0.0.1", help="Review web server bind address. Default: 127.0.0.1.")
            command.add_argument("--review-port", type=int, default=8765, help="Review web server port; 0 chooses a free port. Default: 8765.")
            command.add_argument("--no-browser", action="store_true", help="Do not open the review UI in the default browser.")
        if name == "reader":
            command.add_argument("--bind", default="127.0.0.1", help="Reader web server bind address. Default: 127.0.0.1.")
            command.add_argument("--reader-port", type=int, default=8766, help="Reader web server port; 0 chooses a free port. Default: 8766.")
            command.add_argument("--no-browser", action="store_true", help="Do not open the Reader in the default browser.")
        if name == "translate":
            command.add_argument("--continue", dest="chunk_limit", type=int, default=5, metavar="N", help="Next N unfinished chunks; 0 means all. Default: 5.")
        if name == "approve":
            command.add_argument("--accept-defaults", action="store_true", help="Explicitly approve the current selected/custom values without confirmed=true.")
        if name == "export":
            command.add_argument("--encoding", default="utf-8", help="Encoding for an additional copy; internal state always stays UTF-8.")
            command.add_argument("--output", type=Path, help="Required for non-UTF-8 export.")
        if name == "publish":
            command.add_argument("--target-language", default="pl",
                                 help="BCP 47 target language tag. Default: pl.")
    for name, help_text in (
        ("profiles", "Show configured and resolved profiles without exposing credentials."),
        ("doctor", "Validate local configuration and Codex protocol without a model turn."),
        ("attempts", "Inspect retained attempt evidence offline."),
        ("usage", "Report retained token accounting offline."),
        ("catalog-import", "Validate and atomically activate a model/pricing catalog."),
        ("discover", "Explicitly query a selected provider's live model metadata."),
        ("smoke", "Run one explicitly authorized live structured-output request."),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--project", type=Path, required=True)
        command.add_argument("--quiet", action="store_true")
        if name == "attempts":
            command.add_argument("--attempt", help="Project-relative attempt directory; omit to list all.")
        if name == "catalog-import":
            command.add_argument("file", type=Path)
        if name in {"discover", "smoke"}:
            command.add_argument("--profile")
            command.add_argument("--pass", dest="pass_no", type=int, choices=range(1, 6), default=1)
        if name == "smoke":
            command.add_argument("--live", action="store_true", help="Required acknowledgement that this command may spend provider credit/quota.")
    return p


def _model_options(args) -> dict:
    return {
        "project": args.project.resolve(), "host": args.host, "port": args.port,
        "model": args.model, "context_size": args.context_size, "thinking": args.thinking,
        "allow_model_change": args.allow_model_change, "profile": args.profile,
        "pass_profiles": _parse_pass_profiles(args.pass_profile),
    }


def _parse_pass_profiles(values: list[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for value in values:
        try:
            number_text, name = value.split("=", 1)
            number = int(number_text.removeprefix("P").removeprefix("p"))
        except (ValueError, AttributeError) as exc:
            raise PipelineError(f"Invalid --pass-profile {value!r}; expected P=PROFILE.") from exc
        if number not in range(1, 6) or not name:
            raise PipelineError(f"Invalid --pass-profile {value!r}; pass must be 1..5.")
        result[number] = name
    return result


def _render_import(result, ui: Display) -> None:
    ui.message(
        f"Imported {result.narrative_sections} narrative sections and {result.translation_units} translation units. "
        f"Excluded {result.excluded_sections} front/back-matter section(s) from analysis/translation.\n"
        f"Extracted UTF-8 text: {result.project / 'extracted'}\nNext: analyze --project {result.project}"
    )
    if result.series_id is not None:
        ui.message("Inherited predecessor settings, project prompts and optional project model catalog.")
        ui.message(
            f"Series continuation: {result.series_id} volume {result.series_volume}; "
            f"inherited {result.inherited_terms} term(s) and {result.inherited_observations} observation(s)."
        )
    for warning in result.warnings:
        ui.message("NOTE: " + warning)


def _render_status(result, ui: Display) -> None:
    statuses = [chunk.status for chunk in result.chunks]
    series_line = f"Series: {result.series_id} | volume {result.series_volume}\n" if result.series_id else ""
    ui.message(
        f"Book: {result.title}\n{series_line}Narrative sections: {result.narrative_sections}\n"
        f"Translation units: {len(statuses)} | done: {statuses.count('done')} | "
        f"stale: {statuses.count('stale')} | pending: {statuses.count('pending')}\n"
        f"P1 complete: {result.analysis_complete}\nHuman approval: {result.approved}\n"
        f"Terms: {result.term_count} | retained candidates: {result.retained_candidates}\n"
        f"Readable output: {result.readable_output}"
    )
    if result.publication is not None:
        publication = result.publication
        ui.message(
            f"Translation complete: {publication.translation_complete}\n"
            f"Publication: {publication.state} | current: {publication.current}"
            + (f"\nPublished EPUB: {publication.output_path}" if publication.output_path else "")
            + (f"\nLast publish error: {publication.last_error}" if publication.last_error else "")
        )
    for chapter in result.chapters:
        ui.message(f"  {chapter.id}  {chapter.completed}/{chapter.total} units  {chapter.title[:100]}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project.resolve()
    try:
        with Display(args.quiet) as ui:
            app = create_application(
                ui, provider_factory=ProviderPool, store_factory=Store,
                plan_fingerprint_fn=plan_fingerprint,
            )
            if args.command == "import":
                _render_import(app.projects.import_book(ImportBookCommand(
                    **_model_options(args), source=args.folder, previous_volume=args.previous_volume,
                    opf=args.opf, input_encoding=args.input_encoding, chapter_mode=args.chapter_mode,
                    chapter_selector=args.chapter_selector, include_glob=args.include_glob,
                    sidecar_txt=args.sidecar_txt, whole_section_limit=args.whole_section_limit,
                )), ui)
            elif args.command == "analyze":
                app.pipeline.analyze(AnalyzeCommand(**_model_options(args)))
            elif args.command == "translate":
                result = app.pipeline.translate(TranslateCommand(**_model_options(args), chunk_limit=args.chunk_limit))
                if result.publication is not None:
                    if result.publication.current:
                        ui.message(f"Published EPUB: {result.publication.output_path}")
                    elif result.publication.last_error:
                        ui.message(
                            "Translation completed, but EPUB publishing failed. "
                            f"Retry with `publish --project {root}`.\n{result.publication.last_error}"
                        )
            elif args.command == "review":
                with app.review.open_session(ReviewSessionCommand(root)) as session:
                    ui.stop_progress()
                    run_review_server(session, args.bind, args.review_port, not args.no_browser, ui)
            elif args.command == "reader":
                with app.reader.open_session(ReaderSessionCommand(root)) as session:
                    ui.stop_progress()
                    run_reader_server(session, args.bind, args.reader_port, not args.no_browser, ui)
            elif args.command == "approve":
                result = app.review.approve(ApproveCommand(root, args.accept_defaults))
                ui.message(
                    f"Approved {result.approved_terms} terms. {result.stale_chunks} previously completed chunk(s) "
                    f"marked stale. Existing output was preserved.\nNext: translate --project {root} --continue 5"
                )
            elif args.command == "status":
                _render_status(app.projects.status(StatusCommand(root)), ui)
            elif args.command == "export":
                result = app.exports.export_text(ExportCommand(root, args.encoding, args.output))
                ui.message(f"Exported {args.output} ({args.encoding}, strict)." if result.copy_output
                           else f"Exported {result.internal_output} (UTF-8).")
            elif args.command == "publish":
                result = app.publishing.publish(PublishCommand(root, args.target_language))
                ui.message(
                    f"Published {result.status.output_path}."
                    if result.built else f"Publication already current: {result.status.output_path}."
                )
            else:
                if args.command == "profiles":
                    report = app.operations.profiles(ProfilesCommand(root))
                elif args.command == "doctor":
                    report = app.operations.doctor(DoctorCommand(root))
                elif args.command == "attempts":
                    report = app.operations.attempts(AttemptsCommand(root, args.attempt))
                elif args.command == "usage":
                    report = app.operations.usage(UsageCommand(root))
                elif args.command == "catalog-import":
                    report = app.operations.import_catalog(CatalogImportCommand(root, args.file))
                elif args.command == "discover":
                    report = app.operations.discover(DiscoverCommand(root, args.profile, args.pass_no))
                else:
                    report = app.operations.smoke(SmokeCommand(root, args.live, args.profile, args.pass_no))
                print(json.dumps(report.value, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Completed stages remain saved; rerun the same command. Only the interrupted request must restart.", file=sys.stderr)
        return 130
    except (PipelineError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
