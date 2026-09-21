from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

from .util import PipelineError, atomic_json, atomic_text, digest, dumps, normalized, occurs, read_json, unique


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.db = sqlite3.connect(root / "state.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            key TEXT NOT NULL, fingerprint TEXT NOT NULL, result_path TEXT NOT NULL,
            result_hash TEXT NOT NULL, metadata TEXT NOT NULL,
            PRIMARY KEY(key,fingerprint));
        CREATE TABLE IF NOT EXISTS merged (key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            PRIMARY KEY(key,fingerprint));
        CREATE TABLE IF NOT EXISTS terms (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
            choice TEXT, approved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
            final_path TEXT, deps TEXT NOT NULL DEFAULT '[]', lexical_hash TEXT);
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, data TEXT);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def save_analysis_receipt(self, unit_id: str, receipt: dict) -> None:
        """Persist one P1 receipt atomically without exposing the connection."""
        with self.db:
            self.set("analysis:" + unit_id, receipt)

    def finish_analysis(self) -> None:
        with self.db:
            self.set("analysis_done", True)

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any):
        self.db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, dumps(value)))

    def note(self, kind: str, data: Any):
        self.db.execute("INSERT INTO history(kind,data) VALUES(?,?)", (kind, dumps(data)))

    def terms(self) -> list[dict]:
        out = []
        for row in self.db.execute("SELECT * FROM terms ORDER BY id"):
            data = json.loads(row["data"])
            data.update(id=f"T{row['id']:06d}", choice=row["choice"], approved=bool(row["approved"]))
            out.append(data)
        return out

    def facts(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM facts ORDER BY id")]

    def job(self, key: str, fingerprint: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE key=? AND fingerprint=?", (key, fingerprint)).fetchone()
        if not row:
            return None
        path = self.root / row["result_path"]
        if not path.exists() or digest(path.read_bytes()) != row["result_hash"]:
            raise PipelineError(f"Checkpoint missing or changed: {path}. Restore it; completed work is not silently recomputed.")
        return {"value": read_json(path), "path": row["result_path"], "meta": json.loads(row["metadata"])}

    def checked_result(self, relative: str) -> dict:
        row = self.db.execute("SELECT result_hash FROM jobs WHERE result_path=?", (relative,)).fetchone()
        path = self.root / relative
        if not row or not path.is_file() or digest(path.read_bytes()) != row[0]:
            raise PipelineError(f"Final artifact is missing or changed: {path}. Restore it instead of silently overwriting completed work.")
        return read_json(path)

    def save_job(self, key: str, fingerprint: str, path: Path, metadata: dict):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?)",
                            (key, fingerprint, str(path.relative_to(self.root)), digest(path.read_bytes()), dumps(metadata)))

    def register_chunks(self, book: dict):
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO chunks(id) VALUES(?)", [(c["id"],) for c in book["chunks"]])

    def seed_series(self, seed: dict):
        """Seed inherited memory atomically without treating the new book as approved."""
        if self.db.execute("SELECT 1 FROM terms LIMIT 1").fetchone() or self.db.execute("SELECT 1 FROM facts LIMIT 1").fetchone():
            raise PipelineError("Series memory can only seed an empty new Store.")
        terms = seed.get("terms")
        observations = seed.get("observations")
        if not isinstance(terms, list) or not isinstance(observations, list):
            raise PipelineError("series.seed.json has invalid terms or observations.")
        with self.db:
            for term in terms:
                meanings = [{
                    "text": note["text"],
                    "confidence": note["confidence"],
                    "evidence": [],
                    "series_inherited": True,
                    "series_first_seen_volume": term["first_seen_volume"],
                } for note in term["meaning_notes"]]
                candidate = {
                    "text": term["polish"],
                    "reasons": ["Approved in the previous series volume."],
                    "evidence": [],
                    "confidence": "high",
                    "series_inherited": True,
                }
                data = {
                    "source": term["source"],
                    "aliases": copy.deepcopy(term["aliases"]),
                    "category": term["category"],
                    "meanings": meanings,
                    "candidates": [candidate],
                    "evidence": [],
                    "series_context": copy.deepcopy(term["series_context"]),
                    "series_inherited": True,
                    "series_first_seen_volume": term["first_seen_volume"],
                    "series_review_required": False,
                }
                self.db.execute(
                    "INSERT INTO terms(data,choice,approved) VALUES(?,?,1)",
                    (dumps(data), term["polish"]),
                )
            for observation in observations:
                item = {
                    "about": copy.deepcopy(observation["about"]),
                    "kind": observation["kind"],
                    "statement": observation["statement"],
                    "confidence": observation["confidence"],
                    "evidence": [],
                    "series_inherited": True,
                    "series_first_seen_volume": observation["first_seen_volume"],
                }
                self.db.execute("INSERT INTO facts VALUES(?,?)", (digest(item), dumps(item)))
            self.set("series_seed", {
                "format_version": seed["format_version"],
                "series_id": seed["series_id"],
                "from_volume": seed["from_volume"],
                "to_volume": seed["to_volume"],
                "source_snapshot": copy.deepcopy(seed["source_snapshot"]),
            })
        self.export_memory()

    def chunk(self, cid: str) -> dict:
        row = self.db.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else {"id": cid, "status": "pending", "final_path": None, "deps": "[]"}

    def finish_chunk(self, cid: str, path: str, deps: list[str], lexical_hash: str):
        with self.db:
            self.db.execute("UPDATE chunks SET status='done',final_path=?,deps=?,lexical_hash=? WHERE id=?",
                            (path, dumps(deps), lexical_hash, cid))

    def merge_analysis(self, key: str, fingerprint: str, delta: dict, blocks: list[dict], chapter_id: str):
        """Idempotent: replaying a completed Pass 1 cannot duplicate candidates."""
        if self.db.execute("SELECT 1 FROM merged WHERE key=? AND fingerprint=?", (key, fingerprint)).fetchone():
            return
        blockmap = {b["id"]: b for b in blocks}
        terms = self.terms()
        with self.db:
            for incoming in delta["terms"]:
                names = unique([incoming["source"]] + incoming["aliases"])
                wanted = {normalized(n) for n in names}
                matches = [t for t in terms if wanted & {normalized(n) for n in [t["source"]] + t["aliases"]}]
                if len(matches) > 1:
                    raise PipelineError(f"Ambiguous alias merge for {incoming['source']!r}; review analysis rather than conflating two entities.")
                existing = matches[0] if matches else None
                evidence = [{"block_id": bid, "chapter_id": chapter_id, "order": blockmap[bid]["order"],
                             "excerpt": blockmap[bid]["text"][:900]} for bid in incoming["evidence"]]
                if existing:
                    data = copy.deepcopy(existing)
                    for k in ("id", "choice", "approved"):
                        data.pop(k, None)
                else:
                    data = {"source": incoming["source"], "aliases": [], "category": incoming["category"],
                            "meanings": [], "candidates": [], "evidence": []}
                if existing and data.get("series_inherited") is True:
                    disagreement = []
                    preferred = incoming["candidates"][0]["text"]
                    if normalized(preferred) != normalized(existing["choice"] or ""):
                        disagreement.append("preferred_candidate")
                    if incoming["category"] != existing["category"]:
                        disagreement.append("category")
                        data["category"] = incoming["category"]
                    if disagreement:
                        data["series_review_required"] = True
                        data["series_disagreement"] = unique(data.get("series_disagreement", []) + disagreement)
                data["aliases"] = unique(data["aliases"] + [n for n in names if normalized(n) != normalized(data["source"])])
                data["evidence"] = unique(data["evidence"] + evidence)
                data["meanings"] = unique(data["meanings"] + [{"text": incoming["meaning"],
                    "confidence": incoming["confidence"], "evidence": incoming["evidence"]}])
                for proposal in incoming["candidates"]:
                    candidate = next((c for c in data["candidates"] if normalized(c["text"]) == normalized(proposal["text"])), None)
                    if candidate is None:
                        candidate = {"text": proposal["text"], "reasons": [], "evidence": [], "confidence": incoming["confidence"]}
                        data["candidates"].append(candidate)
                    candidate["reasons"] = unique(candidate["reasons"] + [proposal["reason"]])
                    candidate["evidence"] = unique(candidate["evidence"] + incoming["evidence"])
                # All candidates remain stored. Only the current request is bounded.
                if existing:
                    tid = int(existing["id"][1:])
                    self.db.execute("UPDATE terms SET data=? WHERE id=?", (dumps(data), tid))
                    existing.update(data)
                else:
                    cur = self.db.execute("INSERT INTO terms(data) VALUES(?)", (dumps(data),))
                    tid = cur.lastrowid
                    data_for_index = copy.deepcopy(data)
                    data_for_index.update(id=f"T{tid:06d}", choice=None, approved=False)
                    terms.append(data_for_index)
                self.note("term_evidence", {"term_id": f"T{tid:06d}", "analysis_job": key, "candidates": incoming["candidates"]})
            for fact in delta["observations"]:
                item = copy.deepcopy(fact)
                item["chapter_id"] = chapter_id
                item["available_from_order"] = max(blockmap[b]["order"] for b in item["evidence"])
                fid = digest(item)
                self.db.execute("INSERT OR IGNORE INTO facts VALUES(?,?)", (fid, dumps(item)))
            self.db.execute("INSERT INTO merged VALUES(?,?)", (key, fingerprint))
        self.export_memory()

    def export_memory(self):
        atomic_json(self.root / "book_memory.json", {"format_version": 1, "terms": self.terms(), "observations": self.facts()})

    def analysis_memory(self, source: str, count, limit: int) -> dict:
        """Never drop directly matching lexical records to make a request look small."""
        items = self.terms()
        matched, other = [], []
        for term in items:
            meaning_notes = [x["text"] for x in term["meanings"][-2:]]
            if term.get("series_inherited") is True:
                for context in term.get("series_context", [])[:3]:
                    statement = context.get("statement")
                    if isinstance(statement, str) and statement not in meaning_notes:
                        meaning_notes.append(statement)
            compact = {"id": term["id"], "source": term["source"], "aliases": term["aliases"],
                       "chosen": term["choice"], "candidates": [c["text"] for c in term["candidates"]],
                       "meaning_notes": meaning_notes}
            bucket = matched if any(occurs(source, s) for s in [term["source"]] + term["aliases"]) else other
            bucket.append(compact)
        result = {"matched": matched, "catalogue": [], "catalogue_incomplete": False}
        if count(dumps(result)) > limit:
            raise PipelineError("Directly relevant analysis memory exceeds memory_tokens. Increase the limit or use smaller analysis sections.")
        # A bounded directory helps recognize aliases without replaying all chapters.
        for term in other:
            entry = {"id": term["id"], "source": term["source"], "aliases": term["aliases"]}
            result["catalogue"].append(entry)
            if count(dumps(result)) > limit:
                result["catalogue"].pop()
                result["catalogue_incomplete"] = True
                break
        return result

    def write_review(self, book_fingerprint: str) -> Path:
        path = self.root / "terms.review.json"
        revision = digest(self.terms())
        if path.exists():
            previous = read_json(path)
            if previous.get("analysis_revision") == revision:
                return path  # Never erase pending human edits.
            atomic_json(self.root / "history" / f"review_{digest(previous)[:16]}.json", previous)
        result = {"format_version": 1, "book_fingerprint": book_fingerprint,
                  "analysis_revision": revision, "confirmed": False,
                  "instructions": "Review terms with `translate.py review`: choose a candidate or custom Polish form, mark each term reviewed, then confirm the glossary and run approve. Candidate history is preserved separately.",
                  "terms": []}
        facts = self.facts()
        for term in self.terms():
            candidates = [{"number": i, **c} for i, c in enumerate(term["candidates"], 1)]
            selected = next((c["number"] for c in candidates if c["text"] == term["choice"]), 1)
            term_names = {normalized(x) for x in [term["source"], *term["aliases"]]}
            observations = [copy.deepcopy(f) for f in facts
                            if any(normalized(a) in term_names for a in f.get("about", []) if isinstance(a, str))]
            inherited_reviewed = term.get("series_inherited") is True and not term.get("series_review_required", False)
            entry = {"id": term["id"], "source": term["source"], "aliases": term["aliases"],
                "category": term["category"], "meaning_notes": term["meanings"],
                "observations": observations, "candidates": candidates, "select": selected,
                "custom": term["choice"] if term["choice"] and not any(c["text"] == term["choice"] for c in candidates) else "",
                "reviewed": inherited_reviewed, "user_notes": "", "evidence": term["evidence"]}
            if term.get("series_inherited") is True:
                entry["review_method"] = "inherited" if inherited_reviewed else "series_disagreement"
                entry["series_review_required"] = bool(term.get("series_review_required"))
            result["terms"].append(entry)
        atomic_json(path, result)
        self.write_review_html(result)
        return path

    def write_review_html(self, review: dict):
        # Read-only local catalogue, not an external UI dependency.
        import html
        def e(v):
            return html.escape(str(v))
        rows = []
        for term in review["terms"]:
            options = "<br>".join(f"{c['number']}. <b>{e(c['text'])}</b> — {e('; '.join(c['reasons']))}" for c in term["candidates"])
            notes = "<br>".join(e(x["text"]) for x in term["meaning_notes"])
            rows.append(f"<tr><td>{e(term['id'])}</td><td>{e(term['source'])}</td><td>{options}</td><td>{notes}</td></tr>")
        document = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Terminology review</title><style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #bbb;padding:.6rem;vertical-align:top}th{text-align:left}tr:nth-child(even){background:#f6f6f6}</style><h1>Terminology review</h1><p>Edit terms.review.json to choose candidate numbers or enter custom forms. This page is read-only.</p><table><tr><th>ID</th><th>Source</th><th>Candidates</th><th>Meaning / uncertainty</th></tr>' + "".join(rows) + '</table></html>'
        atomic_text(self.root / "terms.review.html", document)

    def approve(self, fingerprint: str, accept_defaults: bool) -> tuple[int, int]:
        """Compatibility entry point; approval orchestration is application-owned."""
        from .application.review import execute_approval
        result = execute_approval(self, fingerprint, accept_defaults)
        return result.approved_terms, result.stale_chunks

    def backup_approval(self, review: dict) -> Path:
        target = self.root / "history" / f"before_approval_{digest(review)[:16]}.sqlite3"
        target.parent.mkdir(exist_ok=True)
        backup = sqlite3.connect(target)
        try:
            self.db.backup(backup)
        finally:
            backup.close()
        return target

    def commit_approval(self, decisions: list[tuple[dict, str]], changed: set[str]) -> int:
        stale = 0
        with self.db:
            for old, chosen in decisions:
                self.db.execute("UPDATE terms SET choice=?,approved=1 WHERE id=?", (chosen, int(old["id"][1:])))
                if old["choice"] != chosen or not old["approved"]:
                    self.note("human_choice", {"id": old["id"], "old": old["choice"], "new": chosen})
            for row in self.db.execute("SELECT id,deps FROM chunks WHERE status='done'").fetchall():
                if changed & set(json.loads(row["deps"])):
                    self.db.execute("UPDATE chunks SET status='stale' WHERE id=?", (row["id"],))
                    stale += 1
            self.set("approved", True)
        return stale

    def translation_memory(self, chunk: dict, previous: str, count, limit: int) -> tuple[dict, list[str]]:
        source = "\n".join(b["text"] for b in chunk["blocks"]) + "\n" + previous
        selected = [t for t in self.terms() if any(occurs(source, n) for n in [t["source"]] + t["aliases"])]
        if any(not t["approved"] for t in selected):
            raise PipelineError("Unapproved terminology found. Finish human review first.")
        end_order = max(b["order"] for b in chunk["blocks"])
        lexicon = []
        for term in selected:
            evidence_order = {e["block_id"]: e["order"] for e in term["evidence"]}
            available_notes = [m for m in term["meanings"]
                               if m.get("series_inherited") is True
                               or (m.get("evidence") and all(evidence_order.get(b, float("inf")) <= end_order
                                                        for b in m["evidence"]))]
            lexicon.append({"id": term["id"], "source": term["source"], "aliases": term["aliases"],
                            "polish": term["choice"], "category": term["category"],
                            "meaning_notes": available_notes[-2:]})
        # No narrative/factual conclusions from later source positions are injected.
        observations = [f for f in self.facts()
                        if any(occurs(source, n) for n in f["about"])
                        and (f.get("series_inherited") is True
                             or (f["available_from_order"] <= end_order
                                 and f["chapter_id"] == chunk["chapter_id"]))]
        result = {"APPROVED_LEXICON": lexicon, "OBSERVATIONS": observations}
        if count(dumps(result)) > limit:
            raise PipelineError("Relevant translation memory is too large. Increase memory_tokens; nothing was silently removed.")
        return result, [t["id"] for t in selected]
