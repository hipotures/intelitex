"""Root-owned JSON job records with in-memory indexes and bounded event replay."""
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import re
import os
import threading
import uuid

from ..util import PipelineError, atomic_json, digest, file_lock, read_json
from .models import ACTIVE, TERMINAL, Job, now
from .protocol import public_envelope

FORMAT = 1
ACTIVITY_LIMIT = 120


def registry_path(root: Path) -> Path:
    return root / '.runtime'


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) or '..' in value:
        raise PipelineError('Invalid runtime identity.')
    return value


def packed_record(record):
    return {'record': record, 'sha256': digest(record)}


def read_record(path):
    if path.is_symlink():
        raise PipelineError('Unsafe runtime record.')
    try:
        value = read_json(path)
        record = value['record']
        if set(value) != {'record', 'sha256'} or value['sha256'] != digest(record):
            raise ValueError('checksum')
        return record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PipelineError('Missing or corrupt JSON job record. No SQLite fallback.') from exc


def job_value(job):
    return {key: value for key, value in asdict(job).items() if key not in {'workspace_root', 'project'}}


class JobRegistry:
    def __init__(self, workspace_root: Path, *, event_limit: int = 1000):
        if type(event_limit) is not int or event_limit < 1:
            raise ValueError('event_limit must be positive')
        self.root = workspace_root.resolve()
        self.path = registry_path(self.root)
        if self.path.is_symlink() or (self.root / '.runtime.lock').is_symlink():
            raise PipelineError('Unsafe runtime directory.')
        self._owner = file_lock(self.root, '.runtime.lock', 'Another Intelitex server owns this job registry.')
        self._owner.__enter__()
        self.lock = threading.RLock()
        self.event_limit = event_limit
        self._closed = self._failed = False
        try:
            header = self.path / 'registry.json'
            if not self.path.exists():
                self.path.mkdir(mode=0o700)
                (self.path / 'jobs').mkdir(mode=0o700)
                atomic_json(header, {'format_version': FORMAT, 'scope_id': uuid.uuid4().hex, 'cursor_floor': 0, 'jobs': {}})
                descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if header.is_symlink() or (self.path / 'jobs').is_symlink():
                raise PipelineError('Unsafe runtime metadata.')
            metadata = read_json(header)
            if (set(metadata) != {'format_version', 'scope_id', 'cursor_floor', 'jobs'} or metadata['format_version'] != FORMAT
                    or type(metadata['cursor_floor']) is not int or metadata['cursor_floor'] < 0 or not isinstance(metadata['jobs'], dict)):
                raise PipelineError('Unsupported runtime JSON format.')
            self._metadata = metadata
            self.scope_id = _identifier(metadata['scope_id'])
            self._records = {}
            self._jobs = {}
            self._requests = {}
            self._workspace_jobs = defaultdict(list)
            self._active = {}
            self._latest = {}
            self._events = deque(maxlen=event_limit)
            self._activity = defaultdict(lambda: deque(maxlen=ACTIVITY_LIMIT))
            self._cursor, self._order = metadata['cursor_floor'], 0
            records = []
            for ident, checksum in metadata['jobs'].items():
                _identifier(ident)
                if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
                    raise PipelineError('Invalid runtime record reference.')
                record = read_record(self.path / 'jobs' / (checksum + '.json'))
                if digest(record) != checksum or record['job']['job_id'] != ident:
                    raise PipelineError('Runtime reference differs from record identity.')
                records.append(record)
            orders, events = set(), {}
            for record in sorted(records, key=lambda r: r['order']):
                self._validate(record)
                ident = record['job']['job_id']
                if ident in self._records or record['order'] in orders:
                    raise PipelineError('Duplicate runtime job/order.')
                orders.add(record['order'])
                for receipt in record['requests']:
                    if receipt['key'] in self._requests:
                        raise PipelineError('Duplicate runtime request receipt.')
                for event in record['events']:
                    if event['id'] in events:
                        raise PipelineError('Duplicate runtime event ID.')
                    events[event['id']] = event
                self._accept(record)
            self._events.extend(events[k] for k in sorted(events)[-event_limit:])
            for key in sorted(events):
                self._activity[events[key]['workspace_id']].append(events[key])
            for job in list(self._active.values()):
                self.save(replace(job, state='abandoned', pid=None, finished_at=now(), error={
                    'type': 'ServerRestart', 'message': 'Previous server stopped; worker not re-adopted.'}))
        except BaseException:
            self._owner.__exit__(None, None, None)
            raise

    def _validate(self, record):
        if (set(record) != {'format_version', 'scope_id', 'order', 'job', 'requests', 'events'}
                or record['format_version'] != FORMAT or record['scope_id'] != self.scope_id
                or type(record['order']) is not int or record['order'] < 1):
            raise PipelineError('Invalid runtime record format.')
        value = record['job']
        _identifier(value['job_id'])
        workspace = _identifier(value['workspace_id'])
        if 'project' in value or 'workspace_root' in value or value['state'] not in ACTIVE | TERMINAL:
            raise PipelineError('Invalid persisted job identity/state.')
        Job(workspace_root=str(self.root), project=str(self.root / workspace), **value)
        if type(value['sequence']) is not int or value['sequence'] < 0:
            raise PipelineError('Invalid job sequence.')
        last_id = last_sequence = 0
        for event in record['events']:
            if (event['job_id'] != value['job_id'] or event['workspace_id'] != workspace
                    or type(event['id']) is not int or event['id'] <= last_id
                    or type(event['sequence']) is not int or event['sequence'] <= last_sequence
                    or event['sequence'] > value['sequence']):
                raise PipelineError('Invalid retained runtime event.')
            last_id, last_sequence = event['id'], event['sequence']
        latest = value['last_event']
        if value['sequence']:
            if (not latest or latest.get('sequence') != value['sequence'] or latest.get('job_id') != value['job_id']
                    or latest.get('workspace_id') != workspace or type(latest.get('id')) is not int
                    or latest['id'] < last_id):
                raise PipelineError('Invalid latest runtime event.')
        for receipt in record['requests']:
            if (set(receipt) != {'key', 'digest'} or not isinstance(receipt['key'], str) or not receipt['key']
                    or not isinstance(receipt['digest'], str) or not re.fullmatch('[0-9a-f]{64}', receipt['digest'])):
                raise PipelineError('Invalid runtime request receipt.')

    def _accept(self, record):
        value = record['job']
        ident, workspace = value['job_id'], value['workspace_id']
        if ident not in self._records:
            self._workspace_jobs[workspace].append(ident)
        self._records[ident] = record
        job = Job(workspace_root=str(self.root), project=str(self.root / workspace), **value)
        self._jobs[ident] = job
        self._order = max(self._order, record['order'])
        self._cursor = max(self._cursor, (job.last_event or {}).get('id', 0))
        previous = self._latest.get(workspace)
        if previous is None or self._records[previous]['order'] <= record['order']:
            self._latest[workspace] = ident
        if job.state in ACTIVE:
            self._active[ident] = job
        else:
            self._active.pop(ident, None)
        for receipt in record['requests']:
            self._requests[receipt['key']] = (receipt['digest'], ident)

    def _write(self, record):
        if self._closed or self._failed:
            raise PipelineError('Job registry is closed or requires recovery after a write failure.')
        self._validate(record)
        ident, checksum = record['job']['job_id'], digest(record)
        metadata = {**self._metadata, 'jobs': {**self._metadata['jobs'], ident: checksum},
                    'cursor_floor': max(self._cursor, (record['job']['last_event'] or {}).get('id', 0))}
        previous_checksum = self._metadata['jobs'].get(ident)
        try:
            atomic_json(self.path / 'jobs' / (checksum + '.json'), packed_record(record))
            atomic_json(self.path / 'registry.json', metadata)
        except BaseException:
            # Do not acknowledge another mutation after a durability failure.
            self._failed = True
            raise
        self._metadata = metadata
        self._accept(record)
        if previous_checksum and previous_checksum != checksum and previous_checksum not in metadata['jobs'].values():
            try:
                (self.path / 'jobs' / (previous_checksum + '.json')).unlink(missing_ok=True)
            except OSError:
                pass  # A cleanup failure cannot undo the committed record.

    def _scope(self, scope):
        return scope == self.scope_id or scope == str(self.root)

    def save(self, job: Job):
        with self.lock:
            if not self._scope(job.workspace_root) or Path(job.project) != self.root / job.workspace_id:
                raise PipelineError('Job belongs to another workspace root.')
            previous = self._records.get(job.job_id)
            if previous and previous['job']['workspace_id'] != job.workspace_id:
                raise PipelineError('Cannot change job workspace identity.')
            if previous and (previous['job']['operation'] != job.operation or previous['job']['sequence'] != job.sequence
                             or previous['job']['last_event'] != job.last_event):
                raise PipelineError('Job identity/progress changed; append events through the registry.')
            record = deepcopy(previous) if previous else {
                'format_version': FORMAT, 'scope_id': self.scope_id, 'order': self._order + 1,
                'requests': [], 'events': [],
            }
            record['job'] = job_value(job)
            self._write(record)

    def request(self, scope, key, fingerprint):
        with self.lock:
            row = self._requests.get(key) if self._scope(scope) else None
            if row is None:
                return None
            if row[0] != fingerprint:
                from .supervisor import RequestConflict
                raise RequestConflict('Request key reused with different input.')
            return self.get(row[1])

    def receipt(self, scope, key):
        with self.lock:
            if not self._scope(scope) or key not in self._requests:
                raise KeyError(key)
            return self.get(self._requests[key][1])

    def reserve(self, job, key, fingerprint):
        with self.lock:
            if job.job_id in self._records or key is not None and key in self._requests:
                from .supervisor import RequestConflict
                raise RequestConflict('Job or request already reserved.')
            if not self._scope(job.workspace_root) or Path(job.project) != self.root / job.workspace_id:
                raise PipelineError('Job belongs to another workspace root.')
            self._write({'format_version': FORMAT, 'scope_id': self.scope_id, 'order': self._order + 1,
                         'job': job_value(job), 'events': [],
                         'requests': [{'key': key, 'digest': fingerprint}] if key is not None else []})

    def get(self, job_id: str) -> Job:
        with self.lock:
            return deepcopy(self._jobs[job_id])

    def list(self) -> list[Job]:
        """Explicit full history; normal UI reads use current()/latest()/active()."""
        with self.lock:
            return [self.get(ident) for ident in self._records]

    def current(self, workspace_id=None, job_id=None):
        with self.lock:
            if job_id is not None:
                job = self.get(job_id)
                return [job] if workspace_id is None or job.workspace_id == workspace_id else []
            ids = set(self._active) | set(self._latest.values())
            return [self.get(ident) for ident in sorted(ids, key=lambda i: self._records[i]['order'])
                    if workspace_id is None or self._jobs[ident].workspace_id == workspace_id]

    def latest(self, workspace_id, *, exclude=None):
        with self.lock:
            for ident in reversed(self._workspace_jobs.get(workspace_id, [])):
                job = self._jobs[ident]
                if exclude is None or not exclude(job):
                    return deepcopy(job)
            return None

    def active(self, workspace_id):
        with self.lock:
            return next((deepcopy(job) for job in self._active.values() if job.workspace_id == workspace_id), None)

    def append(self, job_id: str, event: dict) -> dict:
        with self.lock:
            job = self.get(job_id)
            envelope = {'id': self._cursor + 1, 'job_id': job_id, 'workspace_id': job.workspace_id,
                        'sequence': job.sequence + 1, 'timestamp': now(), 'event': deepcopy(event)}
            record = deepcopy(self._records[job_id])
            record['job'] = job_value(replace(job, sequence=envelope['sequence'], last_event=envelope))
            record['events'] = (record['events'] + [envelope])[-min(self.event_limit, ACTIVITY_LIMIT):]
            self._write(record)
            self._events.append(envelope)
            self._activity[job.workspace_id].append(envelope)
            return deepcopy(envelope)

    def cursor(self) -> int:
        with self.lock:
            return self._cursor

    def events(self, after: int, *, workspace_root: str, workspace_id=None, job_id=None) -> list[dict]:
        with self.lock:
            if not self._scope(workspace_root):
                return []
            return [public_envelope(e) for e in self._events if e['id'] > after
                    and (workspace_id is None or e['workspace_id'] == workspace_id)
                    and (job_id is None or e['job_id'] == job_id)]

    def recent_events(self, *, workspace_root, workspace_id, job_id=None, limit=120):
        with self.lock:
            if not self._scope(workspace_root):
                return []
            events = self._activity.get(workspace_id, ()) if job_id is None else (
                self._records[job_id]['events'] if job_id in self._records and self._jobs[job_id].workspace_id == workspace_id else ())
            return [public_envelope(e) for e in list(events)[-min(limit, ACTIVITY_LIMIT):]]

    def close(self):
        with self.lock:
            if not self._closed:
                self._closed = True
                self._owner.__exit__(None, None, None)
