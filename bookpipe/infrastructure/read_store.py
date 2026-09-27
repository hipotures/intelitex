"""Read-only JSON state snapshots, sharing checkpoint validation with the writer."""
from pathlib import Path
import json
from ..state_files import connect_state

from ..store import Store
from ..util import PipelineError, digest


class ReadStore:
    translated_dependencies = Store.translated_dependencies
    # These methods only SELECT and validate immutable checkpoint artifacts.
    p1_reset_records = Store.p1_reset_records
    has_dependent_p1_work = Store.has_dependent_p1_work
    close = Store.close

    def __init__(self, root: Path):
        self.root = root
        self.db = connect_state(root, readonly=True)
        try:
            self.db.execute("BEGIN")
            # Establish the snapshot now, before any artifact reads.
            self.db.execute("SELECT key FROM kv LIMIT 1").fetchone()
            self._kv = {row['key']: json.loads(row['value']) for row in self.db.execute('SELECT key,value FROM kv')}
            self._chunks = {row['id']: dict(row) for row in self.db.execute('SELECT * FROM chunks')}
            self._jobs = {(row['key'], row['fingerprint']): dict(row)
                          for row in self.db.execute('SELECT * FROM jobs ORDER BY rowid')}
            self._paths = {row['result_path']: row for row in self._jobs.values()}
            self._latest = {row['key']: row for row in self._jobs.values()}
            self._results = {}
            self._terms = None
        except BaseException:
            self.db.close()
            raise

    def get(self, key, default=None):
        return self._kv.get(key, default)

    def terms(self):
        if self._terms is None:
            self._terms = Store.terms(self)
        return self._terms

    def chunk(self, cid):
        return self._chunks.get(cid, {'id': cid, 'status': 'pending', 'final_path': None, 'deps': '[]'})

    def _result(self, row):
        relative = row['result_path']
        identity = (relative, row['result_hash'])
        if identity not in self._results:
            try:
                raw = (self.root / relative).read_bytes()
            except OSError as exc:
                raise PipelineError('Checkpoint missing or changed: ' + relative) from exc
            if digest(raw) != row['result_hash']:
                raise PipelineError('Checkpoint missing or changed: ' + relative)
            self._results[identity] = json.loads(raw)
        return self._results[identity]

    def job(self, key, fingerprint):
        row = self._jobs.get((key, fingerprint))
        return {'value': self._result(row), 'path': row['result_path'],
                'meta': json.loads(row['metadata'])} if row else None

    def checked_result(self, relative):
        row = self._paths.get(relative)
        if row is None:
            raise PipelineError('Unregistered final artifact: ' + relative)
        return self._result(row)

    def checkpoint_inventory(self) -> dict[str, list[str]]:
        """Retained identities, not a claim that old fingerprints remain current."""
        result = {}
        for row in self._jobs.values():
            result.setdefault(row['key'], []).append(row['fingerprint'])
        return result

    def latest_job(self, key: str) -> dict | None:
        row = self._latest.get(key)
        return self.job(key, row['fingerprint']) if row else None

    def job_for_path(self, key: str, relative_path: str) -> dict | None:
        row = self._paths.get(relative_path)
        if row and row['key'] != key:
            return None
        return self.job(key, row['fingerprint']) if row else None
