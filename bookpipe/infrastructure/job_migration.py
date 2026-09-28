"""Explicit one-way migration of the old global job registry. Never a runtime fallback."""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import uuid

from ..runtime.models import Job
from ..runtime.registry import ACTIVITY_LIMIT, FORMAT, JobRegistry, job_value, packed_record, registry_path, _identifier
from ..util import PipelineError, atomic_json, digest, file_lock, read_json


def legacy_registry_path():
    configured = os.environ.get('XDG_STATE_HOME')
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / '.local' / 'state'
    return base / 'intelitex' / 'jobs.sqlite3'


def _files(source):
    return {str(p): digest(p.read_bytes()) if p.is_file() else None
            for p in (source, Path(str(source) + '-wal'), Path(str(source) + '-shm'))}


def _snapshot(source, previous_root):
    if not source.is_file() or source.is_symlink():
        raise PipelineError('Legacy job registry is missing or unsafe.')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise PipelineError('Legacy job registry failed integrity_check.')
        expected = {'jobs': ('job_id', 'data'), 'events': ('id', 'job_id', 'sequence', 'data'),
                    'requests': ('scope', 'request_key', 'digest', 'job_id'), 'sqlite_sequence': ('name', 'seq')}
        objects = db.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex_%'").fetchall()
        if {name for kind, name in objects if kind == 'table'} != set(expected) or any(kind not in {'table', 'index'} for kind, _ in objects):
            raise PipelineError('Unknown legacy registry schema; refusing incomplete migration.')
        for table, columns in expected.items():
            if tuple(row[1] for row in db.execute(f'PRAGMA table_info({table})')) != columns:
                raise PipelineError('Unknown legacy registry columns.')
        import json
        jobs = []
        all_ids = set()
        for order, ident, raw in db.execute('SELECT rowid,job_id,data FROM jobs ORDER BY rowid'):
            value = json.loads(raw)
            all_ids.add(ident)
            if value['workspace_root'] != str(previous_root):
                continue
            job = Job(**value)
            _identifier(job.workspace_id)
            _identifier(job.job_id)
            if ident != job.job_id or Path(job.project) != previous_root / job.workspace_id:
                raise PipelineError('Legacy job has inconsistent workspace identity.')
            jobs.append({'order': order, 'job': value})
        ids = {item['job']['job_id'] for item in jobs}
        events = []
        for ident, job_id, sequence, raw in db.execute('SELECT * FROM events ORDER BY id'):
            if job_id not in all_ids:
                raise PipelineError('Legacy registry has an orphan event.')
            if job_id not in ids:
                continue
            value = json.loads(raw)
            if value['job_id'] != job_id or value['sequence'] != sequence:
                raise PipelineError('Legacy event identity differs from its row.')
            events.append({**value, 'id': ident})
        requests = []
        for scope, key, fingerprint, job_id in db.execute('SELECT * FROM requests ORDER BY rowid'):
            if scope != str(previous_root):
                continue
            if job_id not in ids:
                raise PipelineError('Legacy registry has an orphan request receipt.')
            requests.append({'key': key, 'digest': fingerprint, 'job_id': job_id})
        row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
        cursor = row[0] if row else 0
        if cursor < max((e['id'] for e in events), default=0):
            raise PipelineError('Legacy event allocator is inconsistent.')
        return {'jobs': jobs, 'events': events, 'requests': requests, 'cursor': cursor,
                'other_scope_jobs': len(all_ids) - len(ids)}


def verify_jobs(root):
    path = registry_path(root.resolve())
    manifest = path / 'history' / 'migration.json'
    if not manifest.is_file():
        raise PipelineError('No job migration audit record.')
    audit = read_json(manifest)
    export = read_json(path / 'history' / 'legacy-export.json')
    if digest(export) != audit['export_sha256']:
        raise PipelineError('Job migration export checksum mismatch.')
    changed = [name for name, expected in audit['retired_files'].items()
               if (digest(Path(name).read_bytes()) if Path(name).is_file() else None) != expected]
    # Validation without restarting/reconciling active jobs in the live registry.
    from ..runtime.registry import read_record
    for _ in range(3):
        header = read_json(path / 'registry.json')
        validator = object.__new__(JobRegistry)
        validator.root, validator.scope_id = root.resolve(), header['scope_id']
        try:
            for ident, checksum in header['jobs'].items():
                if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
                    raise PipelineError('Invalid runtime reference.')
                record = read_record(path / 'jobs' / (checksum + '.json'))
                validator._validate(record)
                if digest(record) != checksum or record['job']['job_id'] != ident:
                    raise PipelineError('Runtime reference differs from record identity.')
        except PipelineError:
            if read_json(path / 'registry.json') != header:
                continue  # A writer may have retired the previous record version.
            raise
        if read_json(path / 'registry.json') == header:
            break
    else:
        raise PipelineError('Job registry changed during verification; retry when idle.')
    return {'verified': not changed, 'retired_files_changed': changed,
            'jobs': len(export['jobs']), 'events_archived': len(export['events']), 'requests': len(export['requests'])}


def migrate_jobs(workspace_root, source=None, *, previous_root=None):
    root = workspace_root.resolve(strict=True)
    source = (source or legacy_registry_path()).resolve()
    previous = (previous_root or root).resolve()
    destination = registry_path(root)
    if destination.is_symlink() or (root / '.runtime.lock').is_symlink():
        raise PipelineError('Unsafe runtime migration destination.')
    with file_lock(root, '.runtime.lock', 'Another Intelitex server owns this job registry.'), \
            file_lock(source.parent, source.name + '.lock', 'Stop or reload the old server before migrating jobs.'):
        if destination.exists():
            return {**verify_jobs(root), 'status': 'already_migrated'}
        snapshot = _snapshot(source, previous)
        metadata = root / '.intelitex-web.json'
        scope_id = read_json(metadata)['scope_id'] if metadata.exists() else uuid.uuid4().hex
        _identifier(scope_id)
        stage = root / ('.runtime-migration-' + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        (stage / 'jobs').mkdir(mode=0o700)
        by_job, requests = defaultdict(list), defaultdict(list)
        for event in snapshot['events']:
            by_job[event['job_id']].append(event)
        for receipt in snapshot['requests']:
            requests[receipt['job_id']].append({key: receipt[key] for key in ('key', 'digest')})
        references = {}
        for item in snapshot['jobs']:
            job = Job(**item['job'])
            record = {'format_version': FORMAT, 'scope_id': scope_id, 'order': item['order'],
                      'job': job_value(job), 'requests': requests[job.job_id],
                      'events': by_job[job.job_id][-ACTIVITY_LIMIT:]}
            # Run the exact runtime validator before publishing anything.
            validator = object.__new__(JobRegistry)
            validator.root, validator.scope_id = root, scope_id
            validator._validate(record)
            references[job.job_id] = digest(record)
            path = stage / 'jobs' / (digest(record) + '.json')
            atomic_json(path, packed_record(record))
            if read_json(path) != packed_record(record):
                raise PipelineError('Job migration round-trip verification failed.')
        atomic_json(stage / 'history' / 'legacy-export.json', snapshot)
        atomic_json(stage / 'history' / 'migration.json', {
            'format_version': 1, 'export_sha256': digest(snapshot), 'retired_files': _files(source),
            'previous_root': str(previous), 'scope_id': scope_id,
        })
        atomic_json(stage / 'registry.json', {'format_version': FORMAT, 'scope_id': scope_id,
                    'cursor_floor': snapshot['cursor'], 'jobs': references})
        # Publish the complete registry in one rename. Unpublished staging trees
        # are never adopted on retry; original SQLite is never modified.
        os.rename(stage, destination)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {**verify_jobs(root), 'status': 'migrated', 'other_scope_jobs': snapshot['other_scope_jobs']}


def request_migration_on_reload(root, source=None):
    """Explicit operator intent consumed once, after the old server releases its lock."""
    root = root.resolve(strict=True)
    source = (source or legacy_registry_path()).resolve(strict=True)
    if registry_path(root).exists():
        raise PipelineError('JSON job registry already exists.')
    snapshot = _snapshot(source, root)  # Read-only rehearsal; final migration takes a fresh locked snapshot.
    path = root / '.job-migration-request.json'
    if path.is_symlink():
        raise PipelineError('Unsafe migration request.')
    atomic_json(path, {'format_version': 1, 'source': str(source), 'workspace_root': str(root)})
    return {'staged_for_reload': True, 'jobs': len(snapshot['jobs']), 'requests': len(snapshot['requests'])}


def complete_requested_migration(root):
    path = root / '.job-migration-request.json'
    if not path.exists():
        if not registry_path(root).exists() and legacy_registry_path().exists():
            raise PipelineError('Legacy job registry exists. Run migrate-jobs explicitly before starting this root.')
        return
    if path.is_symlink():
        raise PipelineError('Unsafe migration request.')
    request = read_json(path)
    if set(request) != {'format_version', 'source', 'workspace_root'} or request['format_version'] != 1 or request['workspace_root'] != str(root):
        raise PipelineError('Invalid explicit job migration request.')
    result = migrate_jobs(root, Path(request['source']))
    if not result['verified']:
        raise PipelineError('Job migration verification failed.')
    path.unlink()
