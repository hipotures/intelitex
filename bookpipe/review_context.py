"""Read-only, block-ID-aligned source/P5 evidence for terminology review.

No text matching on a Polish lemma, LLM call, database migration or artifact write
is performed. Only final paths registered in the checkpoint database are read.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .util import PipelineError, digest, inside, read_json


class EvidenceReader:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._book_stamp = None
        self._sources: dict[str, dict] = {}
        self._units: dict[str, list[tuple[str, str]]] = {}

    def _index(self) -> None:
        path = self.root / "book.json"
        if not path.is_file():
            return
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._book_stamp:
            return
        book = read_json(path)
        sources, units = {}, {}
        for chapter in book.get("chapters", []):
            for block in chapter.get("blocks", []):
                sources[block["id"]] = {**block, "chapter_id": chapter["id"]}
        for chunk in book.get("chunks", []):
            for block in chunk.get("blocks", []):
                entry = (chunk["id"], block["id"])
                for bid in {block["id"], block.get("parent_id", block["id"])}:
                    units.setdefault(bid, []).append(entry)
                sources.setdefault(block["id"], {**block, "chapter_id": chunk["chapter_id"]})
        self._sources, self._units, self._book_stamp = sources, units, stamp

    @staticmethod
    def _choice(term: dict) -> str:
        custom = str(term.get("custom", "")).strip()
        if custom:
            return custom
        return next((c.get("text", "") for c in term.get("candidates", [])
                     if c.get("number") == term.get("select")), "")

    def for_term(self, term: dict) -> dict:
        self._index()
        database = self.root / "state.sqlite3"
        connection = None
        warnings: list[str] = []
        choice_pending = False
        finals: dict[str, dict] = {}
        try:
            if database.is_file():
                # Each request owns a read-only connection; never use a Store
                # connection created in another HTTP thread.
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                connection.row_factory = sqlite3.Row
                tid = term.get("id", "")
                if tid.startswith("T") and tid[1:].isdigit():
                    row = connection.execute("SELECT choice FROM terms WHERE id=?", (int(tid[1:]),)).fetchone()
                    if row and row["choice"] is not None:
                        choice_pending = row["choice"] != self._choice(term)

            def final_for(cid: str) -> dict:
                if cid in finals:
                    return finals[cid]
                final = {"status": "not_translated", "translations": {}}
                finals[cid] = final
                if connection is None:
                    return final
                row = connection.execute("SELECT status,final_path FROM chunks WHERE id=?", (cid,)).fetchone()
                if not row or not row["final_path"]:
                    return final
                try:
                    path = inside(self.root, self.root / row["final_path"])
                    job = connection.execute("SELECT result_hash FROM jobs WHERE result_path=?", (row["final_path"],)).fetchone()
                    raw = path.read_bytes()
                    if not job or digest(raw) != job["result_hash"]:
                        raise PipelineError("Final artifact checksum does not match its checkpoint.")
                    parsed = json.loads(raw)
                    translations = parsed.get("translations")
                    if not isinstance(translations, list):
                        raise PipelineError("Final artifact has no translations array.")
                    mapped = {}
                    for item in translations:
                        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or item.get("id") in mapped:
                            raise PipelineError("Invalid or duplicate translation block.")
                        mapped[item["id"]] = item["text"]
                    final.update(status="stale" if row["status"] != "done" else "available", translations=mapped)
                except (PipelineError, OSError, ValueError, KeyError, TypeError) as exc:
                    final.update(status="error", message=str(exc))
                return final

            entries = []
            for evidence in term.get("evidence", []):
                bid = evidence.get("block_id", "")
                parent = bid.rsplit(".a", 1)[0] if ".a" in bid else bid
                key = bid if bid in self._sources else parent
                source = self._sources.get(key)
                item = {
                    "block_id": bid, "chapter_id": evidence.get("chapter_id", ""),
                    "source_text": source["text"] if source else evidence.get("excerpt", ""),
                    "source_kind": "full_block" if source else "stored_excerpt",
                    "polish_text": None, "stage": None, "status": "not_translated", "unit_ids": [],
                }
                pieces = self._units.get(key, [])
                translated, statuses = [], []
                for cid, piece_id in pieces:
                    final = final_for(cid)
                    item["unit_ids"].append(cid)
                    statuses.append(final["status"])
                    if piece_id in final["translations"]:
                        translated.append(final["translations"][piece_id])
                    elif final["status"] == "available":
                        statuses[-1] = "error"
                    if final.get("message"):
                        item["message"] = final["message"]
                if pieces and len(translated) == len(pieces):
                    item.update(polish_text=" ".join(translated), stage="P5",
                                status="stale" if "stale" in statuses else "available")
                elif "error" in statuses:
                    item["status"] = "error"
                elif translated:
                    item["status"] = "partial"
                entries.append(item)
            if choice_pending:
                warnings.append("The current review choice differs from the approved lexicon. These translations still use an earlier decision.")
            return {"term_id": term["id"], "entries": entries, "warnings": warnings,
                    "choice_pending_approval": choice_pending}
        except sqlite3.Error as exc:
            raise PipelineError(f"Cannot read translation checkpoints: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()
