"""Private WAL registry; never a substitute for project checkpoints."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import threading

from ..util import file_lock
from .models import ACTIVE, Job, now
from .protocol import public_envelope


def default_registry_path() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".local" / "state"
    return base / "intelitex" / "jobs.sqlite3"


class JobRegistry:
    def __init__(self, path: Path, *, event_limit: int = 1000):
        if event_limit < 1:
            raise ValueError("event_limit must be positive")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._owner = file_lock(path.parent, path.name + ".lock", "Another Intelitex server owns this job registry.")
        self._owner.__enter__()
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
            os.chmod(path, 0o600)
            self.db.executescript('''
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_job ON events(job_id, sequence);
            ''')
            self.lock = threading.RLock()
            self.event_limit = event_limit
            for job in self.list():
                if job.state in ACTIVE:
                    self.save(replace(job, state="abandoned", finished_at=now(), error={
                        "type": "ServerRestart", "message": "Previous server stopped; worker not re-adopted."}))
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._owner.__exit__(None, None, None)
            raise

    def save(self, job: Job):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job.job_id, json.dumps(asdict(job))))

    def get(self, job_id: str) -> Job:
        with self.lock:
            row = self.db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return Job(**json.loads(row[0]))

    def list(self) -> list[Job]:
        with self.lock:
            return [Job(**json.loads(row[0])) for row in self.db.execute("SELECT data FROM jobs ORDER BY rowid")]

    def append(self, job_id: str, event: dict) -> dict:
        with self.lock, self.db:
            job = self.get(job_id)
            sequence = job.sequence + 1
            envelope = {"job_id": job_id, "workspace_id": job.workspace_id,
                        "sequence": sequence, "timestamp": now(), "event": event}
            cursor = self.db.execute("INSERT INTO events(job_id, sequence, data) VALUES (?,?,?)",
                                     (job_id, sequence, json.dumps(envelope)))
            envelope["id"] = cursor.lastrowid
            job = replace(job, sequence=sequence, last_event=envelope)
            self.db.execute("UPDATE jobs SET data=? WHERE job_id=?", (json.dumps(asdict(job)), job_id))
            self.db.execute("DELETE FROM events WHERE job_id=? AND sequence<=?",
                            (job_id, sequence - self.event_limit))
            return envelope

    def cursor(self) -> int:
        with self.lock:
            row = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
            return row[0] if row else 0

    def events(self, after: int, *, workspace_root: str, workspace_id=None, job_id=None) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, data FROM events WHERE id>? ORDER BY id", (after,)).fetchall()
            allowed = {j.job_id for j in self.list() if j.workspace_root == workspace_root
                       and (workspace_id is None or j.workspace_id == workspace_id)
                       and (job_id is None or j.job_id == job_id)}
            return [public_envelope({**json.loads(data), "id": ident})
                    for ident, data in rows if json.loads(data)["job_id"] in allowed]

    def close(self):
        with self.lock:
            self.db.close()
            self._owner.__exit__(None, None, None)
