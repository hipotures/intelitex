"""Read-only WAL snapshots, sharing checkpoint validation with the writer."""
from pathlib import Path
import sqlite3

from ..store import Store


class ReadStore:
    # These methods only SELECT and validate immutable checkpoint artifacts.
    get = Store.get
    terms = Store.terms
    chunk = Store.chunk
    checked_result = Store.checked_result
    job = Store.job
    p1_reset_records = Store.p1_reset_records
    has_dependent_p1_work = Store.has_dependent_p1_work
    close = Store.close

    def __init__(self, root: Path):
        self.root = root
        self.db = sqlite3.connect((root / "state.sqlite3").as_uri() + "?mode=ro", uri=True)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            self.db.execute("BEGIN")
            # Establish the snapshot now, before any artifact reads.
            self.db.execute("SELECT key FROM kv LIMIT 1").fetchone()
        except BaseException:
            self.db.close()
            raise

    def checkpoint_inventory(self) -> dict[str, list[str]]:
        """Retained identities, not a claim that old fingerprints remain current."""
        result = {}
        for row in self.db.execute("SELECT key,fingerprint FROM jobs"):
            result.setdefault(row['key'], []).append(row['fingerprint'])
        return result
