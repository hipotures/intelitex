"""Project import, configuration and status use cases."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

from ..importer import import_folder
from ..profiles import migrate_settings_file, validate_profiles, with_builtin_profiles
from ..project_config import materialize_configuration
from ..series import continuation_metadata, prepare_handoff, validate_series_metadata
from ..util import PipelineError, atomic_json, digest, plan_fingerprint, read_json
from .commands import ImportBookCommand, ModelOptions, StatusCommand
from .ports import ApplicationDependencies, ProgressSink
from .results import ChapterStatus, ChunkStatus, ImportResult, StatusResult
from .sessions import OperationScope


def parse_pass_profiles(values: tuple[str, ...] | list[str]) -> dict[int, str]:
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


def effective_settings(bundle: Path, root: Path, options: ModelOptions | None = None,
                       *, base: dict | None = None) -> dict:
    options = options or ModelOptions()
    installed = (root / "settings.json").exists()
    if base is not None:
        settings = copy.deepcopy(base)
    else:
        settings = read_json(root / "settings.json") if installed else read_json(bundle / "settings.default.json")
        if installed:
            settings, _ = migrate_settings_file(root, settings)
    override_profile = settings.get("profiles", {}).get(settings.get("default_profile"))
    if base is not None:
        assignments = parse_pass_profiles(options.pass_profiles)
        selected = (assignments.get(1) or options.profile or settings.get("pass_profiles", {}).get("1")
                    or settings["default_profile"])
        override_profile = settings["profiles"].get(selected)
        if override_profile is None:
            override_profile = with_builtin_profiles(settings)["profiles"].get(selected)
            if override_profile is not None:
                settings["profiles"][selected] = override_profile
        if override_profile is None:
            raise PipelineError(f"Unknown profile {selected!r}.")
    for key in ("host", "port", "model", "context_size", "thinking"):
        value = getattr(options, key, None)
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


def load_valid_book(root: Path, fingerprint=plan_fingerprint) -> dict:
    if not (root / "book.json").exists():
        raise PipelineError("Project not imported. Run import first.")
    book = read_json(root / "book.json")
    if book.get("content_fingerprint") != fingerprint(book):
        raise PipelineError(
            "The frozen source/chunk manifest was modified. Restore book.json or import into a new project. "
            "Only titles and thread_id may be edited in place."
        )
    return book


class ProjectsService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies, self.progress = dependencies, progress

    def import_book(self, command: ImportBookCommand) -> ImportResult:
        root, source = command.project.resolve(), command.source.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            if (root / "book.json").exists():
                raise PipelineError("Project already imported. Use analyze/status/translate, not import again.")
            if (root / "state.sqlite3").exists():
                raise PipelineError("Incomplete or unrelated project database found. Use a new project directory.")
            if root.is_relative_to(source):
                raise PipelineError("Keep the project outside the input folder to avoid importing generated files.")
            handoff = (prepare_handoff(command.previous_volume, root, new_source=source)
                       if command.previous_volume else None)
            configuration = handoff["configuration"] if handoff else None
            settings = effective_settings(
                self.dependencies.bundle, root, command,
                base=configuration["settings"] if configuration else None,
            )
            if command.whole_section_limit is not None:
                settings["whole_section_char_limit"] = command.whole_section_limit
            elif not handoff:
                settings["whole_section_char_limit"] = 10000
            if settings["whole_section_char_limit"] <= 0:
                raise PipelineError("--whole-section-limit must be positive.")
            if configuration:
                materialize_configuration(root, configuration, settings)
            client = scope.providers(
                settings, profile=command.profile,
                pass_profiles=parse_pass_profiles(command.pass_profiles),
            )
            client.discover(1)
            book = import_folder(
                source, root, client.count, settings, self.progress,
                opf=command.opf.resolve() if command.opf else None,
                encoding=command.input_encoding, chapter_mode=command.chapter_mode,
                chapter_selector=command.chapter_selector, sidecars=command.sidecar_txt,
                include_glob=command.include_glob,
            )
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
                for prompt in (self.dependencies.bundle / "prompts").glob("*.txt"):
                    shutil.copy2(prompt, prompts / prompt.name)
            store = scope.store
            store.register_chunks(book)
            series_id = None
            series_volume = None
            inherited_terms = inherited_observations = 0
            if handoff:
                seed_path = root / "series.seed.json"
                atomic_json(seed_path, handoff["seed"])
                store.seed_series(handoff["seed"])
                series = continuation_metadata(book["source_fingerprint"], handoff, digest(seed_path.read_bytes()))
                atomic_json(root / "series.json", series)
                series_id = handoff["series_id"]
                series_volume = handoff["previous_volume"] + 1
                inherited_terms = len(handoff["seed"]["terms"])
                inherited_observations = len(handoff["seed"]["observations"])
            # Completed-import marker is deliberately last.
            atomic_json(root / "book.json", book)
            return ImportResult(
                project=root, narrative_sections=len(book["chapters"]),
                translation_units=len(book["chunks"]),
                excluded_sections=len(book.get("non_narrative_sections", [])),
                warnings=tuple(book["warnings"]), series_id=series_id,
                series_volume=series_volume, inherited_terms=inherited_terms,
                inherited_observations=inherited_observations,
            )

    def status(self, command: StatusCommand) -> StatusResult:
        root = command.project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint)
            store = scope.store
            chunks = tuple(ChunkStatus(c["id"], store.chunk(c["id"])["status"]) for c in book["chunks"])
            terms = store.terms()
            series_id = series_volume = None
            series_path = root / "series.json"
            if series_path.is_file():
                series = validate_series_metadata(root, book["source_fingerprint"], read_json(series_path))
                series_id, series_volume = series["series_id"], series["volume"]
            chapters = tuple(ChapterStatus(
                id=chapter["id"], title=chapter["title"],
                completed=sum(store.chunk(cid)["status"] == "done" for cid in chapter["chunk_ids"]),
                total=len(chapter["chunk_ids"]),
            ) for chapter in book["chapters"])
            return StatusResult(
                project=root, title=book["metadata"].get("title") or Path(book["source_root"]).name,
                series_id=series_id, series_volume=series_volume,
                narrative_sections=len(book["chapters"]), chunks=chunks,
                analysis_complete=bool(store.get("analysis_done")), approved=bool(store.get("approved")),
                term_count=len(terms), retained_candidates=sum(len(t["candidates"]) for t in terms),
                chapters=chapters, readable_output=root / "translation.txt",
            )
