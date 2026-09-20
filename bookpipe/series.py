from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .project_config import prepare_configuration
from .util import PipelineError, atomic_json, digest, file_lock, normalized, plan_fingerprint


SERIES_FORMAT_VERSION = 1
MEMORY_FORMAT_VERSION = 1
REUSABLE_OBSERVATION_KINDS = {"reference", "gender", "register", "technical", "continuity"}
TERM_CATEGORIES = {
    "name", "organization", "people", "place", "ship", "status",
    "technology", "science", "jargon", "other",
}
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
MAX_MEANING_NOTES = 2
MAX_SERIES_CONTEXT = 3


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise PipelineError(f"{label} must contain a JSON object.")
    return value


def _array(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise PipelineError(f"{label} must contain a JSON array.")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{label} must be a non-empty string.")
    return value


def _read_json_bytes(path: Path, label: str) -> tuple[dict, bytes]:
    if not path.is_file():
        raise PipelineError(f"Previous volume is missing {path.name}; {label} is required for continuation.")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Previous volume {path.name} is not valid UTF-8 JSON: {exc}.") from exc
    return _object(value, f"Previous volume {path.name}"), raw


def _validate_book(book: dict) -> str:
    try:
        calculated_plan = plan_fingerprint(book)
    except (KeyError, TypeError) as exc:
        raise PipelineError("Previous volume book.json is not a supported frozen book manifest.") from exc
    if book.get("content_fingerprint") != calculated_plan:
        raise PipelineError("Previous volume book.json has an invalid content_fingerprint; restore its frozen manifest.")
    source_fingerprint = _nonempty_string(
        book.get("source_fingerprint"), "Previous volume book.json source_fingerprint"
    )
    if source_fingerprint != digest(book.get("files")):
        raise PipelineError("Previous volume book.json has an invalid source_fingerprint; restore its frozen manifest.")
    return source_fingerprint


def deterministic_series_id(first_volume_source_fingerprint: str) -> str:
    value = _nonempty_string(first_volume_source_fingerprint, "First-volume source fingerprint")
    return "series-" + digest({"first_volume_source_fingerprint": value})[:20]


def _validate_previous_link(value: Any, volume: int) -> dict | None:
    if volume == 1:
        if value is not None:
            raise PipelineError("series.json volume 1 must have previous=null.")
        return None
    previous = _object(value, "series.json previous")
    if set(previous) != {"volume", "source_fingerprint"}:
        raise PipelineError("series.json previous has unsupported or missing fields.")
    if type(previous.get("volume")) is not int or previous["volume"] != volume - 1:
        raise PipelineError("series.json previous volume must be exactly one less than this volume.")
    _nonempty_string(previous.get("source_fingerprint"), "series.json previous source_fingerprint")
    return previous


def validate_series_metadata(root: Path, book_source_fingerprint: str, value: Any) -> dict:
    metadata = _object(value, "series.json")
    expected = {"format_version", "series_id", "volume", "source_fingerprint", "previous", "seed_sha256"}
    if set(metadata) != expected:
        raise PipelineError("series.json has unsupported or missing fields.")
    if metadata.get("format_version") != SERIES_FORMAT_VERSION:
        raise PipelineError(f"Unsupported series.json format_version: {metadata.get('format_version')!r}.")
    series_id = _nonempty_string(metadata.get("series_id"), "series.json series_id")
    if not series_id.startswith("series-"):
        raise PipelineError("series.json series_id must be an opaque series-* identifier.")
    volume = metadata.get("volume")
    if type(volume) is not int or volume <= 0:
        raise PipelineError("series.json volume must be a positive integer.")
    if metadata.get("source_fingerprint") != book_source_fingerprint:
        raise PipelineError("series.json source_fingerprint conflicts with the frozen book.json.")
    previous = _validate_previous_link(metadata.get("previous"), volume)
    seed_hash = metadata.get("seed_sha256")
    if volume == 1:
        if seed_hash is not None:
            raise PipelineError("series.json volume 1 must have seed_sha256=null.")
    else:
        _nonempty_string(seed_hash, "series.json seed_sha256")
        seed_path = root / "series.seed.json"
        if not seed_path.is_file():
            raise PipelineError("Series predecessor is missing series.seed.json.")
        seed_raw = seed_path.read_bytes()
        if digest(seed_raw) != seed_hash:
            raise PipelineError("Series predecessor series.seed.json does not match series.json seed_sha256.")
        try:
            seed = _object(json.loads(seed_raw.decode("utf-8")), "series.seed.json")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Series predecessor series.seed.json is invalid UTF-8 JSON: {exc}.") from exc
        if (seed.get("format_version") != SERIES_FORMAT_VERSION
                or seed.get("series_id") != series_id
                or seed.get("from_volume") != volume - 1
                or seed.get("to_volume") != volume):
            raise PipelineError("Series predecessor series.seed.json conflicts with series.json.")
        snapshot = _object(seed.get("source_snapshot"), "series.seed.json source_snapshot")
        if set(snapshot) != {"source_fingerprint", "book_memory_sha256", "approved_lexicon_sha256"}:
            raise PipelineError("Series predecessor seed source snapshot has unsupported or missing fields.")
        _nonempty_string(snapshot.get("book_memory_sha256"), "series.seed.json book_memory_sha256")
        _nonempty_string(snapshot.get("approved_lexicon_sha256"), "series.seed.json approved_lexicon_sha256")
        if snapshot.get("source_fingerprint") != previous["source_fingerprint"]:
            raise PipelineError("Series predecessor seed source snapshot conflicts with series.json.")
    return copy.deepcopy(metadata)


def _validated_strings(values: Any, label: str) -> list[str]:
    result = []
    seen = set()
    for index, value in enumerate(_array(values, label)):
        text = _nonempty_string(value, f"{label}[{index}]")
        key = normalized(text)
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _compact_meanings(term: dict, label: str) -> list[dict]:
    meanings = _array(term.get("meanings"), f"{label} meanings")
    result: list[dict] = []
    seen = set()
    for index in range(len(meanings) - 1, -1, -1):
        meaning = _object(meanings[index], f"{label} meanings[{index}]")
        text = meaning.get("text")
        confidence = meaning.get("confidence")
        if not isinstance(text, str) or not text.strip():
            continue
        if confidence not in CONFIDENCE_RANK:
            raise PipelineError(f"{label} has an invalid meaning confidence.")
        key = normalized(text)
        if key in seen:
            continue
        seen.add(key)
        result.append({"text": text, "confidence": confidence})
        if len(result) == MAX_MEANING_NOTES:
            break
    result.reverse()
    return result


def _validate_observation(value: Any, label: str, default_volume: int) -> dict:
    observation = _object(value, label)
    _array(observation.get("evidence"), f"{label} evidence")
    about = _validated_strings(observation.get("about"), f"{label} about")
    if not about:
        raise PipelineError(f"{label} about must not be empty.")
    kind = observation.get("kind")
    if kind not in REUSABLE_OBSERVATION_KINDS:
        raise PipelineError(f"{label} has unsupported kind {kind!r}.")
    statement = _nonempty_string(observation.get("statement"), f"{label} statement")
    confidence = observation.get("confidence")
    if confidence not in CONFIDENCE_RANK:
        raise PipelineError(f"{label} has invalid confidence {confidence!r}.")
    first_seen = observation.get("series_first_seen_volume", observation.get("first_seen_volume", default_volume))
    if type(first_seen) is not int or first_seen <= 0 or first_seen > default_volume:
        raise PipelineError(f"{label} has an invalid first-seen volume.")
    return {
        "about": sorted(about, key=lambda item: (normalized(item), item)),
        "kind": kind,
        "statement": statement,
        "confidence": confidence,
        "first_seen_volume": first_seen,
    }


def _observation_key(observation: dict) -> tuple:
    return (
        tuple(sorted(normalized(item) for item in observation["about"])),
        observation["kind"],
        normalized(observation["statement"]),
    )


def _deduplicate_observations(observations: list[dict]) -> list[dict]:
    combined: dict[tuple, dict] = {}
    for observation in observations:
        key = _observation_key(observation)
        existing = combined.get(key)
        if existing is None:
            combined[key] = copy.deepcopy(observation)
            continue
        existing["first_seen_volume"] = min(existing["first_seen_volume"], observation["first_seen_volume"])
        if CONFIDENCE_RANK[observation["confidence"]] > CONFIDENCE_RANK[existing["confidence"]]:
            existing["confidence"] = observation["confidence"]
    return [combined[key] for key in sorted(combined)]


def _context_for_term(term: dict, observations: list[dict]) -> list[dict]:
    names = {normalized(term["source"]), *(normalized(alias) for alias in term["aliases"])}
    contexts = []
    raw_context = term.get("series_context", [])
    for item in _array(raw_context, f"Term {term['source']!r} series_context"):
        context = _object(item, f"Term {term['source']!r} series_context item")
        kind = context.get("kind")
        statement = context.get("statement")
        confidence = context.get("confidence")
        if kind not in REUSABLE_OBSERVATION_KINDS or not isinstance(statement, str) or not statement.strip():
            raise PipelineError(f"Term {term['source']!r} has malformed series_context.")
        if confidence not in CONFIDENCE_RANK:
            raise PipelineError(f"Term {term['source']!r} has invalid series_context confidence.")
        contexts.append({"kind": kind, "statement": statement, "confidence": confidence})
    for observation in observations:
        if names & {normalized(name) for name in observation["about"]}:
            contexts.append({key: observation[key] for key in ("kind", "statement", "confidence")})
    by_key: dict[tuple, dict] = {}
    for context in contexts:
        key = (context["kind"], normalized(context["statement"]))
        old = by_key.get(key)
        if old is None or CONFIDENCE_RANK[context["confidence"]] > CONFIDENCE_RANK[old["confidence"]]:
            by_key[key] = context
    ranked = sorted(by_key.values(), key=lambda item: (
        -CONFIDENCE_RANK[item["confidence"]], item["kind"], normalized(item["statement"])
    ))
    return ranked[:MAX_SERIES_CONTEXT]


def build_seed(*, book: dict, memory: dict, approved_lexicon: dict, series_id: str,
               predecessor_volume: int, memory_sha256: str, lexicon_sha256: str) -> dict:
    if memory.get("format_version") != MEMORY_FORMAT_VERSION:
        raise PipelineError(f"Unsupported previous book_memory.json format_version: {memory.get('format_version')!r}.")
    memory_terms = _array(memory.get("terms"), "Previous book_memory.json terms")
    memory_observations = _array(memory.get("observations"), "Previous book_memory.json observations")
    lexicon_terms = _array(approved_lexicon.get("terms"), "Previous lexicon.approved.json terms")

    lexicon_by_id: dict[str, dict] = {}
    for index, raw in enumerate(lexicon_terms):
        item = _object(raw, f"Approved lexicon term {index}")
        term_id = _nonempty_string(item.get("id"), f"Approved lexicon term {index} id")
        if term_id in lexicon_by_id:
            raise PipelineError(f"Approved lexicon contains duplicate term ID {term_id}.")
        _nonempty_string(item.get("source"), f"Approved lexicon term {term_id} source")
        _validated_strings(item.get("aliases"), f"Approved lexicon term {term_id} aliases")
        _nonempty_string(item.get("polish"), f"Approved lexicon term {term_id} polish")
        lexicon_by_id[term_id] = item

    compact_observations = _deduplicate_observations([
        _validate_observation(value, f"Previous observation {index}", predecessor_volume)
        for index, value in enumerate(memory_observations)
    ])
    compact_terms = []
    all_names: dict[str, str] = {}
    memory_ids = set()
    for index, raw in enumerate(memory_terms):
        term = _object(raw, f"Previous memory term {index}")
        term_id = _nonempty_string(term.get("id"), f"Previous memory term {index} id")
        if term_id in memory_ids:
            raise PipelineError(f"Previous book_memory.json contains duplicate term ID {term_id}.")
        memory_ids.add(term_id)
        source = _nonempty_string(term.get("source"), f"Previous memory term {term_id} source")
        aliases = _validated_strings(term.get("aliases"), f"Previous memory term {term_id} aliases")
        aliases = [alias for alias in aliases if normalized(alias) != normalized(source)]
        category = term.get("category")
        if category not in TERM_CATEGORIES:
            raise PipelineError(f"Previous memory term {term_id} has invalid category {category!r}.")
        _array(term.get("evidence"), f"Previous memory term {term_id} evidence")
        candidates = _array(term.get("candidates"), f"Previous memory term {term_id} candidates")
        if not candidates:
            raise PipelineError(f"Previous memory term {term_id} has no candidate history.")
        for candidate_index, raw_candidate in enumerate(candidates):
            candidate = _object(raw_candidate, f"Previous memory term {term_id} candidate {candidate_index}")
            _nonempty_string(candidate.get("text"), f"Previous memory term {term_id} candidate text")
            reasons = _array(candidate.get("reasons"), f"Previous memory term {term_id} candidate reasons")
            if not all(isinstance(reason, str) for reason in reasons):
                raise PipelineError(f"Previous memory term {term_id} candidate reasons must be strings.")
            _array(candidate.get("evidence"), f"Previous memory term {term_id} candidate evidence")
            if candidate.get("confidence") not in CONFIDENCE_RANK:
                raise PipelineError(f"Previous memory term {term_id} candidate has invalid confidence.")
        choice = _nonempty_string(term.get("choice"), f"Previous memory term {term_id} approved choice")
        if term.get("approved") is not True:
            raise PipelineError(f"Previous memory term {term_id} is not approved; approve all terminology first.")
        lexicon = lexicon_by_id.get(term_id)
        if lexicon is None:
            raise PipelineError(f"Approved lexicon is missing previous memory term {term_id}.")
        lex_aliases = _validated_strings(lexicon.get("aliases"), f"Approved lexicon term {term_id} aliases")
        if (normalized(lexicon["source"]) != normalized(source)
                or {normalized(alias) for alias in lex_aliases} != {normalized(alias) for alias in aliases}
                or lexicon["polish"] != choice):
            raise PipelineError(f"Approved lexicon term {term_id} disagrees with book_memory.json.")
        for name in [source, *aliases]:
            key = normalized(name)
            owner = all_names.get(key)
            if owner is not None and owner != term_id:
                raise PipelineError(
                    f"Ambiguous inherited alias {name!r} belongs to both {owner} and {term_id}; resolve it in the predecessor."
                )
            all_names[key] = term_id
        first_seen = term.get("series_first_seen_volume", term.get("first_seen_volume", predecessor_volume))
        if type(first_seen) is not int or first_seen <= 0 or first_seen > predecessor_volume:
            raise PipelineError(f"Previous memory term {term_id} has an invalid first-seen volume.")
        compact = {
            "source": source,
            "aliases": aliases,
            "category": category,
            "polish": choice,
            "first_seen_volume": first_seen,
            "meaning_notes": _compact_meanings(term, f"Previous memory term {term_id}"),
        }
        compact["series_context"] = _context_for_term(term, compact_observations)
        compact_terms.append(compact)
    if set(lexicon_by_id) != memory_ids:
        extras = sorted(set(lexicon_by_id) - memory_ids)
        raise PipelineError(f"Approved lexicon contains terms absent from book_memory.json: {extras}.")

    compact_terms.sort(key=lambda term: (normalized(term["source"]), term["source"]))
    return {
        "format_version": SERIES_FORMAT_VERSION,
        "series_id": series_id,
        "from_volume": predecessor_volume,
        "to_volume": predecessor_volume + 1,
        "source_snapshot": {
            "source_fingerprint": book["source_fingerprint"],
            "book_memory_sha256": memory_sha256,
            "approved_lexicon_sha256": lexicon_sha256,
        },
        "terms": compact_terms,
        "observations": compact_observations,
    }


def prepare_handoff(previous_root: Path, new_root: Path, *, new_source: Path | None = None) -> dict:
    previous_root = previous_root.resolve()
    new_root = new_root.resolve()
    if previous_root == new_root:
        raise PipelineError("The new project and --previous-volume must be different paths.")
    if not previous_root.is_dir() or not (previous_root / "book.json").is_file():
        raise PipelineError("Previous volume project is missing book.json.")
    with file_lock(
        previous_root, ".lock",
        "Previous volume project is currently in use; stop the other Intelitex command and retry.",
    ):
        book, _ = _read_json_bytes(previous_root / "book.json", "a frozen manifest")
        source_fingerprint = _validate_book(book)
        memory, memory_raw = _read_json_bytes(previous_root / "book_memory.json", "completed Pass-1 memory")
        lexicon, lexicon_raw = _read_json_bytes(previous_root / "lexicon.approved.json", "approved terminology")

        series_path = previous_root / "series.json"
        if series_path.exists():
            try:
                series_value = json.loads(series_path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PipelineError(f"Previous volume series.json is invalid UTF-8 JSON: {exc}.") from exc
            predecessor_series = validate_series_metadata(
                previous_root, source_fingerprint, series_value
            )
        else:
            predecessor_series = {
                "format_version": SERIES_FORMAT_VERSION,
                "series_id": deterministic_series_id(source_fingerprint),
                "volume": 1,
                "source_fingerprint": source_fingerprint,
                "previous": None,
                "seed_sha256": None,
            }

        seed = build_seed(
            book=book,
            memory=memory,
            approved_lexicon=lexicon,
            series_id=predecessor_series["series_id"],
            predecessor_volume=predecessor_series["volume"],
            memory_sha256=digest(memory_raw),
            lexicon_sha256=digest(lexicon_raw),
        )
        configuration = None
        if new_source is not None:
            configuration = prepare_configuration(previous_root, new_root, book.get("source_root"), new_source)
        if not series_path.exists():
            # This is the sole permitted mutation of a legacy predecessor.
            atomic_json(series_path, predecessor_series)
        return {
            "series_id": predecessor_series["series_id"],
            "previous_volume": predecessor_series["volume"],
            "previous_source_fingerprint": source_fingerprint,
            "seed": seed,
            "configuration": configuration,
        }


def continuation_metadata(book_source_fingerprint: str, handoff: dict, seed_sha256: str) -> dict:
    return {
        "format_version": SERIES_FORMAT_VERSION,
        "series_id": handoff["series_id"],
        "volume": handoff["previous_volume"] + 1,
        "source_fingerprint": book_source_fingerprint,
        "previous": {
            "volume": handoff["previous_volume"],
            "source_fingerprint": handoff["previous_source_fingerprint"],
        },
        "seed_sha256": seed_sha256,
    }
