"""JSON is the sole durable checkpoint store.

SQLite runs only in memory to preserve the existing transactional query API.
No runtime path opens the retired state.sqlite3. Immutable table snapshots and
commit records are published by one atomic HEAD replacement, with optimistic
revision checking under a filesystem lock. A missing/corrupt HEAD fails closed.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from .util import PipelineError, atomic_json, digest, dumps, file_lock, read_json


SCHEMA = '''
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (key TEXT NOT NULL, fingerprint TEXT NOT NULL, result_path TEXT NOT NULL,
 result_hash TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(key,fingerprint));
CREATE TABLE merged (key TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(key,fingerprint));
CREATE TABLE terms (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
 choice TEXT, approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE facts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE chunks (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
 final_path TEXT, deps TEXT NOT NULL DEFAULT '[]', lexical_hash TEXT);
CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, data TEXT);
'''
COLUMNS = {
    'kv': ('key', 'value'),
    'jobs': ('key', 'fingerprint', 'result_path', 'result_hash', 'metadata'),
    'merged': ('key', 'fingerprint'),
    'terms': ('id', 'data', 'choice', 'approved'),
    'facts': ('id', 'data'),
    'chunks': ('id', 'status', 'final_path', 'deps', 'lexical_hash'),
    'history': ('id', 'kind', 'data'),
    'sqlite_sequence': ('name', 'seq'),
}
JSON_COLUMNS = {'kv': ('value',), 'jobs': ('metadata',), 'terms': ('data',),
                'facts': ('data',), 'chunks': ('deps',), 'history': ('data',)}
FORMAT = 1


def state_path(root: Path) -> Path:
    return root / 'state' / 'HEAD.json'


def snapshot(db) -> dict:
    """Lossless SQL values/order, with JSON cells represented as native JSON.

    Noncanonical original JSON text is retained only where necessary to preserve
    legacy byte-sensitive revision calculations. SQL NULL remains distinguishable
    from the JSON value null. AUTOINCREMENT high-water marks are included.
    """
    tables = {}
    for table, columns in COLUMNS.items():
        rows = []
        for values in db.execute(f'SELECT rowid,{",".join(columns)} FROM {table} ORDER BY rowid'):
            row = dict(zip(('_rowid', *columns), values))
            encodings, nulls = {}, []
            for column in JSON_COLUMNS.get(table, ()):
                raw = row[column]
                if raw is None:
                    nulls.append(column)
                else:
                    try:
                        row[column] = json.loads(raw)
                    except (ValueError, TypeError) as exc:
                        raise PipelineError(f'Invalid JSON in state {table}.{column}.') from exc
                    if dumps(row[column]) != raw:
                        encodings[column] = raw
            if encodings:
                row['_json_text'] = encodings
            if nulls:
                row['_sql_nulls'] = nulls
            rows.append(row)
        tables[table] = rows
    return tables


def restore(db, tables):
    if set(tables) != set(COLUMNS):
        raise PipelineError('Incomplete JSON state schema.')
    for table, columns in COLUMNS.items():
        rows = tables[table]
        if not isinstance(rows, list):
            raise PipelineError('Invalid JSON state rows.')
        if table == 'sqlite_sequence':
            db.execute('DELETE FROM sqlite_sequence')
        for row in rows:
            if (not isinstance(row, dict) or set(row) - {'_json_text', '_sql_nulls'} != {'_rowid', *columns}
                    or type(row['_rowid']) is not int):
                raise PipelineError(f'Invalid JSON state record in {table}.')
            encoded = row.get('_json_text', {})
            nulls = row.get('_sql_nulls', [])
            if not isinstance(encoded, dict) or not isinstance(nulls, list):
                raise PipelineError('Invalid JSON state encodings.')
            if set(encoded) - set(JSON_COLUMNS.get(table, ())) or set(nulls) - set(JSON_COLUMNS.get(table, ())):
                raise PipelineError('Unexpected JSON state encoding field.')
            values = dict(row)
            for column in JSON_COLUMNS.get(table, ()):
                if column in nulls:
                    if row[column] is not None:
                        raise PipelineError('Invalid SQL NULL representation.')
                    values[column] = None
                else:
                    raw = encoded.get(column, dumps(row[column]))
                    if dumps(json.loads(raw)) != dumps(row[column]):
                        raise PipelineError('JSON state encoding does not match its value.')
                    values[column] = raw
            names = columns if table in ('terms', 'history') else ('rowid', *columns)
            if table in ('terms', 'history') and row['_rowid'] != row['id']:
                raise PipelineError('State ID does not match row order identity.')
            data = [values[name] if name != 'rowid' else row['_rowid'] for name in names]
            db.execute(f'INSERT INTO {table}({",".join(names)}) VALUES ({",".join("?" for _ in names)})', data)


def _read(path):
    if path.is_symlink():
        raise PipelineError(f'Unsafe JSON state path: {path.name}.')
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        raise PipelineError(f'Missing or corrupt JSON state: {path}. No SQLite fallback.') from exc


def _object(root, kind, identity):
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
        raise PipelineError('Invalid state object identity.')
    path = root / 'state' / kind / (identity + '.json')
    if path.parent.is_symlink():
        raise PipelineError('Unsafe JSON state directory.')
    value = _read(path)
    if digest(value) != identity:
        raise PipelineError(f'JSON state checksum mismatch: {path}.')
    return value


def load_state(root):
    if (root / 'state').is_symlink():
        raise PipelineError('Unsafe JSON state directory.')
    head = _read(state_path(root))
    if not isinstance(head, dict) or set(head) != {'format_version', 'commit'} or head['format_version'] != FORMAT:
        raise PipelineError('Unsupported JSON state format.')
    commit = _object(root, 'commits', head['commit'])
    if (not isinstance(commit, dict) or commit.get('format_version') != FORMAT
            or type(commit.get('revision')) is not int or commit['revision'] < 1
            or set(commit.get('tables', {})) != set(COLUMNS)):
        raise PipelineError('Invalid JSON state commit.')
    tables = {table: _object(root, 'objects', identity) for table, identity in commit['tables'].items()}
    return head, commit, tables


def _write_object(root, kind, value):
    identity = digest(value)
    path = root / 'state' / kind / (identity + '.json')
    if path.parent.is_symlink() or path.is_symlink():
        raise PipelineError('Unsafe JSON state object path.')
    if path.exists():
        if digest(_read(path)) != identity:
            raise PipelineError('Existing JSON state object is corrupt.')
    else:
        atomic_json(path, value)
    return identity


def publish(root, tables, expected, revision, *, origin=None):
    """Only HEAD publishes a transaction; orphan candidates are never replayed."""
    if (root / 'state').is_symlink():
        raise PipelineError('Unsafe JSON state directory.')
    with file_lock(root, '.state.lock', 'JSON state is being committed by another writer.'):
        current = _read(state_path(root)) if state_path(root).exists() else None
        if current != expected:
            raise PipelineError('JSON state changed. Reopen the project before writing.')
        identities = {table: _write_object(root, 'objects', rows) for table, rows in tables.items()}
        commit = {'format_version': FORMAT, 'revision': revision,
                  'previous': expected['commit'] if expected else None, 'tables': identities}
        if origin is not None:
            commit['origin'] = origin
        identity = _write_object(root, 'commits', commit)
        head = {'format_version': FORMAT, 'commit': identity}
        # Persist newly created state/objects/commits directory entries before
        # publishing the pointer, including state/ in the workspace directory.
        for directory in (root / 'state', root):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        atomic_json(state_path(root), head)
        return head, commit


class StateConnection(sqlite3.Connection):
    """Disposable in-memory relational working set, committed to JSON only."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ready = False
        self._readonly = False
        self._depth = 0

    def __enter__(self):
        self._depth += 1
        return self

    def __exit__(self, kind, value, traceback):
        self._depth -= 1
        if kind is not None:
            self.rollback()
        elif self._depth == 0:
            self.commit()
        return False

    def commit(self):
        if self._ready and not self._readonly and self.in_transaction:
            tables = snapshot(self)
            if tables != self._snapshot:
                try:
                    head, commit = publish(self._root, tables, self._head, self._revision + 1)
                except BaseException:
                    super().rollback()
                    raise
                self._head, self._revision, self._snapshot = head, commit['revision'], tables
        super().commit()

    def execute(self, sql, parameters=()):
        if self._ready and sql.strip().rstrip(';').upper() in ('COMMIT', 'END', 'END TRANSACTION'):
            raise sqlite3.NotSupportedError('Use commit() or the state transaction context; raw COMMIT bypasses JSON persistence.')
        return super().execute(sql, parameters)

    def executescript(self, script):
        if self._ready:
            raise sqlite3.NotSupportedError('State scripts may implicitly commit; use a state transaction.')
        return super().executescript(script)


def connect_state(root: Path, *, readonly=False):
    root = root.resolve()
    exists = state_path(root).exists()
    if not exists and (root / 'state.sqlite3').exists():
        raise PipelineError('Legacy state requires explicit migration with migrate-state. No SQLite fallback.')
    if not exists and (readonly or (root / 'state').exists() or (root / 'book.json').exists()):
        raise PipelineError('Missing JSON state HEAD. Recover or explicitly migrate; refusing to initialize over existing state.')
    db = sqlite3.connect(':memory:', factory=StateConnection)
    try:
        db.executescript(SCHEMA)
        if exists:
            head, commit, tables = load_state(root)
            restore(db, tables)
            db.commit()
            if snapshot(db) != tables:
                raise PipelineError('JSON state did not reconstruct exactly.')
        else:
            tables = snapshot(db)
            head, commit = publish(root, tables, None, 1)
        db._root, db._head, db._revision, db._snapshot = root, head, commit['revision'], tables
        db._readonly, db._ready = readonly, True
        db.row_factory = sqlite3.Row
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY
                          if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK)
        if readonly:
            db.execute('PRAGMA query_only=ON')
        return db
    except BaseException:
        db.close()
        raise


def backup_state(db, path):
    """A self-contained JSON backup of a transactionally stable working set."""
    if db.in_transaction:
        raise PipelineError('Cannot back up uncommitted state.')
    atomic_json(path, {'format_version': FORMAT, 'tables': snapshot(db)})
