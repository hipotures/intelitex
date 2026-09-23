"""Validated runtime inputs and detached supervision snapshots."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from .protocol import public_envelope

ACTIVE = frozenset({"starting", "running", "stopping"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "abandoned"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobSpec:
    workspace_root: str
    workspace_id: str
    project: str
    operation: str
    profile: str | None = None
    chunk_limit: int = 0
    chunk_id: str | None = None
    pass_no: int | None = None
    rerun: bool = False
    target_language: str = "pl"

    def __post_init__(self):
        if self.operation not in {"analyze", "translate", "publish"}:
            raise ValueError("Unsupported operation; use analyze, translate or publish.")
        if self.profile is not None and (not isinstance(self.profile, str) or not re.fullmatch(r"[\w.-]{1,128}", self.profile)):
            raise ValueError("Invalid profile name.")
        if type(self.chunk_limit) is not int or self.chunk_limit < 0:
            raise ValueError("chunk_limit must be a nonnegative integer (0 means all).")
        if self.chunk_id is not None:
            if (self.operation != 'translate' or not isinstance(self.chunk_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', self.chunk_id)
                    or self.pass_no not in {2, 3, 4, 5} or self.chunk_limit != 0):
                raise ValueError('Targeted translation requires a chunk ID and pass 2–5.')
        elif self.pass_no is not None:
            raise ValueError('A targeted pass requires a chunk ID.')
        if type(self.rerun) is not bool or (self.rerun and self.chunk_id is None):
            raise ValueError('Rerun applies only to a targeted translation pass.')
        if not isinstance(self.target_language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", self.target_language):
            raise ValueError("Invalid target_language.")
        root = Path(self.workspace_root).resolve()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.workspace_id) or ".." in self.workspace_id:
            raise ValueError("Invalid workspace ID.")
        project = (root / self.workspace_id).resolve()
        if project == root or not project.is_relative_to(root) or project != Path(self.project) or not project.is_dir():
            raise ValueError("Workspace must resolve beneath workspace root.")
        object.__setattr__(self, "workspace_root", str(root))


@dataclass(frozen=True)
class Job:
    job_id: str
    workspace_root: str
    workspace_id: str
    project: str
    operation: str
    state: str = "starting"
    pid: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    sequence: int = 0
    last_event: dict | None = None
    error: dict | None = None

    def public(self) -> dict:
        value = {key: getattr(self, key) for key in (
            'job_id', 'workspace_id', 'operation', 'state', 'pid', 'started_at',
            'finished_at', 'exit_code', 'sequence',
        )}
        value['last_event'] = public_envelope(self.last_event) if self.last_event is not None else None
        error_type = self.error.get('type') if self.error else None
        if not isinstance(error_type, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', error_type):
            error_type = 'WorkerError'
        message = 'Operation failed; inspect locally.'
        safe_errors = {
            'local_model_unavailable': 'The selected local model server is unavailable. Start llama.cpp or choose another profile for this pass.',
            'source_span_mismatch': 'Model output cited text that does not exactly match its source sentence. This attempt was not selected.',
            'missing_prerequisite': 'An earlier pass has no current saved result for this chunk. Run that pass first.',
            'pass_already_saved': 'This pass already has a saved result. Refresh the page and choose Run again.',
        }
        error_code = self.error.get('code') if self.error else None
        if isinstance(error_code, str) and error_code in safe_errors:
            message = safe_errors[error_code]
        private_message = self.error.get('message', '') if self.error else ''
        if error_type == 'PipelineError' and 'source_span is not an exact quote' in private_message and message == 'Operation failed; inspect locally.':
            message = ('Model output cited text that does not exactly match its source sentence. '
                       'This attempt was not selected; check the failed attempt before retrying.')
        value['error'] = ({'type': error_type, 'message': message} if self.error else None)
        return value


@dataclass(frozen=True)
class ImportJobSpec:
    """Import a draft or rebuild an untouched prepared plan under supervision."""
    workspace_root: str
    workspace_id: str
    project: str
    import_root: str
    source_id: str
    operation: str = 'import'
    previous_volume: str | None = None
    opf: str | None = None
    input_encoding: str | None = None
    chapter_mode: str = 'auto'
    chapter_selector: str | None = None
    include_glob: str | None = None
    sidecar_txt: bool = False
    whole_section_limit: int | None = None
    profile: str | None = None
    pass_profiles: dict | None = None
    model: str | None = None
    context_size: int | None = None
    thinking: str | None = None
    reprepare: bool = False
    expected_revision: str | None = None

    def __post_init__(self):
        from ..application.imports import (DestinationConflict, confined_source,
                                           validate_source_tree, workspace_destination)
        root = Path(self.workspace_root).resolve(strict=True)
        destination = workspace_destination(root, self.workspace_id)
        if self.operation != 'import' or str(destination) != self.project:
            raise ValueError('Invalid import specification.')
        if type(self.reprepare) is not bool:
            raise ValueError('Invalid reprepare option.')
        if not self.reprepare and self.expected_revision is not None:
            raise ValueError('Revision applies only to reprepare.')
        if self.reprepare:
            from ..application.workspace_setup import read_workspace_setup
            from ..util import read_json
            if (destination.is_symlink() or not destination.is_dir() or
                    (destination / 'book.json').is_symlink() or
                    not (destination / 'book.json').is_file() or
                    not (destination / 'state.sqlite3').is_file()):
                raise DestinationConflict('Prepared workspace is unavailable for rebuilding.')
            setup = read_workspace_setup(destination)
            legacy_matches = False
            if not setup and (destination / 'book.json').is_file():
                book = read_json(destination / 'book.json')
                recorded = Path(book.get('source_archive', book.get('source_root', ''))).resolve()
                legacy_matches = recorded == Path(self.import_root).resolve(strict=True) / self.source_id
            if (not (setup.get('source_id') == self.source_id or legacy_matches) or
                    not isinstance(self.expected_revision, str) or
                    not re.fullmatch(r'[0-9a-f]{64}', self.expected_revision)):
                raise DestinationConflict('Prepared workspace is unavailable for rebuilding.')
        elif destination.exists():
            from ..application.workspace_setup import validate_draft_destination
            from ..util import PipelineError
            try:
                validate_draft_destination(destination, self.source_id)
            except PipelineError as exc:
                raise DestinationConflict('Destination already exists.') from exc
        if self.reprepare and any(value is not None for value in (
                self.previous_volume, self.opf, self.input_encoding, self.include_glob,
                self.profile, self.pass_profiles, self.model, self.context_size,
                self.thinking, self.whole_section_limit, self.chapter_selector)):
            raise ValueError('Reprepare uses the saved source and settings.')
        if self.reprepare and (self.chapter_mode != 'auto' or self.sidecar_txt):
            raise ValueError('Invalid reprepare options.')
        source_root = Path(self.import_root).resolve(strict=True)
        source = confined_source(source_root, self.source_id)
        if not (source.is_dir() or (source.is_file() and source.suffix.lower() == '.epub')) or destination.is_relative_to(source):
            raise ValueError('Invalid source.')
        validate_source_tree(source)
        if self.opf is not None and (source.is_file() or not confined_source(source, self.opf).is_file()):
            raise ValueError('Invalid OPF selection.')
        if self.previous_volume is not None:
            previous = workspace_destination(root, self.previous_volume)
            if not (previous / 'book.json').is_file():
                raise ValueError('Previous volume is not imported.')
        if self.chapter_mode not in {'auto', 'file', 'headings'} or type(self.sidecar_txt) is not bool:
            raise ValueError('Invalid import options.')
        for value in (self.whole_section_limit, self.context_size):
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError('Expected a positive integer.')
        if self.pass_profiles is not None and not isinstance(self.pass_profiles, dict):
            raise ValueError('Invalid pass profiles.')
        if any(not isinstance(value, str) for value in (self.pass_profiles or {}).values()):
            raise ValueError('Invalid pass profile name.')
        for value in (self.profile, *(self.pass_profiles or {}).values()):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[\w.-]{1,128}', value)):
                raise ValueError('Invalid profile.')
        if self.pass_profiles is not None and any(k not in {'1', '2', '3', '4', '5'} for k in self.pass_profiles):
            raise ValueError('Invalid pass profiles.')
        for value in (self.model, self.input_encoding, self.chapter_selector, self.include_glob):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 1024):
                raise ValueError('Invalid import option.')
        if self.model is not None and ('\\' in self.model or self.model.startswith('/') or '..' in self.model):
            raise ValueError('Model must be an identifier.')
        if self.include_glob is not None and (self.include_glob.startswith('/') or '..' in self.include_glob or '\\' in self.include_glob):
            raise ValueError('Invalid include glob.')
        if self.thinking not in {None, 'on', 'off'}:
            raise ValueError('Invalid thinking setting.')
        object.__setattr__(self, 'workspace_root', str(root))
        object.__setattr__(self, 'import_root', str(source_root))


def parse_spec(value: dict) -> JobSpec | ImportJobSpec:
    return (ImportJobSpec if value.get('operation') == 'import' else JobSpec)(**value)
