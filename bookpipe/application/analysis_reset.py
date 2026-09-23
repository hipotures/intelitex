"""Explicit, versioned removal of current P1 state for web workspaces."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from ..processing import ConfigConflict
from ..util import PipelineError, atomic_json, digest
from .sessions import OperationScope, ProjectReadScope


class AnalysisResetLocked(PipelineError):
    """P1 cannot be detached while dependent work exists."""


_ACTIVE_FILES = ('analysis_plan.json', 'analysis_inputs', 'terms.review.json',
                 'terms.review.html', 'book_memory.json', 'lexicon.approved.json')


def _file_state(root: Path):
    if (root / 'artifacts').is_symlink():
        raise PipelineError('Unsafe P1 artifact.')
    paths = [root / name for name in _ACTIVE_FILES]
    paths.append(root / 'artifacts' / 'pass1')
    result = []
    for path in paths:
        if path.is_symlink():
            raise PipelineError('Unsafe P1 artifact.')
        if not path.exists():
            continue
        if path.is_dir():
            for parent, dirs, files in os.walk(path, followlinks=False):
                for name in [*dirs, *files]:
                    child = Path(parent) / name
                    if child.is_symlink():
                        raise PipelineError('Unsafe P1 artifact.')
                    if child.is_file():
                        result.append((str(child.relative_to(root)), digest(child.read_bytes())))
        else:
            result.append((str(path.relative_to(root)), digest(path.read_bytes())))
    return sorted(result)


def _dependent_work(store, root):
    if store.has_dependent_p1_work():
        return True
    return any((root / name).exists() for name in ('translation.txt', 'translation.status.json',
                                                   'publication.json', 'publication.epub'))


def _history_root(root):
    history, resets = root / 'history', root / 'history' / 'p1_resets'
    if history.is_symlink() or resets.is_symlink():
        raise PipelineError('Unsafe P1 history.')
    if resets.exists() and any((entry / 'pending.json').exists() for entry in resets.iterdir()):
        raise AnalysisResetLocked('An earlier P1 reset needs local recovery.')
    return resets


def status(root, store):
    files = _file_state(root)
    database = store.p1_reset_records()
    resets = _history_root(root)
    has_data = bool(files or database['jobs'] or database['merged'] or database['terms'] or
                    database['facts'] or database['history'] or any(
                        key.startswith('analysis:') or (key == 'analysis_done' and value == 'true')
                        for key, value in database['kv']))
    blocked = _dependent_work(store, root)
    return {'revision': digest({'files': files, 'database': database}),
            'has_data': has_data, 'can_reset': not blocked,
            'reason': 'dependent_work' if blocked else None,
            'history_available': resets.is_dir()}


class AnalysisResetService:
    def __init__(self, dependencies):
        self.dependencies = dependencies

    def status(self, root):
        with ProjectReadScope(self.dependencies, root) as scope:
            return status(root, scope.store)

    def reset(self, root, expected_revision):
        with OperationScope(self.dependencies, root) as scope:
            store = scope.store
            current = status(root, store)
            if current['revision'] != expected_revision:
                raise ConfigConflict('P1 state changed.')
            if not current['can_reset']:
                raise AnalysisResetLocked('P1 has dependent work. Preserve it in this workspace.')
            if not current['has_data']:
                return current
            resets = _history_root(root)
            resets.mkdir(mode=0o700, parents=True, exist_ok=True)
            version = resets / uuid.uuid4().hex
            version.mkdir(mode=0o700)
            store.backup_to(version / 'state.sqlite3')
            atomic_json(version / 'pending.json', {'revision': expected_revision})
            moved = []
            committed = False
            try:
                for name in (*_ACTIVE_FILES, 'artifacts/pass1'):
                    source = root / name
                    if not source.exists():
                        continue
                    target = version / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    source.rename(target)
                    moved.append((source, target))
                store.clear_p1()
                committed = True
                atomic_json(version / 'completed.json', {'revision': expected_revision})
                (version / 'pending.json').unlink()
            except BaseException:
                if not committed:
                    for source, target in reversed(moved):
                        target.rename(source)
                raise
            return status(root, store)
