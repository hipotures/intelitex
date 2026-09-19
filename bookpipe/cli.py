from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from .client import Client
from .engine import analyze, translate, export_text
from .importer import import_folder
from .review import run_review_server
from .store import Store
from .ui import Display
from .util import PipelineError, atomic_json, atomic_text, digest, project_lock, read_json, plan_fingerprint

BUNDLE = Path(__file__).resolve().parent.parent


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Persistent five-pass literary translation. Import -> analyze -> human review -> approve -> translate.")
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("import", "Import an unpacked EPUB/HTML folder; plan chapters and chunks without translating."),
        ("analyze", "P1 over the whole book with cumulative memory; then stop for review."),
        ("review", "Open the local terminology review application backed by terms.review.json."),
        ("approve", "Commit human choices. No model call. Required before translation."),
        ("translate", "Run P2-P5 for the next N unfinished chunks; resume checkpoints automatically."),
        ("status", "Show local progress without contacting the server."),
        ("export", "Rebuild completed text; optionally produce a strict legacy-encoding copy."),
    ):
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
        if name == "import":
            s.add_argument("folder", type=Path)
            s.add_argument("--opf", type=Path, help="Select an OPF package if several exist.")
            s.add_argument("--input-encoding", help="Explicit HTML decoding override; default reads declared encoding/BOM.")
            s.add_argument("--chapter-mode", choices=["auto", "file", "headings"], default="auto")
            s.add_argument("--chapter-selector", help="Optional CSS selector for nonstandard chapter headings, e.g. p.chapter.")
            s.add_argument("--include", dest="include_glob", help="Optional path glob, e.g. '*split_*.html'.")
            s.add_argument("--sidecar-txt", action="store_true", help="Also write UTF-8 .txt beside input HTML; never overwrite different text.")
            s.add_argument("--whole-section-limit", type=int, default=10000,
                           help="Keep a section intact at or below this visible-text character count; larger sections split only at natural scene boundaries. Default: 10000.")
        if name == "review":
            s.add_argument("--bind", default="127.0.0.1", help="Review web server bind address. Default: 127.0.0.1.")
            s.add_argument("--review-port", type=int, default=8765, help="Review web server port; 0 chooses a free port. Default: 8765.")
            s.add_argument("--no-browser", action="store_true", help="Do not open the review UI in the default browser.")
        if name == "translate":
            s.add_argument("--continue", dest="chunk_limit", type=int, default=5, metavar="N", help="Next N unfinished chunks; 0 means all. Default: 5.")
        if name == "approve":
            s.add_argument("--accept-defaults", action="store_true", help="Explicitly approve the current selected/custom values without confirmed=true.")
        if name == "export":
            s.add_argument("--encoding", default="utf-8", help="Encoding for an additional copy; internal state always stays UTF-8.")
            s.add_argument("--output", type=Path, help="Required for non-UTF-8 export.")
    return p


def effective_settings(root: Path, args) -> dict:
    settings = read_json(root / "settings.json") if (root / "settings.json").exists() else read_json(BUNDLE / "settings.default.json")
    for key in ("host", "port", "model", "context_size", "thinking"):
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    for field in ("whole_section_char_limit", "memory_tokens", "request_timeout"):
        if settings[field] <= 0:
            raise PipelineError(f"{field} must be positive.")
    if settings.get("context_size") is not None and settings["context_size"] <= 0:
        raise PipelineError("context_size must be positive or null for auto-detection.")
    for number, cfg in settings["passes"].items():
        if cfg["max_tokens"] <= 0 or not 0 <= cfg["temperature"] <= 2:
            raise PipelineError(f"Invalid settings for pass {number}.")
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
    ui.message(f"Book: {book['metadata'].get('title') or Path(book['source_root']).name}\n"
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
        if args.command != "import" and not (root / "book.json").exists():
            raise PipelineError("Project not imported. Run import first.")
        with project_lock(root), Display(args.quiet) as ui:
            if args.command == "import":
                if (root / "book.json").exists():
                    raise PipelineError("Project already imported. Use analyze/status/translate, not import again.")
                if (root / "state.sqlite3").exists():
                    raise PipelineError("Incomplete or unrelated project database found. Use a new project directory.")
                if root.is_relative_to(args.folder.resolve()):
                    raise PipelineError("Keep the project outside the input folder to avoid importing generated files.")
                settings = effective_settings(root, args)
                settings["whole_section_char_limit"] = args.whole_section_limit
                if args.whole_section_limit <= 0:
                    raise PipelineError("--whole-section-limit must be positive.")
                client = Client(settings, ui)
                client.discover()
                opf = args.opf.resolve() if args.opf else None
                book = import_folder(args.folder, root, client.count, settings, ui, opf=opf,
                    encoding=args.input_encoding, chapter_mode=args.chapter_mode,
                    chapter_selector=args.chapter_selector, sidecars=args.sidecar_txt, include_glob=args.include_glob)
                book["model_identity"] = client.identity
                book["content_fingerprint"] = plan_fingerprint(book)
                book["planning_settings"] = {"whole_section_char_limit": settings["whole_section_char_limit"]}
                atomic_json(root / "settings.json", settings)
                prompts = root / "prompts"
                prompts.mkdir(exist_ok=True)
                for prompt in (BUNDLE / "prompts").glob("*.txt"):
                    shutil.copy2(prompt, prompts / prompt.name)
                store = Store(root)
                store.register_chunks(book)
                # book.json is the completed-import marker, written last.
                atomic_json(root / "book.json", book)
                matter = len(book.get("non_narrative_sections", []))
                ui.message(f"Imported {len(book['chapters'])} narrative sections and {len(book['chunks'])} translation units. "
                           f"Excluded {matter} front/back-matter section(s) from analysis/translation.\n"
                           f"Extracted UTF-8 text: {root / 'extracted'}\nNext: analyze --project {root}")
                for warning in book["warnings"]:
                    ui.message("NOTE: " + warning)
                return 0

            store = Store(root)
            book = read_json(root / "book.json")
            if book.get("content_fingerprint") != plan_fingerprint(book):
                raise PipelineError("The frozen source/chunk manifest was modified. Restore book.json or import into a new project. Only titles and thread_id may be edited in place.")
            if args.command in {"analyze", "translate"}:
                settings = effective_settings(root, args)
                client = Client(settings, ui)
                client.discover()
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
