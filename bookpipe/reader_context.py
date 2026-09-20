"""Checkpoint-verified, block-ID-preserving read model for the local Reader."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .util import PipelineError, digest, inside, normalized, read_json


_INLINE_FORMATTING = re.compile(
    r"(?<!\*)\*\*([^\s*](?:[^*\n]*[^\s*])?)\*\*(?!\*)"
    r"|(?<!\*)\*([^\s*](?:[^*\n]*[^\s*])?)\*(?!\*)"
)
_READER_WORDS = re.compile(r"\w+(?:[’'-]\w+)*", re.UNICODE)


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


def reader_word_count(text: str) -> int:
    """Count the word units used by the Reader progress model."""
    return sum(1 for _ in _READER_WORDS.finditer(text))


class ReaderContext:
    """Assemble canonical Polish blocks only from registered P5 artifacts."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._book_data: dict | None = None
        self._canonical_blocks: set[tuple[str, str]] = set()
        self._block_orders: dict[str, int] = {}
        self._block_chapters: dict[str, str] = {}
        self._structural_blocks: set[str] = set()
        self._chapter_titles: dict[str, str] = {}
        self._verified_blocks: dict[str, dict[str, str]] = {}
        self._lexicon_stamp: tuple[int, int] | None = None
        self._lexicon_forms_by_token: dict[str, list[dict]] = {}
        self._memory_stamp: tuple[int, int] | None = None
        self._memory_data: dict = {"terms": [], "observations": []}
        self._terms_by_id: dict[str, dict] = {}
        self._observations_by_about: dict[str, list[dict]] = {}

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
        metadata = book.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        toc = metadata.get("toc", [])
        if not isinstance(toc, list):
            toc = []
        navigation_files = {
            item.get("file")
            for item in toc
            if isinstance(item, dict)
            and normalized(item.get("label", "")) in {"contents", "table of contents", "navigation"}
            and isinstance(item.get("file"), str)
        }
        navigation_files.update(
            block.get("file")
            for chapter in book["chapters"] if isinstance(chapter, dict)
            for block in chapter.get("blocks", []) if isinstance(block, dict)
            if isinstance(block.get("file"), str)
            and any(isinstance(value, str) and value.casefold().startswith("toc_")
                    for value in (block.get("classes") if isinstance(block.get("classes"), list) else []))
        )
        serial = 0
        for chapter in book["chapters"]:
            chapter_id = chapter.get("id")
            if not isinstance(chapter_id, str):
                continue
            self._chapter_titles[chapter_id] = chapter.get("title") or chapter_id
            chapter_structural = (
                normalized(chapter.get("title", "")) in {"contents", "table of contents", "navigation"}
                or chapter.get("source_file") in navigation_files
                or normalized(chapter.get("role", "")) in {"contents", "toc", "navigation"}
            )
            for block in chapter.get("blocks", []):
                if not isinstance(block, dict) or not isinstance(block.get("id"), str):
                    continue
                serial += 1
                block_id = block["id"]
                order = block.get("order")
                self._block_orders[block_id] = order if type(order) is int else serial
                self._block_chapters[block_id] = chapter_id
                classes = block.get("classes", [])
                if not isinstance(classes, list):
                    classes = []
                if (chapter_structural or block.get("file") in navigation_files
                        or any(isinstance(value, str) and value.casefold().startswith("toc_")
                               for value in classes)):
                    self._structural_blocks.add(block_id)
        for chunk in book["chunks"]:
            chapter_id = chunk.get("chapter_id")
            for piece in chunk.get("blocks", []):
                if not isinstance(piece, dict) or not isinstance(piece.get("id"), str):
                    continue
                parent = piece.get("parent_id", piece["id"])
                if parent in self._block_orders:
                    self._block_orders[piece["id"]] = self._block_orders[parent]
        return book

    def _lexicon(self) -> dict[str, list[dict]]:
        """Cache approved recognition forms without inspecting translated prose."""
        path = self.root / "lexicon.approved.json"
        if not path.is_file():
            self._lexicon_stamp = None
            self._lexicon_forms_by_token = {}
            return self._lexicon_forms_by_token
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._lexicon_stamp:
            return self._lexicon_forms_by_token
        data = read_json(path)
        terms = data.get("terms") if isinstance(data, dict) else None
        if not isinstance(terms, list):
            raise PipelineError("lexicon.approved.json has no valid terms array.")
        by_token: dict[str, list[dict]] = {}
        seen: set[tuple[str, str]] = set()
        for term in terms:
            if not isinstance(term, dict) or not isinstance(term.get("id"), str):
                continue
            term_id = term["id"]
            aliases = term.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            for form in [term.get("source"), term.get("polish"), *aliases]:
                if not isinstance(form, str):
                    continue
                form_key = normalized(form)
                form_words = [normalized(match.group()) for match in _READER_WORDS.finditer(form)]
                if not form_key or not form_words or (term_id, form_key) in seen:
                    continue
                seen.add((term_id, form_key))
                candidate = {"term_id": term_id, "form": form_key, "words": form_words}
                for token_index, token in enumerate(form_words):
                    by_token.setdefault(token, []).append({**candidate, "token_index": token_index})
        self._lexicon_forms_by_token = by_token
        self._lexicon_stamp = stamp
        return by_token

    def _memory(self) -> dict:
        """Cache the local P1 memory, refreshing only when its file changes."""
        path = self.root / "book_memory.json"
        if not path.is_file():
            self._memory_stamp = None
            self._memory_data = {"terms": [], "observations": []}
            self._terms_by_id = {}
            self._observations_by_about = {}
            return {"terms": [], "observations": []}
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._memory_stamp:
            return self._memory_data
        memory = read_json(path)
        if not isinstance(memory, dict):
            raise PipelineError("book_memory.json must contain a JSON object.")
        terms, observations = memory.get("terms", []), memory.get("observations", [])
        if not isinstance(terms, list) or not isinstance(observations, list):
            raise PipelineError("book_memory.json has invalid terms or observations.")
        self._memory_data = {"terms": terms, "observations": observations}
        self._terms_by_id = {
            term["id"]: term for term in terms
            if isinstance(term, dict) and isinstance(term.get("id"), str)
        }
        self._observations_by_about = {}
        for observation in observations:
            if not isinstance(observation, dict) or not isinstance(observation.get("about"), list):
                continue
            about_keys = {normalized(value) for value in observation["about"]
                          if isinstance(value, str) and normalized(value)}
            for about_key in about_keys:
                self._observations_by_about.setdefault(about_key, []).append(observation)
        self._memory_stamp = stamp
        return self._memory_data

    def _resolve_entity(self, text: str, position: int) -> tuple[dict, str] | None:
        """Resolve the longest approved form covering one position in one P5 block."""
        words = list(_READER_WORDS.finditer(text))
        touched_index = next((index for index, word in enumerate(words)
                              if word.start() <= position < word.end()), None)
        if touched_index is None:
            return None
        touched = normalized(words[touched_index].group())
        spans: list[dict] = []
        seen: set[tuple[int, int, str]] = set()
        for candidate in self._lexicon().get(touched, []):
            start_index = touched_index - candidate["token_index"]
            end_index = start_index + len(candidate["words"])
            if start_index < 0 or end_index > len(words):
                continue
            start, end = words[start_index].start(), words[end_index - 1].end()
            key = (start, end, candidate["term_id"])
            if key in seen or normalized(text[start:end]) != candidate["form"]:
                continue
            seen.add(key)
            spans.append({"start": start, "end": end, "term_id": candidate["term_id"]})
        if not spans:
            return None
        longest = max(span["end"] - span["start"] for span in spans)
        candidates = [span for span in spans if span["end"] - span["start"] == longest]
        coordinates = {(span["start"], span["end"]) for span in candidates}
        if len(coordinates) != 1:
            return None
        identities = {span["term_id"] for span in candidates}
        if len(identities) != 1:
            return None
        return candidates[0], next(iter(identities))

    def _evidence_before(self, evidence: object, cutoff: int) -> bool:
        return (
            isinstance(evidence, list)
            and bool(evidence)
            and all(isinstance(block_id, str)
                    and block_id not in self._structural_blocks
                    and self._block_orders.get(block_id, cutoff) < cutoff
                    for block_id in evidence)
        )

    @staticmethod
    def _short_excerpt(text: str, phrase: str = "", limit: int = 180) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        match = (re.search(r"(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)", compact, re.IGNORECASE)
                 if phrase.strip() else None)
        if not match or len(compact) <= limit:
            return compact[:limit]
        half = max(24, (limit - len(match.group(0))) // 2)
        start, end = max(0, match.start() - half), min(len(compact), match.end() + half)
        if start:
            boundary = compact.find(" ", start)
            start = boundary + 1 if boundary >= 0 and boundary < match.start() else start
        if end < len(compact):
            boundary = compact.rfind(" ", match.end(), end)
            end = boundary if boundary > match.end() else end
        return ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")

    def context(self, chapter_id: str, block_id: str, position: int) -> dict:
        """Resolve a known term at a P5 position and return only prior knowledge."""
        self._book()
        if not isinstance(chapter_id, str) or not isinstance(block_id, str):
            raise PipelineError("chapter_id and block_id must be strings.")
        if type(position) is not int:
            raise PipelineError("Context position must be an integer Unicode code-point offset.")
        cutoff = self._block_orders.get(block_id)
        if cutoff is None or (chapter_id, block_id) not in self._canonical_blocks:
            raise PipelineError(f"Unknown canonical block {block_id} in chapter {chapter_id}.")
        text = self.block_text(chapter_id, block_id)
        if not 0 <= position < len(text):
            raise PipelineError("Context position is outside the translated block.")

        resolved = self._resolve_entity(text, position)
        if resolved is None:
            return {"recognized": False}
        span, identity = resolved
        selected = text[span["start"]:span["end"]]
        self._memory()
        term = self._terms_by_id.get(identity, {})

        statements: list[str] = []
        for note in term.get("meanings", []):
            if (isinstance(note, dict) and isinstance(note.get("text"), str)
                    and note["text"].strip() and self._evidence_before(note.get("evidence"), cutoff)):
                statements.append(note["text"].strip())
        source_key = normalized(term.get("source", ""))
        for observation in self._observations_by_about.get(source_key, []):
            available = observation.get("available_from_order")
            about = observation.get("about")
            if (type(available) is not int or available >= cutoff
                    or not self._evidence_before(observation.get("evidence"), cutoff)
                    or not isinstance(about, list)
                    or source_key not in {normalized(value) for value in about if isinstance(value, str)}):
                continue
            statement = observation.get("statement")
            if isinstance(statement, str) and statement.strip():
                statements.append(statement.strip())

        statements = list(dict.fromkeys(statements))[:4]
        earlier_mentions = []
        evidence_ids = []
        for item in term.get("evidence", []):
            block = item.get("block_id") if isinstance(item, dict) else item
            if isinstance(block, str):
                evidence_ids.append(block)
        evidence_ids.sort(key=lambda item: self._block_orders.get(item, cutoff))
        seen_blocks: set[str] = set()
        for previous_id in evidence_ids:
            order = self._block_orders.get(previous_id)
            if (order is None or order >= cutoff or previous_id in seen_blocks
                    or previous_id in self._structural_blocks):
                continue
            previous_chapter = self._block_chapters.get(previous_id)
            if previous_chapter is None:
                continue
            previous_text = self.block_text(previous_chapter, previous_id)
            earlier_mentions.append({
                "chapter_id": previous_chapter,
                "chapter_title": self._chapter_titles.get(previous_chapter, previous_chapter),
                "block_id": previous_id,
                "text": self._short_excerpt(previous_text),
            })
            seen_blocks.add(previous_id)
            if len(earlier_mentions) == 3:
                break

        return {
            "recognized": True,
            "title": selected,
            "statements": statements,
            "earlier_mentions": earlier_mentions,
            "range": {"start": span["start"], "end": span["end"]},
        }

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

    def progress(self) -> dict:
        """Describe the word offsets in the currently available translated prefix."""
        book = self._book()
        chapters = []
        total_words = 0
        last_chapter = None
        for source_chapter in book["chapters"]:
            chapter = self.chapter(source_chapter["id"])
            chapter_start = total_words
            blocks = []
            for block in chapter["blocks"]:
                words = reader_word_count(block["text"])
                blocks.append({"id": block["id"], "start": total_words, "words": words})
                total_words += words
            if blocks:
                chapters.append({
                    "id": chapter["id"],
                    "start": chapter_start,
                    "words": total_words - chapter_start,
                    "blocks": blocks,
                })
                last_chapter = {"id": chapter["id"], "title": chapter["title"]}
            if "unavailable" in chapter:
                break
        return {"total_words": total_words, "last_chapter": last_chapter, "chapters": chapters}

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
        for block in blocks:
            self._verified_blocks[block["id"]] = {"chapter_id": chapter_id, "text": block["text"]}
        return result

    def block_text(self, chapter_id: str, block_id: str) -> str:
        cached = self._verified_blocks.get(block_id)
        if cached is not None and cached["chapter_id"] == chapter_id:
            return cached["text"]
        chapter = self.chapter(chapter_id)
        block = next((item for item in chapter["blocks"] if item["id"] == block_id), None)
        if block is None:
            raise PipelineError(f"Unknown or unavailable translated block {block_id} in chapter {chapter_id}.")
        return block["text"]
