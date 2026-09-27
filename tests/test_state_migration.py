"""Lossless legacy migration and JSON-only persistence; no provider or live workspace."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bookpipe.infrastructure.state_migration import migrate_state
from bookpipe.state_files import connect_state, state_path
from bookpipe.store import Store
from bookpipe.infrastructure.read_store import ReadStore
from bookpipe.util import PipelineError


LEGACY_SCHEMA = '''
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (key TEXT NOT NULL, fingerprint TEXT NOT NULL, result_path TEXT NOT NULL,
 result_hash TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(key,fingerprint));
CREATE TABLE merged (key TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(key,fingerprint));
CREATE TABLE terms (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL, choice TEXT,
 approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE facts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE chunks (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending', final_path TEXT,
 deps TEXT NOT NULL DEFAULT '[]', lexical_hash TEXT);
CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, data TEXT);
'''
TABLES = ('kv', 'jobs', 'merged', 'terms', 'facts', 'chunks', 'history', 'sqlite_sequence')


def database_rows(connection):
    return {name: [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM {name} ORDER BY rowid')]
            for name in TABLES}


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    db = sqlite3.connect(root / 'state.sqlite3')
    db.executescript(LEGACY_SCHEMA)
    db.execute('PRAGMA journal_mode=WAL')
    with db:
        db.executemany('INSERT INTO kv VALUES (?,?)', [
            ('approved', 'true'), ('analysis_done', 'true'),
            ('selected_pass:pass2/c1', '{"fingerprint": "chosen", "base_fingerprint": "base"}'),
            ('invalidated:pass5/c1', '{"reason":"terminology_changed","term_ids":["T000007"]}'),
            ('unknown-future-key', '{"unicode":"Łódź","empty":[],"nil":null}'),
        ])
        db.execute('INSERT INTO terms(id,data,choice,approved) VALUES (7,?,?,1)',
                   (json.dumps({'source': 'Ada', 'aliases': [], 'candidates': []}), 'Żółć'))
        db.execute('INSERT INTO terms(id,data) VALUES (50,?)', ('{}',))
        db.execute('DELETE FROM terms WHERE id=50')
        db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?)', [
            ('pass2/c1', 'later-name', 'artifacts/old.json', 'hash1', '{ "n": 1 }'),
            ('pass2/c1', 'chosen', 'artifacts/chosen.json', 'hash2', '{}'),
        ])
        db.execute('INSERT INTO merged VALUES (?,?)', ('pass1/a1', 'fingerprint'))
        db.execute('INSERT INTO facts VALUES (?,?)', ('fact1', '{"about":["Ada"]}'))
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)', ('c1', 'stale', 'old-p5.json', '["T000007"]', 'lex'))
        db.execute('INSERT INTO history(id,kind,data) VALUES (100,?,?)', ('choice', 'null'))
        db.execute('INSERT INTO history(kind,data) VALUES (?,NULL)', ('sql-null',))
    yield root, db
    db.close()


def test_migration_preserves_every_value_row_order_and_id_sequence(legacy):
    root, source = legacy
    before = database_rows(source)
    report = migrate_state(root)
    assert report['status'] == 'migrated'
    assert report['verified'] is True
    assert state_path(root).is_file()
    new = connect_state(root, readonly=True)
    assert database_rows(new) == before
    new.close()
    assert database_rows(source) == before
    assert migrate_state(root)['status'] == 'already_migrated'


def test_no_sqlite_fallback_before_or_after_migration(legacy):
    root, source = legacy
    with pytest.raises(PipelineError, match='migrat'):
        Store(root)
    migrate_state(root)
    head = state_path(root)
    head.write_text('{broken')
    with pytest.raises(PipelineError):
        Store(root)
    with pytest.raises(PipelineError):
        ReadStore(root)
    assert source.execute("SELECT value FROM kv WHERE key='approved'").fetchone()[0] == 'true'


def test_json_only_mutation_survives_removal_of_legacy_database(legacy, monkeypatch):
    root, source = legacy
    migrate_state(root)
    before = database_rows(source)
    original_connect = sqlite3.connect
    def memory_only(database, *args, **kwargs):
        assert database == ':memory:', 'application tried to open a persistent SQLite database'
        return original_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', memory_only)
    store = Store(root)
    with store.db:
        store.set('new-choice', {'value': 'Gdańsk'})
        row = store.db.execute('INSERT INTO terms(data) VALUES (?)', ('{}',))
        assert row.lastrowid == 51
    store.close()
    assert database_rows(source) == before
    source.close()
    (root / 'state.sqlite3').unlink()
    reader = ReadStore(root)
    assert reader.get('new-choice') == {'value': 'Gdańsk'}
    assert reader.get('approved') is True
    reader.close()


def test_atomic_transaction_rollback_and_stable_reader(tmp_path):
    store = Store(tmp_path)
    with store.db:
        store.set('revision', 1)
    reader = ReadStore(tmp_path)
    with pytest.raises(RuntimeError):
        with store.db:
            store.set('revision', 2)
            store.note('discard', {'x': 1})
            raise RuntimeError('interrupt')
    assert store.get('revision') == 1
    with store.db:
        store.set('revision', 3)
    assert reader.get('revision') == 1
    reader.close()
    fresh = ReadStore(tmp_path)
    assert fresh.get('revision') == 3
    fresh.close()
    store.close()


def test_stale_writer_cannot_overwrite_newer_json(tmp_path):
    a, b = Store(tmp_path), Store(tmp_path)
    with a.db:
        a.set('value', 'new')
    with pytest.raises(PipelineError, match='changed'):
        with b.db:
            b.set('value', 'old')
    a.close(); b.close()
    check = ReadStore(tmp_path)
    assert check.get('value') == 'new'
    check.close()


def test_failure_before_head_preserves_committed_state(tmp_path, monkeypatch):
    import bookpipe.state_files as files
    store = Store(tmp_path)
    with store.db:
        store.set('value', 'before')
    head_before = state_path(tmp_path).read_bytes()
    original = files.atomic_json
    def fail_head(path, value):
        if path == state_path(tmp_path):
            raise OSError('simulated disk failure')
        return original(path, value)
    monkeypatch.setattr(files, 'atomic_json', fail_head)
    with pytest.raises(OSError):
        with store.db:
            store.set('value', 'after')
    assert state_path(tmp_path).read_bytes() == head_before
    assert store.get('value') == 'before'
    store.close()
    check = ReadStore(tmp_path)
    assert check.get('value') == 'before'
    check.close()


def test_unknown_legacy_schema_is_rejected_without_partial_migration(legacy):
    root, source = legacy
    source.execute('CREATE TABLE unhandled(secret TEXT)')
    source.commit()
    with pytest.raises(PipelineError, match='schema'):
        migrate_state(root)
    assert not state_path(root).exists()


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_process_death_at_commit_boundary(tmp_path, phase):
    store = Store(tmp_path)
    with store.db:
        store.set('value', 'before')
    store.close()
    script = '''
import os,sys
from pathlib import Path
from bookpipe.store import Store
import bookpipe.state_files as files
root=Path(sys.argv[1]); phase=sys.argv[2]
write=files.atomic_json
def crash(path,value):
    if path==files.state_path(root) and phase=='before': os._exit(73)
    write(path,value)
    if path==files.state_path(root) and phase=='after': os._exit(73)
files.atomic_json=crash
store=Store(root)
with store.db:
    store.set('value','after')
    store.note('same_transaction', {'value':'after'})
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), phase], timeout=20)
    assert result.returncode == 73
    recovered = ReadStore(tmp_path)
    assert recovered.get('value') == phase
    assert recovered.db.execute('SELECT count(*) FROM history').fetchone()[0] == (phase == 'after')
    recovered.close()


def test_corrupt_referenced_object_never_uses_intact_sqlite(legacy):
    root, _ = legacy
    migrate_state(root)
    head = json.loads(state_path(root).read_text())
    commit = json.loads((root / 'state' / 'commits' / (head['commit'] + '.json')).read_text())
    (root / 'state' / 'objects' / (commit['tables']['terms'] + '.json')).write_text('[]')
    with pytest.raises(PipelineError, match='checksum'):
        ReadStore(root)


def test_readonly_and_rolled_back_writes_do_not_touch_head(tmp_path):
    store = Store(tmp_path)
    head = state_path(tmp_path).read_bytes()
    store.set('uncommitted', True)
    store.close()
    read = ReadStore(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        read.db.execute("INSERT INTO kv VALUES ('illegal','true')")
    assert read.get('uncommitted') is None
    read.close()
    assert state_path(tmp_path).read_bytes() == head


def test_workspace_batch_is_complete_and_idempotent(tmp_path):
    from bookpipe.infrastructure.state_migration import migrate_workspaces
    for name in ('active', 'archived'):
        root = tmp_path / name
        root.mkdir()
        (root / 'book.json').write_text('{}')
        connection = sqlite3.connect(root / 'state.sqlite3')
        connection.executescript(LEGACY_SCHEMA)
        connection.close()
    (tmp_path / 'draft').mkdir()
    reports = migrate_workspaces(tmp_path)
    assert len(reports) == 2 and all(r['status'] == 'migrated' for r in reports)
    assert all(r['status'] == 'already_migrated' for r in migrate_workspaces(tmp_path))


def test_nonstandard_sequence_row_order_and_deleted_ids(legacy):
    root, source = legacy
    with source:
        source.execute('DELETE FROM sqlite_sequence')
        source.execute("INSERT INTO sqlite_sequence(rowid,name,seq) VALUES (3,'history',900)")
        source.execute("INSERT INTO sqlite_sequence(rowid,name,seq) VALUES (8,'terms',800)")
    migrate_state(root)
    store = Store(root)
    assert database_rows(store.db) == database_rows(source)
    with store.db:
        assert store.db.execute("INSERT INTO history(kind,data) VALUES ('new','{}')").lastrowid == 901
        assert store.db.execute("INSERT INTO terms(data) VALUES ('{}')").lastrowid == 801
    store.close()


def test_verify_detects_writes_to_retired_database_without_importing_them(legacy):
    from bookpipe.infrastructure.state_migration import verify_state
    root, source = legacy
    migrate_state(root)
    assert verify_state(root)['verified']
    with source:
        source.execute("UPDATE kv SET value='false' WHERE key='approved'")
    report = verify_state(root)
    assert not report['verified'] and report['retired_files_changed']
    current = ReadStore(root)
    assert current.get('approved') is True
    current.close()


def test_missing_all_state_in_prepared_workspace_is_not_reinitialized(tmp_path):
    (tmp_path / 'book.json').write_text('{}')
    with pytest.raises(PipelineError, match='Missing JSON state'):
        Store(tmp_path)
    assert not (tmp_path / 'state').exists()
