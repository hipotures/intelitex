"""No SQL fallback, exact legacy retention, portable receipts and bounded hot reads."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from bookpipe.infrastructure.job_migration import migrate_jobs, verify_jobs, request_migration_on_reload, complete_requested_migration
from bookpipe.runtime.models import Job, JobSpec
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.protocol import public_envelope
from bookpipe.runtime.supervisor import JobSupervisor, RequestConflict
from bookpipe.util import PipelineError, digest, file_lock, read_json
from test_server_api import api
from test_runtime import request


def legacy(tmp_path, *, count=3):
    root = tmp_path / 'workspaces'
    root.mkdir()
    source = tmp_path / 'old' / 'jobs.sqlite3'
    source.parent.mkdir()
    db = sqlite3.connect(source)
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE jobs(job_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,sequence INTEGER NOT NULL,data TEXT NOT NULL);
        CREATE INDEX event_job ON events(job_id,sequence);
        CREATE TABLE requests(scope TEXT NOT NULL,request_key TEXT NOT NULL,digest TEXT NOT NULL,job_id TEXT NOT NULL,PRIMARY KEY(scope,request_key));''')
    jobs, events = [], []
    for i in range(count):
        job = Job(f'job-{i}', str(root), 'book', str(root / 'book'), 'translate', state='succeeded', started_at=f'2026-09-27T10:00:{i:02}Z')
        for sequence in range(1, 131):
            event = {'job_id': job.job_id, 'workspace_id': job.workspace_id, 'sequence': sequence,
                     'timestamp': '2026-09-27T10:00:00Z', 'event': {'kind': 'usage', 'values': {'pass_no': 3}}}
            cursor = db.execute('INSERT INTO events(job_id,sequence,data) VALUES(?,?,?)', (job.job_id, sequence, json.dumps(event)))
            event = {**event, 'id': cursor.lastrowid}
            events.append(event)
        job = replace(job, sequence=130, last_event=event)
        jobs.append(job)
        db.execute('INSERT INTO jobs VALUES(?,?)', (job.job_id, json.dumps(asdict(job))))
        db.execute('INSERT INTO requests VALUES(?,?,?,?)', (str(root), f'request-{i}', digest(f'payload-{i}'), job.job_id))
    db.commit()  # Remains open; migration must include committed WAL content.
    return root, source, db, jobs, events


def test_complete_migration_wal_receipts_events_and_retired_file_drift(tmp_path):
    root, source, db, jobs, events = legacy(tmp_path)
    # SQLite readers may update SHM read marks; database/WAL content must not change.
    before = {p: digest(p.read_bytes()) for p in source.parent.glob('jobs.sqlite3*') if p.is_file() and not p.name.endswith('-shm')}
    try:
        result = migrate_jobs(root, source)
        assert result['verified'] and result['jobs'] == 3 and result['events_archived'] == 390 and result['requests'] == 3
        assert {p: digest(p.read_bytes()) for p in before} == before
        export = read_json(root / '.runtime/history/legacy-export.json')
        assert [v['job'] for v in export['jobs']] == [asdict(j) for j in jobs]
        assert export['events'] == events
        registry = JobRegistry(root)
        try:
            assert registry.list() == jobs
            assert registry.cursor() == events[-1]['id']
            assert registry.recent_events(workspace_root=str(root), workspace_id='book') == [
                public_envelope(e) for e in events[-120:]]
            for i, job in enumerate(jobs):
                assert registry.request(str(root), f'request-{i}', digest(f'payload-{i}')) == job
            with pytest.raises(RequestConflict):
                registry.request(str(root), 'request-0', digest('different'))
        finally:
            registry.close()
        assert migrate_jobs(root, source)['status'] == 'already_migrated'
        db.execute('UPDATE jobs SET data=data || " " WHERE job_id="job-0"')
        db.commit()
        assert not verify_jobs(root)['verified']
    finally:
        db.close()


def test_registry_survives_move_and_sqlite_deletion_without_model_or_history_scan(tmp_path, monkeypatch):
    root, source, db, jobs, _ = legacy(tmp_path)
    migrate_jobs(root, source)
    db.close()
    moved = tmp_path / 'different-server' / 'other-layout'
    moved.parent.mkdir()
    shutil.move(root, moved)
    shutil.rmtree(source.parent)
    monkeypatch.setenv('HOME', str(tmp_path / 'different-user'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'empty-state'))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Runtime opened SQLite'))
    registry = JobRegistry(moved)
    try:
        assert registry.receipt(str(moved), 'request-0').project == str(moved / 'book')
        assert registry.request(str(moved), 'request-0', digest('payload-0')).job_id == jobs[0].job_id
        for record in (moved / '.runtime/jobs').glob('*.json'):
            assert str(root) not in record.read_text()
        supervisor = JobSupervisor(registry, moved)
        monkeypatch.setattr(registry, 'list', lambda: pytest.fail('Hot path scanned all jobs'))
        assert [j['job_id'] for j in supervisor.snapshot()['jobs']] == [jobs[-1].job_id]
        assert supervisor.active_for_project(moved / 'book') is None
        assert registry.latest('book').job_id == jobs[-1].job_id
        supervisor.shutdown()
    finally:
        registry.close()


def test_bounded_live_replay_and_snapshot_with_full_receipt_history(tmp_path):
    registry = JobRegistry(tmp_path, event_limit=7)
    try:
        for i in range(50):
            job = Job(f'job-{i}', str(tmp_path), 'book', str(tmp_path / 'book'), 'translate', state='succeeded')
            registry.reserve(job, f'key-{i}', digest(i))
            registry.append(job.job_id, {'kind': 'job_state', 'values': {'state': 'succeeded'}})
        assert len(registry.events(0, workspace_root=str(tmp_path))) == 7
        assert len(registry.current()) == 1
        assert len(registry.list()) == 50
        assert registry.receipt(str(tmp_path), 'key-0').job_id == 'job-0'
        assert registry.current(job_id='job-0')[0].job_id == 'job-0'
        assert not registry.events(0, workspace_root=str(tmp_path / 'other'))
    finally:
        registry.close()


@pytest.mark.parametrize('point', ['before', 'after'])
def test_process_death_during_reservation_is_atomic_and_never_resumes_pid(tmp_path, point):
    registry = JobRegistry(tmp_path)
    registry.close()
    script = '''
import os,sys
from pathlib import Path
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.models import Job
from bookpipe.util import digest
import bookpipe.runtime.registry as module
root=Path(sys.argv[1]); registry=JobRegistry(root)
original=module.atomic_json
def crash(path,value):
 if path.name!='registry.json': return original(path,value)
 if sys.argv[2]=='after': original(path,value)
 os._exit(73)
module.atomic_json=crash
registry.reserve(Job('atomic',str(root),'book',str(root/'book'),'translate',pid=999999), 'request',digest('payload'))
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), point], cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 73
    registry = JobRegistry(tmp_path)
    try:
        job = registry.request(str(tmp_path), 'request', digest('payload'))
        if point == 'before':
            assert job is None and registry.list() == []
        else:
            assert job.job_id == 'atomic' and job.state == 'abandoned' and job.pid is None
    finally:
        registry.close()


def test_corrupt_record_is_not_replaced_by_empty_registry_or_sqlite(tmp_path):
    registry = JobRegistry(tmp_path)
    registry.save(Job('a', str(tmp_path), 'book', str(tmp_path/'book'), 'translate', state='succeeded'))
    registry.close()
    record = next((tmp_path / '.runtime/jobs').glob('*.json'))
    record.write_text('{')
    with pytest.raises(PipelineError, match='corrupt JSON job record'):
        JobRegistry(tmp_path)
    assert record.read_text() == '{'


def test_failed_reservation_does_not_claim_success_or_allow_counter_reuse(tmp_path, monkeypatch):
    registry = JobRegistry(tmp_path)
    def failed(*_):
        raise OSError('simulated disk failure')
    monkeypatch.setattr('bookpipe.runtime.registry.atomic_json', failed)
    job = Job('a', str(tmp_path), 'book', str(tmp_path/'book'), 'translate')
    with pytest.raises(OSError):
        registry.reserve(job, 'key', digest('payload'))
    assert registry.request(str(tmp_path), 'key', digest('payload')) is None
    with pytest.raises(PipelineError, match='requires recovery'):
        registry.reserve(job, 'key', digest('payload'))
    registry.close()


def test_explicit_reload_migration_waits_for_old_owner(tmp_path, monkeypatch):
    root, source, db, _, _ = legacy(tmp_path)
    db.close()
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'unused'))
    assert request_migration_on_reload(root, source)['staged_for_reload']
    with file_lock(source.parent, source.name + '.lock', 'owned'):
        with pytest.raises(PipelineError, match='old server'):
            complete_requested_migration(root)
    assert not (root / '.runtime').exists()
    complete_requested_migration(root)
    assert not (root / '.job-migration-request.json').exists()
    assert verify_jobs(root)['verified']


def test_unknown_legacy_schema_is_rejected_without_cutover(tmp_path):
    root, source, db, _, _ = legacy(tmp_path)
    db.execute('CREATE TABLE unknown(value TEXT)')
    db.commit()
    try:
        with pytest.raises(PipelineError, match='schema'):
            migrate_jobs(root, source)
        assert not (root / '.runtime').exists()
    finally:
        db.close()


def test_missing_committed_job_cannot_silently_erase_a_receipt(tmp_path):
    registry = JobRegistry(tmp_path)
    registry.reserve(Job('a', str(tmp_path), 'book', str(tmp_path/'book'), 'translate'), 'key', digest('payload'))
    registry.close()
    next((tmp_path / '.runtime/jobs').glob('*.json')).unlink()
    with pytest.raises(PipelineError, match='Missing or corrupt'):
        JobRegistry(tmp_path)


def test_request_fingerprint_and_job_identity_survive_independent_root_move(tmp_path):
    old = tmp_path / 'old'
    (old / 'book').mkdir(parents=True)
    registry = JobRegistry(old)
    original = JobSpec(str(old), 'book', str(old/'book'), 'translate')
    fingerprint = digest({key: value for key, value in asdict(original).items() if key not in {'workspace_root','project','import_root'}})
    registry.reserve(Job('already-done', str(old), 'book', str(old/'book'), 'translate', state='succeeded'), 'same-click', fingerprint)
    registry.close()
    moved = tmp_path / 'new'
    old.rename(moved)
    registry = JobRegistry(moved)
    supervisor = JobSupervisor(registry, moved, command_factory=lambda _: pytest.fail('Duplicate worker launched'))
    try:
        result = supervisor.start(JobSpec(str(moved), 'book', str(moved/'book'), 'translate'), request_key='same-click')
        assert result.job_id == 'already-done'
    finally:
        supervisor.shutdown()
        registry.close()


@pytest.mark.parametrize('point', ['before', 'after'])
def test_migration_process_death_has_only_complete_or_unpublished_registry(tmp_path, point):
    root, source, db, jobs, _ = legacy(tmp_path)
    db.close()
    script = '''
import os,sys
from pathlib import Path
import bookpipe.infrastructure.job_migration as m
original=m.os.rename
def crash(src,dst):
 if sys.argv[3]=='after': original(src,dst)
 os._exit(73)
m.os.rename=crash
m.migrate_jobs(Path(sys.argv[1]),Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(root), str(source), point], cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 73
    assert (root / '.runtime').exists() == (point == 'after')
    assert migrate_jobs(root, source)['verified']
    registry = JobRegistry(root)
    try:
        assert registry.list() == jobs
    finally:
        registry.close()


def test_api_uses_bounded_jobs_and_recovers_older_request_without_launch(api, monkeypatch):
    _, root, service, server = api
    registry = service.supervisor.registry
    payload = {'operation': 'translate'}
    fingerprint = digest({'operation': root.name, 'payload': payload})
    for i in range(30):
        registry.reserve(Job(f'history-{i}', str(root.parent), root.name, str(root), 'translate', state='succeeded'),
                         f'request-key-{i:016}', fingerprint)
    monkeypatch.setattr(registry, 'list', lambda: pytest.fail('HTTP scanned job archive'))
    status, snapshot = request(server, 'GET', '/api/jobs')
    assert status == 200 and [j['job_id'] for j in snapshot['jobs']] == ['history-29']
    assert request(server, 'GET', '/api/workspaces')[0] == 200
    assert request(server, 'GET', '/api/jobs/history-0')[1]['job_id'] == 'history-0'
    key = 'request-key-0000000000000000'
    assert request(server, 'GET', '/api/requests/' + key)[1]['job_id'] == 'history-0'
    status, replay = request(server, 'POST', f'/api/workspaces/{root.name}/jobs', {**payload, 'request_key': key})
    assert status == 202 and replay['job_id'] == 'history-0'
    assert not service.supervisor.owned
