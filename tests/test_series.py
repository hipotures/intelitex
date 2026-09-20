from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bookpipe import cli as cli_module
from bookpipe import project_config as config_module
from bookpipe.cli import main
from bookpipe.catalog import bundled_catalog_path, load_catalog
from bookpipe.p1_compact import encode_input
from bookpipe.profiles import resolve_profile
from bookpipe.project_config import remap_path
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
    atomic_json(root / "settings.json", read_json(cli_module.BUNDLE / "settings.default.json"))
    (root / "prompts").mkdir()
    for number in range(1, 6):
        (root / "prompts" / f"pass{number}.txt").write_text(f"legacy prompt {number}", encoding="utf-8")
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
        for path in previous.rglob("*") if path.is_file()
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


# Configuration inheritance: only synthetic projects and a no-I/O provider fake.
@pytest.fixture
def configuration_pool(monkeypatch):
    captures = []

    class ConfigurationPool(FakeProviderPool):
        def __init__(self, settings, ui, project, **overrides):
            name, profile, _ = resolve_profile(settings, 1, project=project, **overrides)
            self.identity = {"provider": profile["provider"], "requested_model": profile["model"], "profile": name}
            self.tokenizer_identity = {"model": profile["model"], "method": "synthetic-counter"}
            captures.append({
                "settings": copy.deepcopy(settings), "profile": profile, "name": name,
                "disk_settings": read_json(project / "settings.json") if (project / "settings.json").exists() else None,
                "prompts": {p.name: p.read_bytes() for p in (project / "prompts").glob("*.txt")},
                "catalog": load_catalog(project),
            })

    monkeypatch.setattr(cli_module, "ProviderPool", ConfigurationPool)
    return captures


def tuned_configuration(previous):
    settings = read_json(previous / "settings.json")
    settings.update(default_profile="series-high", pass_profiles={"1": "series-high", "3": "series-low"},
                    memory_tokens=23000, continuity_tokens=1300, json_retries=3,
                    request_timeout=700, analysis_source_limit=25000, whole_section_char_limit=15000)
    high = {
        "provider": "codex", "enabled": True, "model": "synthetic-series-model",
        "reasoning_effort": "high", "context_size": 190000,
        "planning_output_reserve": 18000, "max_output_tokens": None,
        "request_timeout": 800, "executable": "codex",
        "options": {"p1_wire_format": "canonical", "context_margin_tokens": 3000,
                    "auth_source": str(previous.parent / "shared" / "auth.json")},
    }
    settings["profiles"].update({"series-high": high, "series-low": {**high, "reasoning_effort": "low"}})
    atomic_json(previous / "settings.json", settings)
    return settings


def config_import(previous, root, source, *extra):
    args = ["import", str(source), "--project", str(root), "--quiet", *extra]
    if previous is not None:
        args += ["--previous-volume", str(previous)]
    return main(args)


def test_configuration_is_inherited_before_provider_and_sets_book_identity(configuration_pool, tmp_path):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = tuned_configuration(previous)
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 0
    capture = configuration_pool[0]
    assert capture["settings"] == capture["disk_settings"] == settings
    assert read_json(root / "settings.json") == settings
    assert capture["profile"]["reasoning_effort"] == "high"
    assert capture["profile"]["options"]["p1_wire_format"] == "canonical"
    assert capture["catalog"][1] == bundled_catalog_path()
    assert not (root / "catalog" / "models.json").exists()
    book = read_json(root / "book.json")
    assert book["model_identity"] == {
        "provider": "codex", "requested_model": "synthetic-series-model", "profile": "series-high",
    }
    assert book["tokenizer_identity"] == {"model": "synthetic-series-model", "method": "synthetic-counter"}
    assert book["planning_settings"]["whole_section_char_limit"] == 15000
    assert not (root / "artifacts" / "checkpoint.json").exists()


def test_catalog_and_exact_prompts_are_available_before_resolution(configuration_pool, tmp_path):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = tuned_configuration(previous)
    for name in ("series-high", "series-low"):
        settings["profiles"][name].pop("context_size")
    atomic_json(previous / "settings.json", settings)
    catalog = {"schema_version": 1, "models": [{
        "provider": "codex", "id": "synthetic-series-model", "context_tokens": 987654,
        "efforts": ["low", "high"], "pricing": {"currency": "USD", "unit": "million_tokens", "input": 2.5},
    }]}
    atomic_json(previous / "catalog" / "models.json", catalog)
    (previous / "prompts" / "pass1.txt").write_bytes("Custom P1\r\nZażółć.\r\n".encode("utf-8"))
    (previous / "prompts" / "extra.txt").write_text("Extra project prompt", encoding="utf-8")
    before = {p.relative_to(previous): p.read_bytes() for p in previous.rglob("*") if p.is_file()}
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 0
    capture = configuration_pool[0]
    assert capture["catalog"] == (catalog, root / "catalog" / "models.json")
    assert capture["profile"]["context_size"] == 987654
    assert read_json(root / "catalog" / "models.json") == catalog
    expected_prompts = {p.name: p.read_bytes() for p in (previous / "prompts").glob("*.txt")}
    assert capture["prompts"] == expected_prompts
    assert {p.name: p.read_bytes() for p in (root / "prompts").glob("*.txt")} == expected_prompts
    assert all((previous / name).read_bytes() == raw for name, raw in before.items())
    assert {p.relative_to(previous) for p in previous.rglob("*") if p.is_file()} == {
        *before, Path(".lock"), Path("series.json"),
    }


@pytest.mark.parametrize("local_catalog", [False, True])
def test_standalone_configuration_behavior(configuration_pool, tmp_path, local_catalog):
    root = tmp_path / "standalone"
    if local_catalog:
        atomic_json(root / "catalog" / "models.json", {"schema_version": 1, "models": []})
        settings = read_json(cli_module.BUNDLE / "settings.default.json")
        settings.update(memory_tokens=21000, whole_section_char_limit=12345)
        atomic_json(root / "settings.json", settings)
    assert config_import(None, root, source_folder(tmp_path / "source")) == 0
    saved = read_json(root / "settings.json")
    assert saved["whole_section_char_limit"] == 10000
    assert saved["memory_tokens"] == (21000 if local_catalog else 12000)
    expected_catalog = root / "catalog" / "models.json" if local_catalog else bundled_catalog_path()
    assert configuration_pool[0]["catalog"][1] == expected_catalog
    for prompt in (cli_module.BUNDLE / "prompts").glob("*.txt"):
        assert (root / "prompts" / prompt.name).read_bytes() == prompt.read_bytes()
    assert not list(root.glob("series*"))


@pytest.mark.parametrize("override,expected", [(None, 15000), (8000, 8000)])
def test_continuation_whole_section_limit_precedence(configuration_pool, tmp_path, override, expected):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    tuned_configuration(previous)
    extra = [] if override is None else ["--whole-section-limit", str(override)]
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source"), *extra) == 0
    assert read_json(root / "settings.json")["whole_section_char_limit"] == expected
    assert read_json(root / "book.json")["planning_settings"]["whole_section_char_limit"] == expected


def test_continuation_persisted_llama_cli_overrides(configuration_pool, tmp_path):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source"),
                         "--host", "example.invalid", "--port", "9876", "--model", "new-model",
                         "--context-size", "65536", "--thinking", "analysis", "--whole-section-limit", "8000") == 0
    saved = read_json(root / "settings.json")
    assert {key: saved[key] for key in ("host", "port", "model", "context_size", "thinking")} == {
        "host": "example.invalid", "port": 9876, "model": "new-model", "context_size": 65536, "thinking": "analysis",
    }
    profile = configuration_pool[0]["profile"]
    assert profile["endpoint"] == "example.invalid"
    assert profile["model"] == "new-model" and profile["context_size"] == 65536
    assert profile["options"]["port"] == 9876 and profile["options"]["thinking"] == "analysis"
    assert configuration_pool[0]["disk_settings"] == saved


@pytest.mark.parametrize("extra,expected", [
    (["--profile", "series-low"], "series-low"),
    (["--profile", "series-high", "--pass-profile", "P1=series-low"], "series-low"),
])
def test_command_profile_overrides_are_not_persisted(configuration_pool, tmp_path, extra, expected):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = tuned_configuration(previous)
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source"), *extra) == 0
    assert configuration_pool[0]["name"] == expected
    assert configuration_pool[0]["profile"]["reasoning_effort"] == "low"
    assert read_json(root / "settings.json") == settings
    assert read_json(root / "book.json")["model_identity"]["profile"] == expected


@pytest.mark.parametrize("provider", ["codex", "llamacpp"])
def test_explicit_scalar_overrides_reach_selected_inherited_p1_profile(configuration_pool, tmp_path, provider):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = tuned_configuration(previous)
    selected = "series-low" if provider == "codex" else "local"
    settings["pass_profiles"]["1"] = selected
    atomic_json(previous / "settings.json", settings)
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source"),
                         "--model", "override-model", "--context-size", "75000",
                         "--host", "override.invalid", "--port", "9999", "--thinking", "analysis") == 0
    profile = configuration_pool[0]["profile"]
    assert profile["model"] == "override-model" and profile["context_size"] == 75000
    if provider == "llamacpp":
        assert profile["endpoint"] == "override.invalid"
        assert profile["options"]["port"] == 9999 and profile["options"]["thinking"] == "analysis"
    saved = read_json(root / "settings.json")
    assert saved["profiles"]["series-high"] == settings["profiles"]["series-high"]
    assert saved["default_profile"] == "series-high" and saved["pass_profiles"]["1"] == selected


@pytest.mark.parametrize("field,value", [
    ("runtime_root", "/shared/runtime"), ("executable", "/shared/bin/codex"),
    ("executable", "codex"), ("options.auth_source", "/shared/auth.json"),
])
def test_shared_paths_and_path_lookup_unchanged(tmp_path, field, value):
    assert remap_path(value, field, tmp_path / "old-project", tmp_path / "old-source",
                      tmp_path / "new-project", tmp_path / "new-source") == value


@pytest.mark.parametrize("location", ["project", "source"])
def test_volume_local_runtime_remapping(configuration_pool, tmp_path, location):
    previous = tmp_path / "v1"
    old_source = tmp_path / "source-1"
    book = write_predecessor(previous)
    book["source_root"] = str(old_source)
    atomic_json(previous / "book.json", book)
    settings = tuned_configuration(previous)
    runtime = (previous if location == "project" else old_source) / "scratch" / "runtime"
    settings["profiles"]["series-high"]["runtime_root"] = str(runtime)
    # A model string which happens to look like a path must not be rewritten.
    settings["profiles"]["series-high"]["model"] = str(previous / "opaque-model-id")
    atomic_json(previous / "settings.json", settings)
    root = tmp_path / "v2"
    source = source_folder(tmp_path / "source-2")
    assert config_import(previous, root, source) == 0
    saved = read_json(root / "settings.json")["profiles"]["series-high"]
    assert saved["runtime_root"] == str((root if location == "project" else source) / "scratch" / "runtime")
    assert saved["model"] == settings["profiles"]["series-high"]["model"]
    assert not Path(saved["runtime_root"]).exists()  # No runtime contents were cloned.


@pytest.mark.parametrize("field,value", [
    ("runtime_root", "runtime"), ("options.auth_source", "auth.json"), ("executable", "./bin/codex"),
])
def test_cwd_relative_paths_fail_without_reinterpretation(tmp_path, field, value):
    with pytest.raises(PipelineError, match="unknown working directory"):
        remap_path(value, field, tmp_path / "old-project", tmp_path / "old-source",
                   tmp_path / "new-project", tmp_path / "new-source")


@pytest.mark.parametrize("location", ["project", "source"])
def test_local_auth_is_not_read_or_copied(configuration_pool, tmp_path, capsys, location):
    previous = tmp_path / "v1"
    old_source = tmp_path / "source-1"
    old_source.mkdir()
    book = write_predecessor(previous)
    book["source_root"] = str(old_source)
    atomic_json(previous / "book.json", book)
    settings = tuned_configuration(previous)
    auth = (previous if location == "project" else old_source) / "auth.json"
    auth.write_text("SYNTHETIC-SECRET-NOT-TO-COPY", encoding="utf-8")
    settings["profiles"]["series-high"]["options"]["auth_source"] = str(auth)
    atomic_json(previous / "settings.json", settings)
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source-2")) == 1
    output = capsys.readouterr()
    assert "cannot be inherited automatically" in output.err
    assert "SYNTHETIC-SECRET" not in output.err + output.out
    assert configuration_pool == []
    assert not (root / "settings.json").exists() and not (root / "auth.json").exists()
    assert not (root / "book.json").exists()


@pytest.mark.parametrize("existing", ["settings.json", "prompts/pass1.txt", "catalog/models.json"])
def test_prepared_destination_configuration_fails_without_overwriting(configuration_pool, tmp_path, capsys, existing):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    root = tmp_path / "v2"
    target = root / existing
    target.parent.mkdir(parents=True)
    target.write_bytes(b"explicit new-volume configuration")
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 1
    assert "already contains" in capsys.readouterr().err
    assert target.read_bytes() == b"explicit new-volume configuration"
    assert configuration_pool == [] and not (root / "book.json").exists()


@pytest.mark.parametrize("problem", [
    "missing-settings", "invalid-json", "settings-array", "invalid-profile", "missing-profile",
    "invalid-catalog", "missing-prompt", "empty-prompt", "binary-prompt", "literal-secret", "legacy-secret",
])
def test_invalid_inherited_configuration_fails_before_discovery(configuration_pool, tmp_path, capsys, problem):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = read_json(previous / "settings.json")
    if problem == "missing-settings":
        (previous / "settings.json").unlink()
    elif problem == "invalid-json":
        (previous / "settings.json").write_bytes(b"{SYNTHETIC-SECRET")
    elif problem == "settings-array":
        atomic_json(previous / "settings.json", [])
    elif problem in {"invalid-profile", "missing-profile", "literal-secret", "legacy-secret"}:
        if problem == "invalid-profile":
            settings["profiles"]["local"]["provider"] = "invalid"
        elif problem == "missing-profile":
            settings["pass_profiles"]["1"] = "missing"
        elif problem == "literal-secret":
            settings["profiles"]["local"]["options"]["api_key"] = "SYNTHETIC-SECRET"
        else:
            settings["request_extra"]["Authorization"] = "Bearer SYNTHETIC-SECRET"
        atomic_json(previous / "settings.json", settings)
    elif problem == "invalid-catalog":
        atomic_json(previous / "catalog" / "models.json", {"schema_version": 999, "models": []})
    elif problem == "missing-prompt":
        (previous / "prompts" / "pass3.txt").unlink()
    elif problem == "empty-prompt":
        (previous / "prompts" / "pass3.txt").write_bytes(b" \n")
    else:
        (previous / "prompts" / "pass3.txt").write_bytes(b"\xff\xfe")
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 1
    output = capsys.readouterr()
    assert "ERROR:" in output.err and "SYNTHETIC-SECRET" not in output.err
    assert configuration_pool == []
    assert not (root / "book.json").exists() and not (root / "settings.json").exists()
    assert not (previous / "series.json").exists()


def test_legacy_settings_are_migrated_only_in_new_volume(configuration_pool, tmp_path):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    settings = read_json(previous / "settings.json")
    for field in ("profiles", "default_profile", "pass_profiles"):
        settings.pop(field)
    settings.update(format_version=1, model="legacy-model", memory_tokens=23456)
    atomic_json(previous / "settings.json", settings)
    before = (previous / "settings.json").read_bytes()
    root = tmp_path / "v2"
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 0
    saved = read_json(root / "settings.json")
    assert saved["format_version"] == 2 and saved["profiles"]["local"]["model"] == "legacy-model"
    assert saved["memory_tokens"] == 23456
    assert (previous / "settings.json").read_bytes() == before
    assert (previous / "state.sqlite3").read_bytes() == b"legacy database must not be opened"
    assert not (previous / "backups").exists()


@pytest.mark.parametrize("changed", [False, True])
def test_v3_inherits_current_v2_configuration_without_v1(configuration_pool, tmp_path, changed):
    v1, v2, v3 = (tmp_path / name for name in ("v1", "v2", "v3"))
    write_predecessor(v1)
    tuned_configuration(v1)
    assert config_import(v1, v2, source_folder(tmp_path / "source-2")) == 0
    memory = read_json(v2 / "book_memory.json")
    atomic_json(v2 / "lexicon.approved.json", {"terms": [
        {"id": t["id"], "source": t["source"], "aliases": t["aliases"], "polish": t["choice"]}
        for t in memory["terms"]
    ]})
    settings = read_json(v2 / "settings.json")
    if changed:
        settings["profiles"]["series-high"]["reasoning_effort"] = "low"
        settings["memory_tokens"] = 17000
        atomic_json(v2 / "settings.json", settings)
        (v2 / "prompts" / "pass2.txt").write_text("Tuned in volume 2.", encoding="utf-8")
    v1.rename(tmp_path / "offline-v1")
    assert config_import(v2, v3, source_folder(tmp_path / "source-3")) == 0
    assert read_json(v3 / "settings.json") == settings
    assert (v3 / "prompts" / "pass2.txt").read_bytes() == (v2 / "prompts" / "pass2.txt").read_bytes()


def test_local_executable_must_be_provisioned_and_is_never_copied(tmp_path):
    old, new, old_source, new_source = (tmp_path / name for name in ("old", "new", "src1", "src2"))
    tool = old / "bin" / "tool"
    tool.parent.mkdir(parents=True)
    tool.write_text("old executable", encoding="utf-8")
    target = new / "bin" / "tool"
    with pytest.raises(PipelineError, match="provision the executable"):
        remap_path(str(tool), "executable", old, old_source, new, new_source)
    assert not target.exists()
    target.parent.mkdir(parents=True)
    target.write_text("explicit new executable", encoding="utf-8")
    target.chmod(0o700)
    assert remap_path(str(tool), "executable", old, old_source, new, new_source) == str(target)
    assert target.read_text() == "explicit new executable"


def test_configuration_capture_holds_both_project_locks(configuration_pool, monkeypatch, tmp_path):
    previous, root = tmp_path / "v1", tmp_path / "v2"
    write_predecessor(previous)
    original = config_module._json
    captured = []

    def locked_read(path):
        for project in (previous, root):
            with pytest.raises(PipelineError, match="Another process"):
                with project_lock(project):
                    pytest.fail("Configuration read without project lock")
        captured.append(path.name)
        return original(path)

    monkeypatch.setattr(config_module, "_json", locked_read)
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 0
    assert "settings.json" in captured


def test_configuration_write_failure_does_not_complete_import(configuration_pool, monkeypatch, tmp_path):
    previous, root = tmp_path / "v1", tmp_path / "v2"
    write_predecessor(previous)

    def fail_write(*args):
        raise OSError("Synthetic write failure")

    monkeypatch.setattr(config_module, "atomic_text", fail_write)
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 1
    assert not (root / "book.json").exists()
    assert not (root / "state.sqlite3").exists()
    assert configuration_pool == []


def test_catalog_rejects_unsupported_inherited_effort(configuration_pool, tmp_path, capsys):
    previous = tmp_path / "v1"
    write_predecessor(previous)
    tuned_configuration(previous)
    atomic_json(previous / "catalog" / "models.json", {"schema_version": 1, "models": [{
        "provider": "codex", "id": "synthetic-series-model", "context_tokens": 190000, "efforts": ["low"],
    }]})
    assert config_import(previous, tmp_path / "v2", source_folder(tmp_path / "source")) == 1
    assert "effort 'high' is not listed" in capsys.readouterr().err
    assert configuration_pool == []


def test_remapping_rejects_symlink_back_into_predecessor(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (new / "runtime").symlink_to(old, target_is_directory=True)
    with pytest.raises(PipelineError, match="target escapes"):
        remap_path(str(old / "runtime"), "runtime_root", old, tmp_path / "src1", new, tmp_path / "src2")


def test_external_auth_symlink_to_local_secret_is_rejected(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    auth = old / "auth.json"
    auth.write_text("Synthetic credential", encoding="utf-8")
    shared = tmp_path / "shared-auth.json"
    shared.symlink_to(auth)
    with pytest.raises(PipelineError, match="cannot be inherited automatically"):
        remap_path(str(shared), "options.auth_source", old, tmp_path / "src1", new, tmp_path / "src2")


def test_catalog_provenance_does_not_retain_predecessor_path(configuration_pool, tmp_path):
    previous, root = tmp_path / "v1", tmp_path / "v2"
    write_predecessor(previous)
    catalog = {"schema_version": 1, "models": [], "import": {
        "source": str(previous / "original-catalog.json"), "sha256": "original-catalog-hash",
    }}
    atomic_json(previous / "catalog" / "models.json", catalog)
    assert config_import(previous, root, source_folder(tmp_path / "source")) == 0
    saved = read_json(root / "catalog" / "models.json")
    assert saved["models"] == catalog["models"]
    assert saved["import"] == {"sha256": "original-catalog-hash"}
    assert read_json(previous / "catalog" / "models.json") == catalog
