from __future__ import annotations

import argparse
import copy
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from .application import (
    AnalyzeCommand, ApproveCommand, AttemptsCommand, CatalogImportCommand, DiscoverCommand,
    DoctorCommand, ExportCommand, ImportBookCommand, ProfilesCommand, ReaderSessionCommand,
    ReviewSessionCommand, SmokeCommand, StatusCommand, TranslateCommand, UsageCommand,
)
from .bootstrap import create_application

from .catalog import import_catalog, load_catalog, pricing_snapshot
from .contracts import preflight_measurement, preflight_metadata
from .evidence import AttemptRecorder
from .engine import analyze, translate, export_text
from .importer import import_folder
from .reader import run_reader_server
from .review import run_review_server
from .operations import attempt_report, doctor_report, print_json, profile_report, usage_report
from .profiles import migrate_settings_file, validate_profiles, with_builtin_profiles
from .provider_registry import ProviderPool
from .project_config import materialize_configuration
from .series import continuation_metadata, prepare_handoff, validate_series_metadata
from .store import Store
from .ui import Display
from .util import PipelineError, atomic_json, atomic_text, digest, project_lock, reader_lock, read_json, plan_fingerprint

BUNDLE = Path(__file__).resolve().parent.parent


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Persistent five-pass literary translation. Import -> analyze -> human review -> approve -> translate.")
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
    )
    for name, help_text in pipeline_commands:
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--project", type=Path, required=True)
        s.add_argument("--quiet", action="store_true", help="Disable progress display.")
        if name in {"import", "analyze", "translate"}:
            s.add_argument("--host", help="IP address or http(s) URL; saved during import.")
            s.add_argument("--port", type=int)
            s.add_argument("--model", help="Model ID from /v1/models.")
            s.add_argument("--context-size", type=int, help="Optional context ceiling; never exceeds detected server capacity.")
            s.add_argument("--thinking", choices=["off", "analysis"], help="off is default; analysis enables model thinking in P1/P2/P4.")
            s.add_argument("--allow-model-change", action="store_true", help="Explicitly permit a different model; preserve the existing chunk manifest/checkpoints.")
            s.add_argument("--profile", help="Use one named profile for this command without saving it.")
            s.add_argument("--pass-profile", action="append", default=[], metavar="P=PROFILE",
                           help="Override a pass profile for this command; repeatable.")
        if name == "import":
            s.add_argument("folder", type=Path)
            s.add_argument("--previous-volume", type=Path,
                           help="Seed this import from the immediately preceding approved Intelitex project.")
            s.add_argument("--opf", type=Path, help="Select an OPF package if several exist.")
            s.add_argument("--input-encoding", help="Explicit HTML decoding override; default reads declared encoding/BOM.")
            s.add_argument("--chapter-mode", choices=["auto", "file", "headings"], default="auto")
            s.add_argument("--chapter-selector", help="Optional CSS selector for nonstandard chapter headings, e.g. p.chapter.")
            s.add_argument("--include", dest="include_glob", help="Optional path glob, e.g. '*split_*.html'.")
            s.add_argument("--sidecar-txt", action="store_true", help="Also write UTF-8 .txt beside input HTML; never overwrite different text.")
            s.add_argument("--whole-section-limit", type=int, default=None,
                           help="Keep a section intact at or below this visible-text character count; larger sections split only at natural scene boundaries. Default: inherited for continuations, otherwise 10000.")
        if name == "review":
            s.add_argument("--bind", default="127.0.0.1", help="Review web server bind address. Default: 127.0.0.1.")
            s.add_argument("--review-port", type=int, default=8765, help="Review web server port; 0 chooses a free port. Default: 8765.")
            s.add_argument("--no-browser", action="store_true", help="Do not open the review UI in the default browser.")
        if name == "reader":
            s.add_argument("--bind", default="127.0.0.1", help="Reader web server bind address. Default: 127.0.0.1.")
            s.add_argument("--reader-port", type=int, default=8766, help="Reader web server port; 0 chooses a free port. Default: 8766.")
            s.add_argument("--no-browser", action="store_true", help="Do not open the Reader in the default browser.")
        if name == "translate":
            s.add_argument("--continue", dest="chunk_limit", type=int, default=5, metavar="N", help="Next N unfinished chunks; 0 means all. Default: 5.")
        if name == "approve":
            s.add_argument("--accept-defaults", action="store_true", help="Explicitly approve the current selected/custom values without confirmed=true.")
        if name == "export":
            s.add_argument("--encoding", default="utf-8", help="Encoding for an additional copy; internal state always stays UTF-8.")
            s.add_argument("--output", type=Path, help="Required for non-UTF-8 export.")
    for name, help_text in (
        ("profiles", "Show configured and resolved profiles without exposing credentials."),
        ("doctor", "Validate local configuration and Codex protocol without a model turn."),
        ("attempts", "Inspect retained attempt evidence offline."),
        ("usage", "Report retained token accounting offline."),
        ("catalog-import", "Validate and atomically activate a model/pricing catalog."),
        ("discover", "Explicitly query a selected provider's live model metadata."),
        ("smoke", "Run one explicitly authorized live structured-output request."),
    ):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--project", type=Path, required=True)
        s.add_argument("--quiet", action="store_true")
        if name == "attempts":
            s.add_argument("--attempt", help="Project-relative attempt directory; omit to list all.")
        if name == "catalog-import":
            s.add_argument("file", type=Path)
        if name in {"discover", "smoke"}:
            s.add_argument("--profile")
            s.add_argument("--pass", dest="pass_no", type=int, choices=range(1, 6), default=1)
        if name == "smoke":
            s.add_argument("--live", action="store_true", help="Required acknowledgement that this command may spend provider credit/quota.")
    return p


def parse_pass_profiles(values: list[str]) -> dict[int, str]:
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


def effective_settings(root: Path, args, *, base: dict | None = None) -> dict:
    installed = (root / "settings.json").exists()
    if base is not None:
        settings = copy.deepcopy(base)
    else:
        settings = read_json(root / "settings.json") if installed else read_json(BUNDLE / "settings.default.json")
        if installed:
            settings, _ = migrate_settings_file(root, settings)
    override_profile = settings.get("profiles", {}).get(settings.get("default_profile"))
    if base is not None:
        # A continuation may inherit a non-default P1 assignment. Apply explicit
        # import overrides to the profile actually selected for this import.
        assignments = parse_pass_profiles(args.pass_profile)
        selected = (assignments.get(1) or args.profile or settings.get("pass_profiles", {}).get("1")
                    or settings["default_profile"])
        override_profile = settings["profiles"].get(selected)
        if override_profile is None:
            override_profile = with_builtin_profiles(settings)["profiles"].get(selected)
            if override_profile is not None:
                # Import-time scalar overrides must affect the selected profile
                # passed to ProviderPool. Persisting this one explicitly selected
                # built-in also preserves those overrides in the new project.
                settings["profiles"][selected] = override_profile
        if override_profile is None:
            raise PipelineError(f"Unknown profile {selected!r}.")
    for key in ("host", "port", "model", "context_size", "thinking"):
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
            if base is not None and key in {"model", "context_size"}:
                override_profile[key] = value
            elif override_profile and override_profile.get("provider") == "llamacpp":
                if key == "host":
                    override_profile["endpoint"] = value
                elif key == "port":
                    override_profile.setdefault("options", {})["port"] = value
                elif key == "thinking":
                    override_profile.setdefault("options", {})["thinking"] = value
                elif key in {"model", "context_size"}:
                    override_profile[key] = value
    for field in ("whole_section_char_limit", "memory_tokens", "request_timeout"):
        if settings[field] <= 0:
            raise PipelineError(f"{field} must be positive.")
    if settings.get("context_size") is not None and settings["context_size"] <= 0:
        raise PipelineError("context_size must be positive or null for auto-detection.")
    for number, cfg in settings["passes"].items():
        if cfg["max_tokens"] <= 0 or not 0 <= cfg["temperature"] <= 2:
            raise PipelineError(f"Invalid settings for pass {number}.")
    validate_profiles(settings, root if installed else None)
    return settings


def check_model(client: Client, book: dict, allowed: bool, ui: Display):
    old = book.get("model_identity", {})
    if old and digest(old) != digest(client.identity):
        if not allowed:
            raise PipelineError("The server model differs from the import model. Use --allow-model-change intentionally. Existing chunk boundaries/checkpoints will be preserved.")
        ui.message("WARNING: different model. Fixed chunk boundaries remain; per-request token counts are recalculated. Old successful outputs are not replaced.")


def local_status(store: Store, book: dict, ui: Display):
    statuses = [store.chunk(c["id"])["status"] for c in book["chunks"]]
    terms = store.terms()
    series_line = ""
    series_path = store.root / "series.json"
    if series_path.is_file():
        series = validate_series_metadata(store.root, book["source_fingerprint"], read_json(series_path))
        series_line = f"Series: {series['series_id']} | volume {series['volume']}\n"
    ui.message(f"Book: {book['metadata'].get('title') or Path(book['source_root']).name}\n"
               f"{series_line}"
               f"Narrative sections: {len(book['chapters'])}\nTranslation units: {len(statuses)} | done: {statuses.count('done')} | stale: {statuses.count('stale')} | pending: {statuses.count('pending')}\n"
               f"P1 complete: {bool(store.get('analysis_done'))}\nHuman approval: {bool(store.get('approved'))}\n"
               f"Terms: {len(terms)} | retained candidates: {sum(len(t['candidates']) for t in terms)}\n"
               f"Readable output: {store.root / 'translation.txt'}")
    for chapter in book["chapters"]:
        n = sum(store.chunk(cid)["status"] == "done" for cid in chapter["chunk_ids"])
        ui.message(f"  {chapter['id']}  {n}/{len(chapter['chunk_ids'])} units  {chapter['title'][:100]}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project.resolve()
    store = client = None
    try:
        extracted = {"import", "analyze", "review", "reader", "approve", "translate", "status", "export", "profiles", "doctor", "attempts", "usage",
                     "catalog-import", "discover", "smoke"}
        if args.command in extracted:
            with Display(args.quiet) as ui:
                app = create_application(
                    ui, provider_factory=ProviderPool, store_factory=Store,
                    plan_fingerprint_fn=plan_fingerprint,
                )
                if args.command == "import":
                    result = app.projects.import_book(ImportBookCommand(
                        project=root, source=args.folder, previous_volume=args.previous_volume,
                        opf=args.opf, input_encoding=args.input_encoding, chapter_mode=args.chapter_mode,
                        chapter_selector=args.chapter_selector, include_glob=args.include_glob,
                        sidecar_txt=args.sidecar_txt, whole_section_limit=args.whole_section_limit,
                        host=args.host, port=args.port, model=args.model, context_size=args.context_size,
                        thinking=args.thinking, allow_model_change=args.allow_model_change,
                        profile=args.profile, pass_profiles=tuple(args.pass_profile),
                    ))
                    ui.message(
                        f"Imported {result.narrative_sections} narrative sections and "
                        f"{result.translation_units} translation units. Excluded {result.excluded_sections} "
                        f"front/back-matter section(s) from analysis/translation.\n"
                        f"Extracted UTF-8 text: {root / 'extracted'}\nNext: analyze --project {root}"
                    )
                    if result.series_id is not None:
                        ui.message("Inherited predecessor settings, project prompts and optional project model catalog.")
                        ui.message(
                            f"Series continuation: {result.series_id} volume {result.series_volume}; "
                            f"inherited {result.inherited_terms} term(s) and "
                            f"{result.inherited_observations} observation(s)."
                        )
                    for warning in result.warnings:
                        ui.message("NOTE: " + warning)
                elif args.command in {"analyze", "translate"}:
                    common = dict(
                        project=root, host=args.host, port=args.port, model=args.model,
                        context_size=args.context_size, thinking=args.thinking,
                        allow_model_change=args.allow_model_change, profile=args.profile,
                        pass_profiles=tuple(args.pass_profile),
                    )
                    if args.command == "analyze":
                        app.pipeline.analyze(AnalyzeCommand(**common))
                    else:
                        app.pipeline.translate(TranslateCommand(**common, chunk_limit=args.chunk_limit))
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
                        f"Approved {result.approved_terms} terms. {result.stale_chunks} previously completed "
                        f"chunk(s) marked stale. Existing output was preserved.\n"
                        f"Next: translate --project {root} --continue 5"
                    )
                elif args.command == "status":
                    result = app.projects.status(StatusCommand(root))
                    statuses = [chunk.status for chunk in result.chunks]
                    series_line = (f"Series: {result.series_id} | volume {result.series_volume}\n"
                                   if result.series_id else "")
                    ui.message(
                        f"Book: {result.title}\n{series_line}"
                        f"Narrative sections: {result.narrative_sections}\n"
                        f"Translation units: {len(statuses)} | done: {statuses.count('done')} | "
                        f"stale: {statuses.count('stale')} | pending: {statuses.count('pending')}\n"
                        f"P1 complete: {result.analysis_complete}\nHuman approval: {result.approved}\n"
                        f"Terms: {result.term_count} | retained candidates: {result.retained_candidates}\n"
                        f"Readable output: {result.readable_output}"
                    )
                    for chapter in result.chapters:
                        ui.message(f"  {chapter.id}  {chapter.completed}/{chapter.total} units  {chapter.title[:100]}")
                elif args.command == "export":
                    result = app.exports.export_text(ExportCommand(root, args.encoding, args.output))
                    if result.copy_output:
                        ui.message(f"Exported {args.output} ({args.encoding}, strict).")
                    else:
                        ui.message(f"Exported {result.internal_output} (UTF-8).")
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
                    print_json(report.value)
            return 0
        if args.command != "import" and not (root / "book.json").exists():
            raise PipelineError("Project not imported. Run import first.")
        command_lock = reader_lock(root) if args.command == "reader" else project_lock(root)
        with command_lock, Display(args.quiet) as ui:
            if args.command in {"profiles", "doctor", "attempts", "usage", "catalog-import", "discover", "smoke"}:
                settings = effective_settings(root, args)
                if args.command == "profiles":
                    print_json(profile_report(settings, root))
                elif args.command == "doctor":
                    print_json(doctor_report(settings, root))
                elif args.command == "attempts":
                    print_json(attempt_report(root, args.attempt))
                elif args.command == "usage":
                    print_json(usage_report(root))
                elif args.command == "catalog-import":
                    print_json(import_catalog(args.file, root))
                elif args.command == "discover":
                    client = ProviderPool(settings, ui, root, command_profile=args.profile)
                    print_json(client.discover(args.pass_no))
                else:
                    if not args.live:
                        raise PipelineError("smoke requires --live because it starts a billable/model turn.")
                    client = ProviderPool(settings, ui, root, command_profile=args.profile)
                    provider = client.for_pass(args.pass_no)
                    stamp = str(time.time_ns())
                    attempt = root / "artifacts" / "smoke" / stamp / "attempt_001"
                    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                              "required": ["ok"], "additionalProperties": False}
                    prompt = "Return the requested JSON object and nothing else."
                    inputs = {"REQUEST": "Set ok to true."}
                    recorder = AttemptRecorder(attempt, {"operation": "live_smoke", "provider": provider.provider,
                                                         "profile": provider.profile_name, "requested_model": provider.model})
                    recorder.semantic({"trusted_instructions": prompt, "input_payload": inputs,
                                       "output_schema": schema, "resolved_profile": provider.resolved_profile}, schema)
                    catalog, catalog_path = load_catalog(root)
                    recorder.pricing(pricing_snapshot(catalog, catalog_path, provider.provider, provider.model))
                    try:
                        if provider.provider == "llamacpp" and not provider.identity.get("id"):
                            recorder.event("outbound", "provider_discovery", {"provider": "llamacpp"})
                            recorder.event("inbound", "provider_discovery", provider.discover())
                        body = provider.body(prompt, inputs, schema, args.pass_no)
                        count = provider.preflight(body, recorder)
                        measurement_meta = preflight_metadata(preflight_measurement(provider, count))
                        answer, metadata = provider.generate(body, attempt, recorder)
                        if json.loads(answer) != {"ok": True}:
                            raise PipelineError("Smoke response did not match the requested structured value.")
                        recorder.finish(generation="completed", validation="passed",
                                        metadata={**metadata, **measurement_meta})
                        print_json({"status": "passed", "attempt": str(attempt.relative_to(root)), **metadata})
                    except BaseException as exc:
                        recorder.finish(generation="failed", validation="failed", metadata={"status": "failed"},
                                        error={"type": type(exc).__name__, "message": str(exc)})
                        raise
                return 0
            if args.command == "import":
                if (root / "book.json").exists():
                    raise PipelineError("Project already imported. Use analyze/status/translate, not import again.")
                if (root / "state.sqlite3").exists():
                    raise PipelineError("Incomplete or unrelated project database found. Use a new project directory.")
                if root.is_relative_to(args.folder.resolve()):
                    raise PipelineError("Keep the project outside the input folder to avoid importing generated files.")
                handoff = (prepare_handoff(args.previous_volume, root, new_source=args.folder.resolve())
                           if args.previous_volume else None)
                configuration = handoff["configuration"] if handoff else None
                settings = effective_settings(root, args, base=configuration["settings"] if configuration else None)
                if args.whole_section_limit is not None:
                    settings["whole_section_char_limit"] = args.whole_section_limit
                elif not handoff:
                    settings["whole_section_char_limit"] = 10000  # Preserve standalone import behavior.
                if settings["whole_section_char_limit"] <= 0:
                    raise PipelineError("--whole-section-limit must be positive.")
                if configuration:
                    materialize_configuration(root, configuration, settings)
                client = ProviderPool(settings, ui, root, command_profile=args.profile,
                                      command_pass_profiles=parse_pass_profiles(args.pass_profile))
                client.discover(1)
                opf = args.opf.resolve() if args.opf else None
                book = import_folder(args.folder, root, client.count, settings, ui, opf=opf,
                    encoding=args.input_encoding, chapter_mode=args.chapter_mode,
                    chapter_selector=args.chapter_selector, sidecars=args.sidecar_txt, include_glob=args.include_glob)
                book["model_identity"] = client.identity
                book["tokenizer_identity"] = client.tokenizer_identity
                for chapter in book["chapters"]:
                    chapter["source_tokens_tokenizer"] = client.tokenizer_identity
                book["content_fingerprint"] = plan_fingerprint(book)
                book["planning_settings"] = {"whole_section_char_limit": settings["whole_section_char_limit"]}
                if not configuration:
                    atomic_json(root / "settings.json", settings)
                    prompts = root / "prompts"
                    prompts.mkdir(exist_ok=True)
                    for prompt in (BUNDLE / "prompts").glob("*.txt"):
                        shutil.copy2(prompt, prompts / prompt.name)
                store = Store(root)
                store.register_chunks(book)
                if handoff:
                    seed_path = root / "series.seed.json"
                    atomic_json(seed_path, handoff["seed"])
                    store.seed_series(handoff["seed"])
                    series = continuation_metadata(book["source_fingerprint"], handoff, digest(seed_path.read_bytes()))
                    atomic_json(root / "series.json", series)
                # book.json is the completed-import marker, written last.
                atomic_json(root / "book.json", book)
                matter = len(book.get("non_narrative_sections", []))
                ui.message(f"Imported {len(book['chapters'])} narrative sections and {len(book['chunks'])} translation units. "
                           f"Excluded {matter} front/back-matter section(s) from analysis/translation.\n"
                           f"Extracted UTF-8 text: {root / 'extracted'}\nNext: analyze --project {root}")
                if handoff:
                    ui.message("Inherited predecessor settings, project prompts and optional project model catalog.")
                    ui.message(f"Series continuation: {handoff['series_id']} volume {handoff['previous_volume'] + 1}; "
                               f"inherited {len(handoff['seed']['terms'])} term(s) and "
                               f"{len(handoff['seed']['observations'])} observation(s).")
                for warning in book["warnings"]:
                    ui.message("NOTE: " + warning)
                return 0

            book = read_json(root / "book.json")
            if book.get("content_fingerprint") != plan_fingerprint(book):
                raise PipelineError("The frozen source/chunk manifest was modified. Restore book.json or import into a new project. Only titles and thread_id may be edited in place.")
            if args.command == "reader":
                ui.stop_progress()
                run_reader_server(root, args.bind, args.reader_port, not args.no_browser, ui)
                return 0
            store = Store(root)
            if args.command in {"analyze", "translate"}:
                settings = effective_settings(root, args)
                client = ProviderPool(settings, ui, root, command_profile=args.profile,
                                      command_pass_profiles=parse_pass_profiles(args.pass_profile))
                first_pass = 1 if args.command == "analyze" else 2
                client.planning_pass = first_pass
                selected = client.for_pass(first_pass)
                if getattr(selected, "provider", "llamacpp") == "llamacpp":
                    client.discover(first_pass)
                else:
                    client.identity = {"provider": selected.provider, "requested_model": selected.model,
                                       "profile": selected.profile_name}
                check_model(client, book, args.allow_model_change, ui)
                if args.command == "analyze":
                    analyze(store, book, client, settings, ui)
                else:
                    translate(store, book, client, settings, ui, args.chunk_limit)
            elif args.command == "review":
                if not store.get("analysis_done"):
                    raise PipelineError("Analysis has not finished; run analyze to resume it.")
                path = store.write_review(book["source_fingerprint"])
                ui.stop_progress()
                run_review_server(path, args.bind, args.review_port, not args.no_browser, ui)
            elif args.command == "approve":
                if not store.get("analysis_done"):
                    raise PipelineError("Finish analysis before approving terminology.")
                count, stale = store.approve(book["source_fingerprint"], args.accept_defaults)
                ui.message(f"Approved {count} terms. {stale} previously completed chunk(s) marked stale. Existing output was preserved.\nNext: translate --project {root} --continue 5")
            elif args.command == "status":
                local_status(store, book, ui)
            elif args.command == "export":
                export_text(store, book)
                if args.output:
                    destination = args.output.resolve()
                    if destination.is_relative_to(root) and not destination.is_relative_to(root / "exports"):
                        raise PipelineError("Additional exports inside a project must be placed under its exports/ directory; internal files are protected.")
                    text = (root / "translation.txt").read_text(encoding="utf-8")
                    try:
                        raw = text.encode(args.encoding, errors="strict")
                    except (UnicodeError, LookupError) as exc:
                        raise PipelineError(f"Cannot export to {args.encoding} without loss: {exc}. UTF-8 originals remain unchanged.") from exc
                    if args.output.resolve().is_relative_to(root) and args.output.resolve() == root / "translation.txt" and args.encoding.lower() not in {"utf-8", "utf8"}:
                        raise PipelineError("Do not overwrite the internal UTF-8 translation with a legacy encoding.")
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    # Additional exports are atomically replaced too.
                    from tempfile import NamedTemporaryFile
                    import os
                    with NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
                        name = handle.name
                        handle.write(raw)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(name, args.output)
                    ui.message(f"Exported {args.output} ({args.encoding}, strict).")
                elif args.encoding.lower() not in {"utf-8", "utf8"}:
                    raise PipelineError("Supply --output for a legacy-encoding copy.")
                else:
                    ui.message(f"Exported {root / 'translation.txt'} (UTF-8).")
            return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Completed stages remain saved; rerun the same command. Only the interrupted request must restart.", file=sys.stderr)
        return 130
    except (PipelineError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()
        if store:
            store.close()
