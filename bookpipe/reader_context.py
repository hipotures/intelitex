"""Checkpoint-verified, block-ID-preserving read model for the local Reader."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .util import PipelineError, digest, inside, read_json


_INLINE_FORMATTING = re.compile(
    r"(?<!\*)\*\*([^\s*](?:[^*\n]*[^\s*])?)\*\*(?!\*)"
    r"|(?<!\*)\*([^\s*](?:[^*\n]*[^\s*])?)\*(?!\*)"
)


def parse_inline_formatting(text: str) -> tuple[str, list[dict[str, int | str]]]:
    """Remove importer emphasis delimiters and describe their visible-text ranges."""
    chunks: list[str] = []
    formatting: list[dict[str, int | str]] = []
    source_offset = 0
    visible_offset = 0
    for match in _INLINE_FORMATTING.finditer(text):
        prefix = text[source_offset:match.start()]
        chunks.append(prefix)
        visible_offset += len(prefix)
        content = match.group(1) if match.group(1) is not None else match.group(2)
        style = "strong" if match.group(1) is not None else "em"
        chunks.append(content)
        formatting.append({"start": visible_offset, "end": visible_offset + len(content), "style": style})
        visible_offset += len(content)
        source_offset = match.end()
    chunks.append(text[source_offset:])
    return "".join(chunks), formatting


class ReaderContext:
    """Assemble canonical Polish blocks only from registered P5 artifacts."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._book_data: dict | None = None
        self._canonical_blocks: set[tuple[str, str]] = set()

    def _book(self) -> dict:
        if self._book_data is not None:
            return self._book_data
        path = self.root / "book.json"
        if not path.is_file():
            raise PipelineError("Reader project has no book.json.")
        book = read_json(path)
        if not isinstance(book.get("chapters"), list) or not isinstance(book.get("chunks"), list):
            raise PipelineError("book.json has no valid chapter/chunk manifest.")
        if not isinstance(book.get("source_fingerprint"), str) or not book["source_fingerprint"]:
            raise PipelineError("book.json has no source fingerprint.")
        # The CLI project lock makes book.json immutable for the Reader lifetime.
        # Reuse the parsed manifest across metadata and chapter requests.
        self._book_data = book
        self._canonical_blocks = {
            (chapter.get("id"), block.get("id"))
            for chapter in book["chapters"] if isinstance(chapter, dict)
            for block in chapter.get("blocks", []) if isinstance(block, dict)
            if isinstance(chapter.get("id"), str) and isinstance(block.get("id"), str)
        }
        return book

    def has_canonical_block(self, chapter_id: str, block_id: str) -> bool:
        self._book()
        return (chapter_id, block_id) in self._canonical_blocks

    def metadata(self) -> dict:
        book = self._book()
        title = book.get("metadata", {}).get("title")
        return {
            "book_fingerprint": book["source_fingerprint"],
            "title": title if isinstance(title, str) and title.strip() else "Translated book",
            "chapters": [
                {"id": chapter["id"], "title": chapter.get("title") or f"Chapter {index}"}
                for index, chapter in enumerate(book["chapters"], 1)
            ],
        }

    @staticmethod
    def _translation_map(raw: bytes, expected_ids: list[str]) -> dict[str, str]:
        try:
            parsed = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Final P5 artifact is not valid UTF-8 JSON: {exc}") from exc
        translations = parsed.get("translations") if isinstance(parsed, dict) else None
        if not isinstance(translations, list):
            raise PipelineError("Final P5 artifact has no translations array.")
        mapped: dict[str, str] = {}
        for item in translations:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not isinstance(item.get("text"), str) or item["id"] in mapped):
                raise PipelineError("Final P5 artifact contains an invalid or duplicate translation block.")
            mapped[item["id"]] = item["text"]
        if set(mapped) != set(expected_ids):
            raise PipelineError("Final P5 artifact does not exactly cover its registered translation unit.")
        return mapped

    def chapter(self, chapter_id: str) -> dict:
        book = self._book()
        chapter = next((item for item in book["chapters"] if item.get("id") == chapter_id), None)
        if chapter is None:
            raise PipelineError(f"Unknown chapter ID: {chapter_id}.")
        canonical = chapter.get("blocks")
        if not isinstance(canonical, list):
            raise PipelineError(f"Chapter {chapter_id} has no valid canonical blocks.")
        canonical_by_id = {block.get("id"): block for block in canonical if isinstance(block, dict)}
        if len(canonical_by_id) != len(canonical) or not all(isinstance(key, str) for key in canonical_by_id):
            raise PipelineError(f"Chapter {chapter_id} has invalid or duplicate canonical block IDs.")

        chunks = [chunk for chunk in book["chunks"] if chunk.get("chapter_id") == chapter_id]
        manifest_ids = chapter.get("chunk_ids")
        if isinstance(manifest_ids, list):
            by_id = {chunk.get("id"): chunk for chunk in chunks}
            if set(by_id) != set(manifest_ids):
                raise PipelineError(f"Chapter {chapter_id} chunk manifest is inconsistent.")
            chunks = [by_id[cid] for cid in manifest_ids]
        else:
            chunks.sort(key=lambda item: item.get("number", 0))

        expected_by_parent: dict[str, list[str]] = {bid: [] for bid in canonical_by_id}
        for chunk in chunks:
            for piece in chunk.get("blocks", []):
                if not isinstance(piece, dict) or not isinstance(piece.get("id"), str):
                    raise PipelineError(f"Translation unit {chunk.get('id')} has an invalid block entry.")
                parent_id = piece.get("parent_id", piece["id"])
                if parent_id not in canonical_by_id:
                    raise PipelineError(f"Translation piece {piece['id']} has unknown parent block {parent_id}.")
                expected_by_parent[parent_id].append(piece["id"])

        database = self.root / "state.sqlite3"
        if not database.is_file():
            raise PipelineError("Reader project has no state.sqlite3 checkpoint database.")
        translated: dict[str, list[tuple[str, str]]] = {bid: [] for bid in canonical_by_id}
        unavailable_at: str | None = None
        unavailable_reason = "Translation is not available beyond this point."
        stale_units: list[str] = []
        connection = None
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            for chunk in chunks:
                cid = chunk.get("id")
                row = connection.execute(
                    "SELECT status,final_path FROM chunks WHERE id=?", (cid,)
                ).fetchone()
                if not row or not row["final_path"]:
                    if unavailable_at is None:
                        unavailable_at = cid
                    continue
                try:
                    path = inside(self.root, self.root / row["final_path"])
                    job = connection.execute(
                        "SELECT result_hash FROM jobs WHERE result_path=?", (row["final_path"],)
                    ).fetchone()
                    raw = path.read_bytes()
                except OSError as exc:
                    raise PipelineError(f"Final P5 artifact for {cid} is missing: {exc}") from exc
                if not job or digest(raw) != job["result_hash"]:
                    raise PipelineError(f"Final P5 artifact checksum mismatch for {cid}.")
                expected = [piece["id"] for piece in chunk.get("blocks", [])]
                mapped = self._translation_map(raw, expected)
                if row["status"] != "done":
                    stale_units.append(cid)
                if unavailable_at is not None:
                    # Preserve a readable prefix when an earlier unit is unavailable.
                    continue
                for piece in chunk.get("blocks", []):
                    parent_id = piece.get("parent_id", piece["id"])
                    translated[parent_id].append((piece["id"], mapped[piece["id"]].strip()))
        except sqlite3.Error as exc:
            raise PipelineError(f"Cannot read translation checkpoints: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

        blocks = []
        for block in canonical:
            bid = block["id"]
            pieces = translated[bid]
            expected = expected_by_parent[bid]
            if not expected or [piece_id for piece_id, _ in pieces] != expected:
                if unavailable_at is None:
                    unavailable_at = bid
                    unavailable_reason = "A canonical block has no complete checkpoint-verified P5 translation."
                break
            text, formatting = parse_inline_formatting(" ".join(text for _, text in pieces))
            rendered_block: dict[str, object] = {
                "id": bid,
                "kind": block.get("kind", "paragraph"),
                "text": text,
            }
            if formatting:
                rendered_block["formatting"] = formatting
            blocks.append(rendered_block)

        complete = unavailable_at is None and not stale_units
        result = {
            "id": chapter["id"],
            "title": chapter.get("title") or chapter["id"],
            "blocks": blocks,
            "complete": complete,
            "stale": bool(stale_units),
        }
        if unavailable_at is not None:
            result["unavailable"] = {"after_blocks": len(blocks), "reason": unavailable_reason}
        if stale_units:
            result["warning"] = "This chapter includes checkpoint-verified P5 text from stale translation units."
        return result

    def block_text(self, chapter_id: str, block_id: str) -> str:
        chapter = self.chapter(chapter_id)
        block = next((item for item in chapter["blocks"] if item["id"] == block_id), None)
        if block is None:
            raise PipelineError(f"Unknown or unavailable translated block {block_id} in chapter {chapter_id}.")
        return block["text"]
