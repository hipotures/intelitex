"""Checkpoint-verified, block-ID-preserving read model for the local Reader."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from .util import PipelineError, digest, inside, normalized, read_json


_INLINE_FORMATTING = re.compile(
    r"(?<!\*)\*\*([^\s*](?:[^*\n]*[^\s*])?)\*\*(?!\*)"
    r"|(?<!\*)\*([^\s*](?:[^*\n]*[^\s*])?)\*(?!\*)"
)
_READER_WORDS = re.compile(r"\w+(?:[’'-]\w+)*", re.UNICODE)
_POLISH_ENDINGS = (
    "owego", "owej", "owych", "owymi", "owie", "ami", "ach", "emu", "cie",
    "ego", "ymi", "ych", "ej", "ow", "om", "em", "ym", "ie", "ze",
    "a", "e", "i", "y", "o", "u",
)
_HONORIFICS = {
    "mr", "mrs", "ms", "miss", "dr", "doctor", "director", "agent", "saint",
    "pan", "pani", "doktor", "dyrektor", "dyrektorka", "agentka", "swiety", "swieta",
}


def _fold_token(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", normalized(value))
                   if not unicodedata.combining(character))


def _inflection_roots(value: str) -> set[str]:
    """Return only mechanically conservative Polish case/number roots."""
    folded = _fold_token(value)
    roots = {folded}
    if folded.isalpha():
        for ending in _POLISH_ENDINGS:
            if folded.endswith(ending) and len(folded) - len(ending) >= 4:
                roots.add(folded[:-len(ending)])
    return roots


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
        self._lexicon_forms_by_root: dict[str, list[dict]] = {}
        self._lexicon_forms_by_id: dict[str, list[dict]] = {}
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
        from .processing import effective_book
        book = effective_book(book, self.root, include_dormant=True)
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
            self._lexicon_forms_by_root = {}
            self._lexicon_forms_by_id = {}
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
        by_root: dict[str, list[dict]] = {}
        by_id: dict[str, list[dict]] = {}
        seen: set[tuple[str, str]] = set()
        for term in terms:
            if not isinstance(term, dict) or not isinstance(term.get("id"), str):
                continue
            term_id = term["id"]
            aliases = term.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            base_forms = [value for value in (term.get("source"), term.get("polish"))
                          if isinstance(value, str)]
            base_sequences = [tuple(_fold_token(match.group()) for match in _READER_WORDS.finditer(value))
                              for value in base_forms]
            invariant_tokens = set.intersection(*(set(words) for words in base_sequences)) if base_sequences else set()
            entries = [(term.get("source"), "source"), (term.get("polish"), "polish")]
            entries.extend((alias, "alias") for alias in aliases)
            for form, kind in entries:
                if not isinstance(form, str):
                    continue
                form_key = normalized(form)
                word_matches = list(_READER_WORDS.finditer(form))
                form_words = [normalized(match.group()) for match in word_matches]
                if not form_key or not form_words or (term_id, form_key) in seen:
                    continue
                seen.add((term_id, form_key))
                folded_words = tuple(_fold_token(word) for word in form_words)
                transparent = kind != "alias" or any(
                    (len(base) <= len(folded_words)
                     and any(folded_words[index:index + len(base)] == base
                             for index in range(len(folded_words) - len(base) + 1)))
                    or (len(folded_words) <= len(base)
                        and any(base[index:index + len(folded_words)] == folded_words
                                for index in range(len(base) - len(folded_words) + 1)))
                    for base in base_sequences if base
                )
                candidate = {
                    "term_id": term_id, "form": form_key, "text": form.strip(), "kind": kind,
                    "transparent": transparent, "words": form_words,
                    "folded_words": folded_words,
                    "roots": [frozenset(_inflection_roots(word)) for word in form_words],
                    "invariant": [folded in invariant_tokens for folded in folded_words],
                }
                by_id.setdefault(term_id, []).append(candidate)
                for token_index, token in enumerate(form_words):
                    reference = {"form": candidate, "token_index": token_index}
                    by_token.setdefault(token, []).append(reference)
                    for root in candidate["roots"][token_index]:
                        by_root.setdefault(root, []).append(reference)
        self._lexicon_forms_by_token = by_token
        self._lexicon_forms_by_root = by_root
        self._lexicon_forms_by_id = by_id
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

    def _term_evidence_ids(self, term: dict) -> list[str]:
        result = []
        for item in term.get("evidence", []):
            block_id = item.get("block_id") if isinstance(item, dict) else item
            if isinstance(block_id, str):
                result.append(block_id)
        return result

    def _term_supported_at(self, term: dict, block_id: str, cutoff: int) -> bool:
        return any(evidence == block_id or (
            evidence not in self._structural_blocks
            and self._block_orders.get(evidence, cutoff) < cutoff
        ) for evidence in self._term_evidence_ids(term))

    def _alias_established_before(self, form: dict, term: dict, cutoff: int) -> bool:
        """Require explicit pre-cutoff evidence before joining an unrelated alias."""
        if form["kind"] != "alias" or form["transparent"]:
            return True
        wanted = form["form"]
        for candidate in term.get("candidates", []):
            if (isinstance(candidate, dict) and normalized(candidate.get("text", "")) == wanted
                    and self._evidence_before(candidate.get("evidence"), cutoff)):
                return True
        for item in term.get("evidence", []):
            if not isinstance(item, dict) or not isinstance(item.get("block_id"), str):
                continue
            evidence_id = item["block_id"]
            if (evidence_id in self._structural_blocks
                    or self._block_orders.get(evidence_id, cutoff) >= cutoff):
                continue
            excerpt = item.get("excerpt")
            if isinstance(excerpt, str) and re.search(
                    r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", normalized(excerpt)):
                return True
        return False

    @staticmethod
    def _match_form_at(words: list[re.Match], start: int, form: dict) -> tuple[int, ...] | None:
        if start < 0 or start + len(form["words"]) > len(words):
            return None
        qualities = []
        for index, expected in enumerate(form["words"]):
            visible = normalized(words[start + index].group())
            if visible == expected:
                qualities.append(0)
            elif _fold_token(visible) == form["folded_words"][index]:
                qualities.append(1)
            elif (not form["invariant"][index]
                  and _inflection_roots(visible) & form["roots"][index]):
                qualities.append(2)
            else:
                return None
        changed = sum(quality > 1 for quality in qualities)
        if changed and (len(qualities) == 1 or changed > max(1, len(qualities) // 2)):
            return None
        if changed and len(qualities) > 1 and not any(quality <= 1 for quality in qualities):
            return None
        return tuple(qualities)

    def _form_allowed(self, form: dict, term: dict, cutoff: int) -> bool:
        return form["kind"] != "alias" or form["transparent"] or self._alias_established_before(form, term, cutoff)

    def _candidate_span(self, words: list[re.Match], start: int, form: dict,
                        block_id: str, cutoff: int) -> dict | None:
        qualities = self._match_form_at(words, start, form)
        if qualities is None:
            return None
        term = self._terms_by_id.get(form["term_id"], {})
        if not self._form_allowed(form, term, cutoff):
            return None
        if any(quality > 0 for quality in qualities) and not self._term_supported_at(term, block_id, cutoff):
            return None
        end_index = start + len(form["words"]) - 1
        return {
            "start": words[start].start(), "end": words[end_index].end(),
            "term_id": form["term_id"], "form": form, "quality": qualities,
        }

    def _resolve_entity(self, text: str, position: int, block_id: str, cutoff: int) -> dict | None:
        """Resolve one local exact/inflected approved form without scanning other prose."""
        words = list(_READER_WORDS.finditer(text))
        touched_index = next((index for index, word in enumerate(words)
                              if word.start() <= position < word.end()), None)
        if touched_index is None:
            return None
        self._lexicon()
        touched = normalized(words[touched_index].group())
        references = list(self._lexicon_forms_by_token.get(touched, []))
        for root in _inflection_roots(touched):
            references.extend(self._lexicon_forms_by_root.get(root, []))
        spans = []
        seen: set[tuple[int, int, str, str]] = set()
        for reference in references:
            form = reference["form"]
            start_index = touched_index - reference["token_index"]
            span = self._candidate_span(words, start_index, form, block_id, cutoff)
            if span is None or not span["start"] <= position < span["end"]:
                continue
            key = (span["start"], span["end"], span["term_id"], form["form"])
            if key not in seen:
                seen.add(key)
                spans.append(span)
        if not spans:
            return None
        longest_words = max(len(span["form"]["words"]) for span in spans)
        spans = [span for span in spans if len(span["form"]["words"]) == longest_words]
        longest = max(span["end"] - span["start"] for span in spans)
        spans = [span for span in spans if span["end"] - span["start"] == longest]
        best_quality = min((sum(span["quality"]), max(span["quality"])) for span in spans)
        candidates = [span for span in spans
                      if (sum(span["quality"]), max(span["quality"])) == best_quality]
        coordinates = {(span["start"], span["end"]) for span in candidates}
        identities = {span["term_id"] for span in candidates}
        if len(coordinates) != 1 or len(identities) != 1:
            return None
        return candidates[0]

    def _term_occurrences(self, text: str, term_id: str, block_id: str, cutoff: int) -> list[dict]:
        """Find only one resolved term's forms in one explicitly requested/evidence block."""
        self._lexicon()
        words = list(_READER_WORDS.finditer(text))
        occurrences = []
        seen: set[tuple[int, int, str]] = set()
        for start in range(len(words)):
            for form in self._lexicon_forms_by_id.get(term_id, []):
                span = self._candidate_span(words, start, form, block_id, cutoff)
                if span is None:
                    continue
                key = (span["start"], span["end"], form["form"])
                if key not in seen:
                    seen.add(key)
                    occurrences.append(span)
        return sorted(occurrences, key=lambda item: (item["start"], -(item["end"] - item["start"])))

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

    @staticmethod
    def _sentence_spans(text: str) -> list[tuple[int, int]]:
        spans = []
        start = 0
        for match in re.finditer(r"[.!?…]+[\"'”’»)]*(?=\s|$)", text):
            end = match.end()
            if text[start:end].strip():
                spans.append((start, end))
            whitespace = re.match(r"\s+", text[end:])
            start = end + (whitespace.end() if whitespace else 0)
        return spans

    def _same_block_context(self, text: str, position: int, span: dict,
                            block_id: str, cutoff: int) -> list[dict]:
        prefix = text[:position]
        previous = [item for item in self._term_occurrences(
            prefix, span["term_id"], block_id, cutoff
        ) if item["end"] <= span["start"]]
        if not previous:
            return []
        occurrence = max(previous, key=lambda item: item["end"])
        sentences = self._sentence_spans(prefix)
        containing = next((index for index, (start, end) in enumerate(sentences)
                           if start <= occurrence["start"] < end), None)
        if containing is None:
            return []
        indexes = [index for index in (containing - 1, containing, containing + 1)
                   if 0 <= index < len(sentences)]
        excerpt_start, excerpt_end = sentences[indexes[0]][0], sentences[indexes[-1]][1]
        while excerpt_end - excerpt_start > 520 and len(indexes) > 1:
            if indexes[0] < containing:
                indexes.pop(0)
            else:
                indexes.pop()
            excerpt_start, excerpt_end = sentences[indexes[0]][0], sentences[indexes[-1]][1]
        excerpt = re.sub(r"\s+", " ", prefix[excerpt_start:excerpt_end]).strip()
        return [{"block_id": block_id, "text": excerpt}] if excerpt else []

    @staticmethod
    def _display_candidate(text: str, span: dict) -> str:
        visible = text[span["start"]:span["end"]]
        return span["form"]["text"] if any(span["quality"]) else visible

    def _display_name(self, selected: str, span: dict, term: dict,
                      occurrence_sources: list[tuple[str, list[dict]]]) -> str:
        current_name = span["form"]["text"] if any(span["quality"]) else selected
        candidates = [(current_name, span["form"])]
        for text, occurrences in occurrence_sources:
            for occurrence in occurrences:
                form = occurrence["form"]
                if form["kind"] == "alias" and not form["transparent"]:
                    continue
                candidates.append((self._display_candidate(text, occurrence), form))

        is_person_name = normalized(term.get("category", "")) == "name"

        def rank(item: tuple[str, dict]) -> tuple[int, int, int]:
            value, _form = item
            tokens = [match.group() for match in _READER_WORDS.finditer(value)]
            honorific = bool(tokens and _fold_token(tokens[0]).rstrip(".") in _HONORIFICS)
            return (0 if is_person_name and honorific else 1, len(tokens), len(value))

        return max(candidates, key=rank)[0]

    @staticmethod
    def _gender_attribute(observation: dict) -> dict[str, str] | None:
        statement = observation.get("statement")
        if observation.get("confidence") != "high" or not isinstance(statement, str) or not statement.strip():
            return None
        folded = _fold_token(statement)
        system = any(marker in folded for marker in (
            "sie/hir", "gender cycle", "cycle between gender", "cykl", "omnia",
            "non-binary", "nonbinary", "not a permanent binary", "fixed binary",
        ))
        if system:
            return {"label": "Gender system", "value": statement.strip()}
        male = re.search(r"\b(?:male|masculine|man|men|mezczy\w*|mesk\w*)\b", folded) is not None
        female = re.search(r"\b(?:female|feminine|woman|women|kobiet\w*|zensk\w*)\b", folded) is not None
        if male and not female:
            return {"label": "Gender", "value": "male"}
        if female and not male:
            return {"label": "Gender", "value": "female"}
        about = observation.get("about")
        if (male and female) or not isinstance(about, list) or len(about) != 1:
            return None
        return {"label": "Gender", "value": statement.strip()}

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

        self._memory()
        resolved = self._resolve_entity(text, position, block_id, cutoff)
        if resolved is None:
            return {"recognized": False}
        span, identity = resolved, resolved["term_id"]
        selected = text[span["start"]:span["end"]]
        term = self._terms_by_id.get(identity, {})

        statements: list[str] = []
        attributes: list[dict[str, str]] = []
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
                if observation.get("kind") == "gender":
                    attribute = self._gender_attribute(observation)
                    if attribute and attribute not in attributes:
                        attributes.append(attribute)
                else:
                    statements.append(statement.strip())

        statements = list(dict.fromkeys(statements))[:4]
        earlier_mentions = []
        evidence_ids = self._term_evidence_ids(term)
        evidence_ids.sort(key=lambda item: self._block_orders.get(item, cutoff))
        seen_blocks: set[str] = set()
        occurrence_sources: list[tuple[str, list[dict]]] = []
        current_prefix = text[:span["start"]]
        occurrence_sources.append((current_prefix, self._term_occurrences(
            current_prefix, identity, block_id, cutoff
        )))
        for previous_id in evidence_ids:
            order = self._block_orders.get(previous_id)
            if (order is None or order >= cutoff or previous_id in seen_blocks
                    or previous_id in self._structural_blocks):
                continue
            previous_chapter = self._block_chapters.get(previous_id)
            if previous_chapter is None:
                continue
            previous_text = self.block_text(previous_chapter, previous_id)
            occurrences = self._term_occurrences(previous_text, identity, previous_id, cutoff)
            occurrence_sources.append((previous_text, occurrences))
            if len(earlier_mentions) < 3:
                phrase = (previous_text[occurrences[0]["start"]:occurrences[0]["end"]]
                          if occurrences else "")
                earlier_mentions.append({
                    "chapter_id": previous_chapter,
                    "chapter_title": self._chapter_titles.get(previous_chapter, previous_chapter),
                    "block_id": previous_id,
                    "text": self._short_excerpt(previous_text, phrase),
                })
            seen_blocks.add(previous_id)

        same_block_context = self._same_block_context(text, position, span, block_id, cutoff)
        display_name = self._display_name(selected, span, term, occurrence_sources)

        return {
            "recognized": True,
            "matched_text": selected,
            "display_name": display_name,
            "title": display_name,
            "attributes": attributes[:4],
            "statements": statements,
            "earlier_mentions": earlier_mentions,
            "same_block_context": same_block_context,
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
