"""Explicit offline maintenance adapter; the only reader of legacy checkpoint DBs."""
from pathlib import Path
from contextlib import closing
import sqlite3

from ..state_files import COLUMNS, SCHEMA, connect_state, publish, restore, snapshot, state_path
from ..util import PipelineError, atomic_json, digest, project_lock


def migrate_state(root: Path) -> dict:
    root = root.resolve()
    with project_lock(root):
        if state_path(root).exists():
            return {**verify_state(root), 'status': 'already_migrated'}
        source = root / 'state.sqlite3'
        if not source.is_file() or source.is_symlink():
            raise PipelineError('Legacy state database is missing or unsafe.')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as legacy:
            legacy.execute('BEGIN')
            if legacy.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise PipelineError('Legacy state failed integrity_check; no migration was published.')
            objects = legacy.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if {name for kind, name in objects if kind == 'table'} != set(COLUMNS) - {'sqlite_sequence'}:
                raise PipelineError('Unknown legacy state schema; export would be incomplete.')
            if any(kind not in ('table', 'index') for kind, name in objects):
                raise PipelineError('Unsupported legacy state schema objects.')
            for table, columns in COLUMNS.items():
                if tuple(row[1] for row in legacy.execute(f'PRAGMA table_info({table})')) != columns:
                    raise PipelineError(f'Unknown legacy state schema for {table}.')
            tables = snapshot(legacy)
            check = sqlite3.connect(':memory:')
            try:
                check.executescript(SCHEMA)
                restore(check, tables)
                if snapshot(check) != tables:
                    raise PipelineError('Migration round-trip verification failed.')
            finally:
                check.close()
            identity = digest(tables)
            backup = root / 'history' / 'state_migration' / (identity + '.sqlite3')
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                destination = sqlite3.connect(backup)
                try:
                    legacy.backup(destination)
                finally:
                    destination.close()
            # Verify the backup separately, including WAL-only source changes.
            with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as saved:
                if snapshot(saved) != tables:
                    raise PipelineError('Legacy backup does not match migration snapshot.')
            publish(root, tables, None, 1, origin={
                'kind': 'sqlite-migration', 'snapshot_sha256': identity,
                'backup': str(backup.relative_to(root)),
                'legacy_file_sha256': digest(source.read_bytes()),
            })
        # Reopen through the runtime path; no implicit legacy reader is available.
        reader = connect_state(root, readonly=True)
        try:
            if snapshot(reader) != tables:
                raise PipelineError('Published JSON migration verification failed.')
        finally:
            reader.close()
        atomic_json(root / 'history' / 'state_migration' / 'retired-files.json', {
            'files': {name: digest((root / name).read_bytes()) if (root / name).is_file() else None
                      for name in ('state.sqlite3', 'state.sqlite3-wal', 'state.sqlite3-shm')},
            'snapshot_sha256': identity,
        })
        return {'project': str(root), 'status': 'migrated', 'verified': True,
                'snapshot_sha256': identity, 'backup': str(backup),
                'rows': {name: len(rows) for name, rows in tables.items()}}


def migrate_workspaces(root: Path) -> list[dict]:
    """Only immediate workspace directories, never nested historical backups."""
    if not root.is_dir():
        raise PipelineError('Workspace root does not exist.')
    results = []
    for project in sorted(root.iterdir()):
        if project.is_symlink():
            continue
        if project.is_dir() and (project / 'book.json').is_file():
            results.append(migrate_state(project))
    return results


def verify_state(root: Path) -> dict:
    """Validate JSON and detect any change to retired files without opening SQLite."""
    from ..util import read_json
    root = root.resolve()
    reader = connect_state(root, readonly=True)
    try:
        revision = reader._revision
        rows = {name: len(values) for name, values in snapshot(reader).items()}
    finally:
        reader.close()
    baseline = root / 'history' / 'state_migration' / 'retired-files.json'
    changed = []
    if (root / 'state.sqlite3').exists() and not baseline.is_file():
        raise PipelineError('Retired database audit baseline is missing; migration needs verification.')
    if baseline.is_file():
        for name, expected in read_json(baseline)['files'].items():
            actual = digest((root / name).read_bytes()) if (root / name).is_file() else None
            if actual != expected:
                changed.append(name)
    return {'project': str(root), 'verified': not changed, 'revision': revision,
            'retired_files_changed': changed, 'rows': rows}


def verify_workspaces(root: Path) -> list[dict]:
    if not root.is_dir():
        raise PipelineError('Workspace root does not exist.')
    return [verify_state(project) for project in sorted(root.iterdir())
            if not project.is_symlink() and project.is_dir() and (project / 'book.json').is_file()]
