"""Project import, configuration and status use cases."""
from __future__ import annotations

import copy
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ..importer import estimate_source_tokens, import_folder
from ..profiles import migrate_settings_file, resolve_profile, validate_profiles, with_builtin_profiles
from ..project_config import materialize_configuration
from ..series import continuation_metadata, prepare_handoff, validate_series_metadata
from ..util import PipelineError, atomic_json, digest, plan_fingerprint, read_json
from .commands import ImportBookCommand, ModelOptions, StatusCommand
from .ports import ApplicationDependencies, ProgressSink
from .results import ChapterStatus, ChunkStatus, ImportResult, StatusResult
from .sessions import OperationScope, ProjectReadScope
from .epub_sources import unpack_epub


def validate_pass_profiles(values: Mapping[int, str]) -> dict[int, str]:
    """Validate the semantic per-pass profile mapping supplied by an adapter."""
    if not isinstance(values, Mapping):
        raise PipelineError("Pass profiles must be a mapping from pass number to profile name.")
    result: dict[int, str] = {}
    for number, name in values.items():
        if type(number) is not int or number not in range(1, 6):
            raise PipelineError("Pass profile keys must be integers from 1 through 5.")
        if not isinstance(name, str) or not name:
            raise PipelineError(f"Profile name for pass {number} must be a non-empty string.")
        result[number] = name
    return result


def effective_settings(bundle: Path, root: Path, options: ModelOptions | None = None,
                       *, base: dict | None = None, files=None) -> dict:
    options = options or ModelOptions()
    exists = files.exists if files else Path.exists
    read = files.read_json if files else read_json
    installed = exists(root / "settings.json")
    if base is not None:
        settings = copy.deepcopy(base)
    else:
        settings = read(root / "settings.json") if installed else read(bundle / "settings.default.json")
        if installed:
            settings, _ = migrate_settings_file(root, settings)
    override_profile = settings.get("profiles", {}).get(settings.get("default_profile"))
    if base is not None:
        assignments = validate_pass_profiles(options.pass_profiles)
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


def load_valid_book(root: Path, fingerprint=plan_fingerprint, files=None) -> dict:
    exists = files.exists if files else Path.exists
    read = files.read_json if files else read_json
    if not exists(root / "book.json"):
        raise PipelineError("Project not imported. Run import first.")
    book = read(root / "book.json")
    if book.get("content_fingerprint") != fingerprint(book):
        raise PipelineError(
            "The frozen source/chunk manifest was modified. Restore book.json or import into a new project. "
            "Only titles and thread_id may be edited in place."
        )
    return book


class ReprepareLocked(PipelineError):
    """A source plan with persisted work cannot be replaced in place."""


def _mark_local_estimates(book: dict) -> None:
    identity = {"method": "chars_per_four_estimate", "chars_per_token": 4}
    book["model_identity"] = None
    book["tokenizer_identity"] = identity
    for chapter in book["chapters"]:
        chapter["source_tokens_tokenizer"] = identity
        chapter["source_tokens_quality"] = "estimated"
    for chunk in book["chunks"]:
        chunk["source_tokens_quality"] = "estimated"


class ProjectsService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink,
                 publishing=None):
        self.dependencies, self.progress = dependencies, progress
        self.publishing = publishing

    def import_book(self, command: ImportBookCommand) -> ImportResult:
        root, source = command.project.resolve(), command.source.resolve()
        with OperationScope(self.dependencies, root, self.progress, create=True) as scope:
            if self.dependencies.files.exists(root / "book.json"):
                raise PipelineError("Project already imported. Use analyze/status/translate, not import again.")
            if self.dependencies.files.exists(root / "state.sqlite3"):
                raise PipelineError("Incomplete or unrelated project database found. Use a new project directory.")
            if root.is_relative_to(source):
                raise PipelineError("Keep the project outside the input folder to avoid importing generated files.")
            handoff = (prepare_handoff(command.previous_volume, root, new_source=source)
                       if command.previous_volume else None)
            configuration = handoff["configuration"] if handoff else None
            settings = effective_settings(
                self.dependencies.bundle, root, command,
                base=configuration["settings"] if configuration else None,
                files=self.dependencies.files,
            )
            if command.whole_section_limit is not None:
                settings["whole_section_char_limit"] = command.whole_section_limit
            elif not handoff:
                settings["whole_section_char_limit"] = 10000
            if settings["whole_section_char_limit"] <= 0:
                raise PipelineError("--whole-section-limit must be positive.")
            if configuration:
                materialize_configuration(root, configuration, settings)
            assignments = validate_pass_profiles(command.pass_profiles)
            if command.local_token_estimate:
                # Web Prepare freezes the source without constructing a provider.
                for number in range(1, 6):
                    resolve_profile(settings, number, command_profile=command.profile,
                                    command_pass_profiles=assignments, project=root)
                count = estimate_source_tokens
                client = None
            else:
                client = scope.providers(settings, profile=command.profile, pass_profiles=assignments)
                client.discover(1)
                count = client.count
            source_folder = unpack_epub(source, root) if source.is_file() and source.suffix.lower() == '.epub' else source
            book = import_folder(
                source_folder, root, count, settings, self.progress,
                opf=command.opf.resolve() if command.opf else None,
                encoding=command.input_encoding, chapter_mode=command.chapter_mode,
                chapter_selector=command.chapter_selector, sidecars=command.sidecar_txt,
                include_glob=command.include_glob,
            )
            if source_folder != source:
                book['source_archive'] = str(source)
            if client is None:
                _mark_local_estimates(book)
            else:
                book["model_identity"] = client.identity
                book["tokenizer_identity"] = client.tokenizer_identity
                for chapter in book["chapters"]:
                    chapter["source_tokens_tokenizer"] = client.tokenizer_identity
            book["content_fingerprint"] = plan_fingerprint(book)
            book["planning_settings"] = {"whole_section_char_limit": settings["whole_section_char_limit"]}
            if not configuration:
                self.dependencies.files.write_json(root / "settings.json", settings)
                self.dependencies.files.copy_prompts(
                    self.dependencies.bundle / "prompts", root / "prompts",
                )
            store = scope.store
            store.register_chunks(book)
            series_id = None
            series_volume = None
            inherited_terms = inherited_observations = 0
            if handoff:
                seed_path = root / "series.seed.json"
                self.dependencies.files.write_json(seed_path, handoff["seed"])
                store.seed_series(handoff["seed"])
                series = continuation_metadata(book["source_fingerprint"], handoff, digest(seed_path.read_bytes()))
                self.dependencies.files.write_json(root / "series.json", series)
                series_id = handoff["series_id"]
                series_volume = handoff["previous_volume"] + 1
                inherited_terms = len(handoff["seed"]["terms"])
                inherited_observations = len(handoff["seed"]["observations"])
            # Completed-import marker is deliberately last.
            self.dependencies.files.write_json(root / "book.json", book)
            return ImportResult(
                project=root, narrative_sections=len(book["chapters"]),
                translation_units=len(book["chunks"]),
                excluded_sections=len(book.get("non_narrative_sections", [])),
                warnings=tuple(book["warnings"]), series_id=series_id,
                series_volume=series_volume, inherited_terms=inherited_terms,
                inherited_observations=inherited_observations,
            )

    def _reprepare_inputs(self, root: Path, source: Path, expected_revision: str, scope):
        from .source_preflight import source_signature
        from .workspace_setup import read_workspace_setup
        from ..processing import ConfigConflict, configuration

        old_book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
        setup = read_workspace_setup(root)
        if not setup or source.name != setup['source_id']:
            raise ReprepareLocked('Only configured workspaces can rebuild their source plan.')
        config = configuration(root)
        if digest(config) != expected_revision:
            raise ConfigConflict('Configuration changed.')
        if source_signature(source.parent, source.name) != setup['source_fingerprint']:
            raise ReprepareLocked('The source changed since setup; use a new workspace.')
        allowed = {'book.json', 'settings.json', 'workspace.json', 'web.config.json',
                   'web.lifecycle.json', 'state.sqlite3', 'state.sqlite3-wal', 'state.sqlite3-shm',
                   '.lock', 'source-package', 'chapters', 'matter', 'extracted', 'prompts',
                   'catalog', 'history'}
        if (scope.store.has_work_since_import() or
                any(entry.name not in allowed or entry.is_symlink() for entry in root.iterdir())):
            raise ReprepareLocked('This workspace has persisted work; preserve it and prepare a new workspace.')
        history = root / 'history'
        versions = history / 'prepare_versions'
        if history.is_symlink() or versions.is_symlink():
            raise PipelineError('Unsafe preparation history.')
        settings = effective_settings(self.dependencies.bundle, root, files=self.dependencies.files)
        for number in range(1, 6):
            resolve_profile(settings, number, project=root)
        return old_book, config, settings, versions

    def check_reprepare(self, project: Path, source: Path, expected_revision: str) -> None:
        root = project.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            self._reprepare_inputs(root, source.resolve(), expected_revision, scope)

    def reprepare(self, command: ImportBookCommand, expected_revision: str) -> ImportResult:
        """Build a new local plan, then replace an untouched plan with a versioned backup."""
        root, source = command.project.resolve(), command.source.resolve()
        with OperationScope(self.dependencies, root, self.progress) as scope:
            old_book, config, settings, versions = self._reprepare_inputs(root, source, expected_revision, scope)
            stage = root / ('.reprepare-stage-' + uuid.uuid4().hex)
            stage.mkdir(mode=0o700)
            try:
                source_folder = unpack_epub(source, stage) if source.is_file() and source.suffix.lower() == '.epub' else source
                book = import_folder(source_folder, stage, estimate_source_tokens, settings, self.progress)
                if source_folder != source:
                    book['source_archive'] = str(source)
                    book['source_root'] = str(root / 'source-package')
                _mark_local_estimates(book)
                book['planning_settings'] = {'whole_section_char_limit': settings['whole_section_char_limit']}
                book['content_fingerprint'] = plan_fingerprint(book)
                version = versions / uuid.uuid4().hex
                version.mkdir(mode=0o700, parents=True)
                atomic_json(version / 'book.json', old_book)
                atomic_json(version / 'web.config.json', config)
                atomic_json(version / 'version.json', {
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'old_plan_fingerprint': old_book['content_fingerprint'],
                    'new_plan_fingerprint': book['content_fingerprint'],
                    'section_choices_reset': True,
                })
                # New rows are pending. Keeping old pending rows also makes an
                # interruption before book.json switches harmless to the old plan.
                scope.store.register_chunks(book)
                moved = []
                try:
                    for name in ('source-package', 'extracted', 'chapters', 'matter'):
                        old, new = root / name, stage / name
                        if old.exists():
                            old.rename(version / name)
                            moved.append(('old', name))
                        if new.exists():
                            new.rename(old)
                            moved.append(('new', name))
                    atomic_json(root / 'web.config.json', {**config, 'sections': {}})
                    atomic_json(root / 'book.json', book)
                except BaseException:
                    atomic_json(root / 'book.json', old_book)
                    atomic_json(root / 'web.config.json', config)
                    for kind, name in reversed(moved):
                        if kind == 'new':
                            (root / name).rename(stage / name)
                        else:
                            (version / name).rename(root / name)
                    shutil.rmtree(version, ignore_errors=True)
                    raise
                return ImportResult(
                    project=root, narrative_sections=len(book['chapters']),
                    translation_units=len(book['chunks']),
                    excluded_sections=len(book.get('non_narrative_sections', [])),
                    warnings=tuple(book['warnings']), series_id=None, series_volume=None,
                    inherited_terms=0, inherited_observations=0,
                )
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def status(self, command: StatusCommand) -> StatusResult:
        root = command.project.resolve()
        with ProjectReadScope(self.dependencies, root) as scope:
            book = load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)
            store = scope.store
            chunks = tuple(ChunkStatus(c["id"], store.chunk(c["id"])["status"]) for c in book["chunks"])
            terms = store.terms()
            series_id = series_volume = None
            series_path = root / "series.json"
            if self.dependencies.files.is_file(series_path):
                series = validate_series_metadata(
                    root, book["source_fingerprint"], self.dependencies.files.read_json(series_path),
                )
                series_id, series_volume = series["series_id"], series["volume"]
            chapters = tuple(ChapterStatus(
                id=chapter["id"], title=chapter["title"],
                completed=sum(store.chunk(cid)["status"] == "done" for cid in chapter["chunk_ids"]),
                total=len(chapter["chunk_ids"]),
            ) for chapter in book["chapters"])
            result = StatusResult(
                project=root, title=book["metadata"].get("title") or Path(book["source_root"]).name,
                series_id=series_id, series_volume=series_volume,
                narrative_sections=len(book["chapters"]), chunks=chunks,
                analysis_complete=bool(store.get("analysis_done")), approved=bool(store.get("approved")),
                term_count=len(terms), retained_candidates=sum(len(t["candidates"]) for t in terms),
                chapters=chapters, readable_output=root / "translation.txt",
                translation_complete=bool(chunks) and all(chunk.status == "done" for chunk in chunks),
            )
            if self.publishing is not None:
                result = replace(result, publication=self.publishing.query_snapshot(root, book, store))
        return result
