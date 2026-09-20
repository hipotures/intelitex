from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bookpipe import cli as cli_module
from bookpipe.cli import main
from bookpipe.p1_compact import encode_input
from bookpipe.series import prepare_handoff
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, digest, plan_fingerprint, project_lock, read_json


def frozen_book(label: str = "one") -> dict:
    files = [{"file": f"{label}.html", "sha256": digest(label), "encoding": "utf-8"}]
    block = {
        "id": "B0000001", "order": 1, "kind": "paragraph", "text": f"Book {label}.",
        "file": f"{label}.html", "scene_id": "ch0001_s001", "scene_start": True,
    }
    piece = {**block, "parent_id": block["id"], "start": 0, "end": len(block["text"])}
    chunk = {
        "id": "ch0001_c0001", "number": 1, "chapter_id": "ch0001", "index_in_chapter": 1,
        "blocks": [piece], "sentences": [{"id": "S0000001", "text": block["text"]}],
    }
    chapter = {
        "id": "ch0001", "number": 1, "source_file": f"{label}.html", "title": "One",
        "blocks": [block], "pieces": [piece], "chunk_ids": [chunk["id"]],
    }
    book = {
        "format_version": 2,
        "source_root": f"/synthetic/{label}",
        "metadata": {"title": label},
        "warnings": [],
        "files": files,
        "chapters": [chapter],
        "chunks": [chunk],
    }
    book["source_fingerprint"] = digest(files)
    book["content_fingerprint"] = plan_fingerprint(book)
    return book


def predecessor_memory(*, choice: str = "Eldlund", approved: bool = True) -> dict:
    return {
        "format_version": 1,
        "terms": [{
            "id": "T000001",
            "source": "Eldlund",
            "aliases": ["Captain Eldlund"],
            "category": "name",
            "meanings": [
                {"text": "An earlier discarded description.", "confidence": "low", "evidence": ["B0000001"]},
                {"text": "The expedition captain.", "confidence": "high", "evidence": ["B0000002"]},
            ],
            "candidates": [
                {"text": "Eldlund", "reasons": ["Approved"], "evidence": ["B0000002"], "confidence": "high"},
                {"text": "Eldland", "reasons": ["Rejected"], "evidence": ["B0000003"], "confidence": "low"},
            ],
            "evidence": [{
                "block_id": "B0000002", "chapter_id": "ch0001", "order": 2,
                "excerpt": "A predecessor-local excerpt.",
            }],
            "choice": choice,
            "approved": approved,
        }],
        "observations": [{
            "about": ["Eldlund"],
            "kind": "gender",
            "statement": "Eldlund uses feminine grammatical forms.",
            "confidence": "high",
            "evidence": ["B0000002"],
            "chapter_id": "ch0001",
            "available_from_order": 2,
        }],
    }


def approved_lexicon(*, polish: str = "Eldlund") -> dict:
    return {"terms": [{
        "id": "T000001", "source": "Eldlund", "aliases": ["Captain Eldlund"], "polish": polish,
    }]}


def write_predecessor(root: Path, *, label: str = "one", memory: dict | None = None,
                      lexicon: dict | None = None) -> dict:
    root.mkdir(parents=True)
    book = frozen_book(label)
    atomic_json(root / "book.json", book)
    atomic_json(root / "book_memory.json", memory if memory is not None else predecessor_memory())
    atomic_json(root / "lexicon.approved.json", lexicon if lexicon is not None else approved_lexicon())
    (root / "state.sqlite3").write_bytes(b"legacy database must not be opened")
    (root / "prompts").mkdir()
    (root / "prompts" / "pass1.txt").write_text("legacy prompt", encoding="utf-8")
    (root / "artifacts").mkdir()
    (root / "artifacts" / "checkpoint.json").write_text("checkpoint", encoding="utf-8")
    return book


class FakeProviderPool:
    def __init__(self, *args, **kwargs):
        self.identity = {"provider": "test", "id": "test-model"}
        self.tokenizer_identity = {"provider": "test", "id": "test-tokenizer"}

    @staticmethod
    def count(text: str) -> int:
        return max(1, len(text) // 4)

    def discover(self, pass_no: int):
        return {"id": "test-model"}

    def close(self):
        pass


def import_project(monkeypatch, source: Path, root: Path, previous: Path | None = None) -> int:
    monkeypatch.setattr(cli_module, "ProviderPool", FakeProviderPool)
    args = ["import", str(source), "--project", str(root), "--quiet"]
    if previous is not None:
        args.extend(["--previous-volume", str(previous)])
    return main(args)


def source_folder(root: Path, text: str = "Eldlund returned to the ship.") -> Path:
    root.mkdir()
    (root / "one.html").write_text(f"<html><body><h1>One</h1><p>{text}</p></body></html>", encoding="utf-8")
    return root


def test_standalone_import_remains_unseeded(monkeypatch, tmp_path):
    root = tmp_path / "standalone"
    assert import_project(monkeypatch, source_folder(tmp_path / "source"), root) == 0
    assert not (root / "series.json").exists()
    assert not (root / "series.seed.json").exists()
    assert not (root / "book_memory.json").exists()
    store = Store(root)
    try:
        assert store.terms() == []
        assert store.facts() == []
        assert store.get("series_seed") is None
    finally:
        store.close()


def test_legacy_v1_to_v2_retrofit_preserves_frozen_predecessor(monkeypatch, tmp_path):
    previous = tmp_path / "volume-1"
    write_predecessor(previous)
    protected = {
        path.relative_to(previous): path.read_bytes()
        for path in [
            previous / "book.json",
            previous / "state.sqlite3",
            previous / "book_memory.json",
            previous / "lexicon.approved.json",
            previous / "prompts" / "pass1.txt",
            previous / "artifacts" / "checkpoint.json",
        ]
    }
    root = tmp_path / "volume-2"
    assert import_project(monkeypatch, source_folder(tmp_path / "source-2"), root, previous) == 0

    old_series = read_json(previous / "series.json")
    new_series = read_json(root / "series.json")
    assert old_series["volume"] == 1 and old_series["previous"] is None
    assert new_series["volume"] == 2
    assert old_series["series_id"] == new_series["series_id"]
    assert all((previous / relative).read_bytes() == raw for relative, raw in protected.items())
    assert set(path.relative_to(previous) for path in previous.rglob("*") if path.is_file()) == {
        *protected.keys(), Path("series.json"), Path(".lock"),
    }

    seed = read_json(root / "series.seed.json")
    assert seed["source_snapshot"]["source_fingerprint"] == read_json(previous / "book.json")["source_fingerprint"]
    assert new_series["seed_sha256"] == digest((root / "series.seed.json").read_bytes())
    memory = read_json(root / "book_memory.json")
    assert memory["terms"][0]["choice"] == "Eldlund"
    assert memory["terms"][0]["approved"] is True
    assert memory["observations"][0]["series_inherited"] is True
    store = Store(root)
    try:
        assert not store.get("approved", False)
        assert not list((root / "analysis_inputs").glob("*.json")) if (root / "analysis_inputs").exists() else True
    finally:
        store.close()


def test_compaction_is_deterministic_and_strips_local_history(tmp_path):
    previous = tmp_path / "volume-1"
    write_predecessor(previous)
    first = prepare_handoff(previous, tmp_path / "volume-2")["seed"]
    second = prepare_handoff(previous, tmp_path / "another-volume-2")["seed"]
    assert first == second
    assert digest(first) == digest(second)
    term = first["terms"][0]
    assert term == {
        "source": "Eldlund",
        "aliases": ["Captain Eldlund"],
        "category": "name",
        "polish": "Eldlund",
        "first_seen_volume": 1,
        "meaning_notes": [
            {"text": "An earlier discarded description.", "confidence": "low"},
            {"text": "The expedition captain.", "confidence": "high"},
        ],
        "series_context": [{
            "kind": "gender",
            "statement": "Eldlund uses feminine grammatical forms.",
            "confidence": "high",
        }],
    }
    serialized = json.dumps(first, ensure_ascii=False)
    assert str(previous.resolve()) not in serialized
    for forbidden in (
        "block_id", "chapter_id", "available_from_order", "excerpt", "Eldland",
        "candidates", "reasons", "translated", "attempt",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("memory", "lexicon", "message"),
    [
        (predecessor_memory(), approved_lexicon(polish="Eldland"), "disagrees"),
        (predecessor_memory(approved=False), approved_lexicon(), "not approved"),
        (predecessor_memory(choice=""), approved_lexicon(), "approved choice"),
    ],
)
def test_predecessor_approval_consistency_failures(tmp_path, memory, lexicon, message):
    previous = tmp_path / "previous"
    write_predecessor(previous, memory=memory, lexicon=lexicon)
    with pytest.raises(PipelineError, match=message):
        prepare_handoff(previous, tmp_path / "new")
    assert not (previous / "series.json").exists()


def test_seeded_store_first_p1_memory_and_compact_v1_compatibility(tmp_path):
    previous = tmp_path / "previous"
    write_predecessor(previous)
    seed = prepare_handoff(previous, tmp_path / "new")["seed"]
    root = tmp_path / "store"
    root.mkdir()
    store = Store(root)
    try:
        store.seed_series(seed)
        memory = store.analysis_memory("Eldlund entered.", lambda text: len(text), 50_000)
        assert set(memory) == {"matched", "catalogue", "catalogue_incomplete"}
        assert memory["matched"][0]["chosen"] == "Eldlund"
        assert "Eldlund uses feminine grammatical forms." in memory["matched"][0]["meaning_notes"]
        canonical = {
            "SECTION_ID": "ch0001_a001",
            "SOURCE_BLOCKS": [{
                "id": "B0000001", "kind": "paragraph", "scene_id": "ch0001_s001",
                "scene_start": True, "text": "Eldlund entered.",
            }],
            "EXISTING_MEMORY": memory,
        }
        compact, block_ids, _ = encode_input(canonical)
        assert compact["EXISTING_MEMORY"]["m"][0][1] == "Eldlund"
        assert block_ids == ("B0000001",)
    finally:
        store.close()


def _seeded_store(tmp_path: Path) -> tuple[Store, dict]:
    previous = tmp_path / "previous"
    write_predecessor(previous)
    seed = prepare_handoff(previous, tmp_path / "new")["seed"]
    root = tmp_path / "store"
    root.mkdir()
    store = Store(root)
    store.seed_series(seed)
    return store, seed


def test_recurring_alias_merges_once_and_inherited_term_is_prereviewed(tmp_path):
    store, _ = _seeded_store(tmp_path)
    try:
        block = {"id": "B1000001", "order": 1, "text": "The Captain returned."}
        store.merge_analysis("pass1/one", "fp", {
            "terms": [{
                "source": "Eldlund", "aliases": ["The Captain"], "category": "name",
                "meaning": "The same expedition captain.", "confidence": "high",
                "candidates": [{"text": "Eldlund", "reason": "Keep the approved series form"}],
                "evidence": [block["id"]],
            }, {
                "source": "New Relay", "aliases": [], "category": "technology",
                "meaning": "A new device.", "confidence": "medium",
                "candidates": [{"text": "Nowy Przekaźnik", "reason": "Direct rendering"}],
                "evidence": [block["id"]],
            }],
            "observations": [],
        }, [block], "ch0001")
        terms = store.terms()
        assert len(terms) == 2
        inherited = next(term for term in terms if term["source"] == "Eldlund")
        assert "The Captain" in inherited["aliases"]
        assert inherited["choice"] == "Eldlund"
        assert inherited["series_review_required"] is False
        review = read_json(store.write_review("book-fingerprint"))
        by_source = {term["source"]: term for term in review["terms"]}
        assert by_source["Eldlund"]["reviewed"] is True
        assert by_source["Eldlund"]["review_method"] == "inherited"
        assert by_source["New Relay"]["reviewed"] is False
    finally:
        store.close()


@pytest.mark.parametrize(
    ("candidate", "category", "reason"),
    [("Eldland", "name", "preferred_candidate"), ("Eldlund", "organization", "category")],
)
def test_inherited_terminology_disagreement_requires_review(tmp_path, candidate, category, reason):
    store, _ = _seeded_store(tmp_path)
    try:
        block = {"id": "B1000001", "order": 1, "text": "Eldlund returned."}
        store.merge_analysis("pass1/one", "fp", {
            "terms": [{
                "source": "Eldlund", "aliases": [], "category": category,
                "meaning": "Current evidence.", "confidence": "high",
                "candidates": [{"text": candidate, "reason": "Current preferred form"}],
                "evidence": [block["id"]],
            }],
            "observations": [],
        }, [block], "ch0001")
        term = store.terms()[0]
        assert term["choice"] == "Eldlund"
        assert term["series_review_required"] is True
        assert reason in term["series_disagreement"]
        review = read_json(store.write_review("book-fingerprint"))
        assert review["terms"][0]["reviewed"] is False
        assert review["terms"][0]["review_method"] == "series_disagreement"
    finally:
        store.close()


def test_inherited_observation_is_immediate_and_current_spoiler_gate_remains(tmp_path):
    store, _ = _seeded_store(tmp_path)
    try:
        late = {"id": "B1000020", "order": 20, "text": "Eldlund reveals the route."}
        store.merge_analysis("pass1/late", "fp", {
            "terms": [],
            "observations": [{
                "about": ["Eldlund"], "kind": "continuity", "statement": "The route is revealed late.",
                "confidence": "high", "evidence": [late["id"]],
            }],
        }, [late], "ch0001")
        early_chunk = {
            "chapter_id": "ch0001",
            "blocks": [{"id": "B1000001", "order": 1, "text": "Eldlund waits."}],
        }
        memory, dependencies = store.translation_memory(early_chunk, "", lambda text: len(text), 50_000)
        assert dependencies == ["T000001"]
        assert memory["APPROVED_LEXICON"][0]["polish"] == "Eldlund"
        statements = {item["statement"] for item in memory["OBSERVATIONS"]}
        assert "Eldlund uses feminine grammatical forms." in statements
        assert "The route is revealed late." not in statements
        unrelated = copy.deepcopy(early_chunk)
        unrelated["blocks"][0]["text"] = "Nobody waits."
        unrelated_memory, _ = store.translation_memory(unrelated, "", lambda text: len(text), 50_000)
        assert unrelated_memory["OBSERVATIONS"] == []
    finally:
        store.close()


def test_v2_to_v3_is_cumulative_without_reopening_v1(monkeypatch, tmp_path):
    volume1 = tmp_path / "volume-1"
    write_predecessor(volume1)
    volume2 = tmp_path / "volume-2"
    assert import_project(monkeypatch, source_folder(tmp_path / "source-2"), volume2, volume1) == 0

    memory = read_json(volume2 / "book_memory.json")
    memory["terms"].append({
        "id": "T000002", "source": "Helios", "aliases": [], "category": "ship",
        "meanings": [{"text": "The survey ship.", "confidence": "high", "evidence": ["B0000001"]}],
        "candidates": [{"text": "Helios", "reasons": ["Approved"], "evidence": ["B0000001"], "confidence": "high"}],
        "evidence": [{"block_id": "B0000001", "chapter_id": "ch0001", "order": 1, "excerpt": "Helios"}],
        "choice": "Helios", "approved": True,
    })
    atomic_json(volume2 / "book_memory.json", memory)
    atomic_json(volume2 / "lexicon.approved.json", {"terms": [
        {"id": term["id"], "source": term["source"], "aliases": term["aliases"], "polish": term["choice"]}
        for term in memory["terms"]
    ]})
    hidden_v1 = tmp_path / "volume-1-offline"
    volume1.rename(hidden_v1)

    volume3 = tmp_path / "volume-3"
    assert import_project(
        monkeypatch, source_folder(tmp_path / "source-3", "Eldlund boarded Helios."), volume3, volume2
    ) == 0
    series2 = read_json(volume2 / "series.json")
    series3 = read_json(volume3 / "series.json")
    assert series3["volume"] == 3
    assert series3["series_id"] == series2["series_id"]
    seed3 = read_json(volume3 / "series.seed.json")
    assert [term["source"] for term in seed3["terms"]] == ["Eldlund", "Helios"]
    assert [term["first_seen_volume"] for term in seed3["terms"]] == [1, 2]


def test_malformed_conflicting_series_metadata_and_alias_ambiguity_fail_closed(tmp_path):
    previous = tmp_path / "previous"
    book = write_predecessor(previous)
    atomic_json(previous / "series.json", {
        "format_version": 99,
        "series_id": "series-bad",
        "volume": 1,
        "source_fingerprint": book["source_fingerprint"],
        "previous": None,
        "seed_sha256": None,
    })
    with pytest.raises(PipelineError, match="Unsupported series.json"):
        prepare_handoff(previous, tmp_path / "new")

    (previous / "series.json").unlink()
    memory = predecessor_memory()
    second = copy.deepcopy(memory["terms"][0])
    second.update(id="T000002", source="Another", aliases=["Eldlund"], choice="Inny")
    memory["terms"].append(second)
    atomic_json(previous / "book_memory.json", memory)
    lexicon = approved_lexicon()
    lexicon["terms"].append({"id": "T000002", "source": "Another", "aliases": ["Eldlund"], "polish": "Inny"})
    atomic_json(previous / "lexicon.approved.json", lexicon)
    with pytest.raises(PipelineError, match="Ambiguous inherited alias"):
        prepare_handoff(previous, tmp_path / "new")


def test_missing_artifacts_same_path_and_locked_predecessor_fail(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(PipelineError, match="missing book.json"):
        prepare_handoff(missing, tmp_path / "new")

    previous = tmp_path / "previous"
    write_predecessor(previous)
    with pytest.raises(PipelineError, match="different paths"):
        prepare_handoff(previous, previous)
    with project_lock(previous):
        with pytest.raises(PipelineError, match="currently in use"):
            prepare_handoff(previous, tmp_path / "new")


@pytest.mark.parametrize("missing_name", ["book_memory.json", "lexicon.approved.json"])
def test_missing_canonical_handoff_artifacts_fail_closed(tmp_path, missing_name):
    previous = tmp_path / "previous"
    write_predecessor(previous)
    (previous / missing_name).unlink()
    with pytest.raises(PipelineError, match=missing_name):
        prepare_handoff(previous, tmp_path / "new")
    assert not (previous / "series.json").exists()


def test_frozen_and_series_source_fingerprint_conflicts_fail_closed(tmp_path):
    previous = tmp_path / "previous"
    book = write_predecessor(previous)
    broken = copy.deepcopy(book)
    broken["source_fingerprint"] = "not-the-files-fingerprint"
    atomic_json(previous / "book.json", broken)
    with pytest.raises(PipelineError, match="invalid source_fingerprint"):
        prepare_handoff(previous, tmp_path / "new")

    atomic_json(previous / "book.json", book)
    atomic_json(previous / "series.json", {
        "format_version": 1,
        "series_id": "series-conflict",
        "volume": 1,
        "source_fingerprint": "not-the-book-fingerprint",
        "previous": None,
        "seed_sha256": None,
    })
    with pytest.raises(PipelineError, match="conflicts with the frozen book.json"):
        prepare_handoff(previous, tmp_path / "new")


def test_existing_continuation_cannot_claim_a_different_series(tmp_path, monkeypatch):
    volume1 = tmp_path / "volume-1"
    write_predecessor(volume1)
    volume2 = tmp_path / "volume-2"
    assert import_project(monkeypatch, source_folder(tmp_path / "source-2"), volume2, volume1) == 0
    memory = read_json(volume2 / "book_memory.json")
    atomic_json(volume2 / "lexicon.approved.json", {"terms": [
        {"id": term["id"], "source": term["source"], "aliases": term["aliases"], "polish": term["choice"]}
        for term in memory["terms"]
    ]})
    series = read_json(volume2 / "series.json")
    series["series_id"] = "series-conflicting-identity"
    atomic_json(volume2 / "series.json", series)
    with pytest.raises(PipelineError, match="series.seed.json conflicts"):
        prepare_handoff(volume2, tmp_path / "volume-3")
