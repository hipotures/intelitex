from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

import bookpipe.reader as reader_module
import bookpipe.application.reader as reader_application
import bookpipe.cli as cli_module
from bookpipe.cli import main
from bookpipe.reader import MarkerConflict, MarkerRepository, ReaderServer
from bookpipe.reader_context import ReaderContext, parse_inline_formatting
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, project_lock, reader_lock, read_json


def make_reader_project(tmp_path, *, pieces=False, translated=2):
    root = tmp_path / "project"
    root.mkdir()
    canonical = [
        {"id": "B0000001", "kind": "p", "text": "English one.", "order": 1},
        {"id": "B0000002", "kind": "p", "text": "English two.", "order": 2},
    ]
    if pieces:
        chunks = [
            {"id": "ch0001_c0001", "chapter_id": "ch0001", "number": 1, "blocks": [
                {"id": "B0000001.a1", "parent_id": "B0000001", "text": "English "},
            ]},
            {"id": "ch0001_c0002", "chapter_id": "ch0001", "number": 2, "blocks": [
                {"id": "B0000001.a2", "parent_id": "B0000001", "text": "one."},
                {"id": "B0000002", "parent_id": "B0000002", "text": "English two."},
            ]},
        ]
        outputs = [
            [{"id": "B0000001.a1", "text": "Zażółć"}],
            [{"id": "B0000001.a2", "text": "gęślą"}, {"id": "B0000002", "text": "Jaźń 😀 płynie."}],
        ]
    else:
        chunks = [
            {"id": "ch0001_c0001", "chapter_id": "ch0001", "number": 1, "blocks": [
                {**canonical[0], "parent_id": "B0000001"},
            ]},
            {"id": "ch0001_c0002", "chapter_id": "ch0001", "number": 2, "blocks": [
                {**canonical[1], "parent_id": "B0000002"},
            ]},
        ]
        outputs = [
            [{"id": "B0000001", "text": "Zażółć 😀 gęślą jaźń."}],
            [{"id": "B0000002", "text": "Drugi polski akapit."}],
        ]
    book = {
        "source_fingerprint": "source-fingerprint",
        "metadata": {"title": "Książka"},
        "chapters": [{
            "id": "ch0001", "number": 1, "title": "Rozdział Łódź", "blocks": canonical,
            "chunk_ids": [chunk["id"] for chunk in chunks],
        }],
        "chunks": chunks,
    }
    atomic_json(root / "book.json", book)
    store = Store(root)
    store.register_chunks(book)
    paths = []
    for index, (chunk, output) in enumerate(zip(chunks, outputs), 1):
        if index > translated:
            break
        path = root / "artifacts" / "pass5" / chunk["id"] / "result.json"
        atomic_json(path, {"translations": output})
        store.save_job(f"pass5/{chunk['id']}", f"fingerprint-{index}", path, {})
        store.finish_chunk(chunk["id"], str(path.relative_to(root)), [], "lexical")
        paths.append(path)
    store.close()
    return root, paths


def test_reader_assembles_verified_p5_pieces_under_canonical_ids(tmp_path):
    root, _ = make_reader_project(tmp_path, pieces=True)
    chapter = ReaderContext(root).chapter("ch0001")
    assert chapter["complete"] is True
    assert chapter["stale"] is False
    assert chapter["blocks"] == [
        {"id": "B0000001", "kind": "p", "text": "Zażółć gęślą"},
        {"id": "B0000002", "kind": "p", "text": "Jaźń 😀 płynie."},
    ]


def test_reader_extracts_only_importer_inline_formatting_with_visible_offsets():
    text, formatting = parse_inline_formatting(
        "To *ważne* i **bardzo mocne** <img src=x> oraz *otwarte, 2 * 3 * 4 i ***zagnieżdżone***."
    )
    assert text == "To ważne i bardzo mocne <img src=x> oraz *otwarte, 2 * 3 * 4 i ***zagnieżdżone***."
    assert formatting == [
        {"start": 3, "end": 8, "style": "em"},
        {"start": 11, "end": 23, "style": "strong"},
    ]


def test_reader_exposes_only_verified_prefix_when_translation_is_incomplete(tmp_path):
    root, _ = make_reader_project(tmp_path, translated=1)
    chapter = ReaderContext(root).chapter("ch0001")
    assert chapter["complete"] is False
    assert [block["id"] for block in chapter["blocks"]] == ["B0000001"]
    assert "unavailable" in chapter


def test_reader_progress_counts_only_the_contiguous_available_prefix(tmp_path):
    root, _ = make_reader_project(tmp_path, translated=1)
    progress = ReaderContext(root).progress()
    assert progress == {
        "total_words": 3,
        "last_chapter": {"id": "ch0001", "title": "Rozdział Łódź"},
        "chapters": [{
            "id": "ch0001", "start": 0, "words": 3,
            "blocks": [{"id": "B0000001", "start": 0, "words": 3}],
        }],
    }


def make_context_project(tmp_path):
    root, paths = make_reader_project(tmp_path)
    atomic_json(paths[0], {"translations": [{
        "id": "B0000001",
        "text": "Yuri pojawił się wcześniej. Działu Bezpieczeństwa Pozasłonecznego Connexion używano wcześniej. Statek wrócił.",
    }]})
    atomic_json(paths[1], {"translations": [{
        "id": "B0000002",
        "text": "Yuri Alster, Callum Hepburn i Alik Monday. Dział Bezpieczeństwa Pozasłonecznego Connexion odpowiedział. Olyix przybył. Statek wrócił. Wspólna Nazwa milczała.",
    }]})
    book = read_json(root / "book.json")
    toc = {
        "id": "B0000000", "kind": "paragraph", "text": "Yuri — Contents.", "order": 0,
        "classes": ["toc_chap"], "file": "OEBPS/book_toc.xhtml",
    }
    toc_chunk = {
        "id": "ch0001_c0000", "chapter_id": "ch0001", "number": 0,
        "blocks": [{**toc, "parent_id": toc["id"]}],
    }
    future = {"id": "B0000003", "kind": "p", "text": "English future.", "order": 3}
    future_chunk = {
        "id": "ch0001_c0003", "chapter_id": "ch0001", "number": 3,
        "blocks": [{**future, "parent_id": future["id"]}],
    }
    book["chapters"][0]["blocks"].insert(0, toc)
    book["chapters"][0]["blocks"].append(future)
    book["chapters"][0]["chunk_ids"].insert(0, toc_chunk["id"])
    book["chapters"][0]["chunk_ids"].append(future_chunk["id"])
    book["chunks"].insert(0, toc_chunk)
    book["chunks"].append(future_chunk)
    atomic_json(root / "book.json", book)
    store = Store(root)
    toc_path = root / "artifacts" / "pass5" / toc_chunk["id"] / "result.json"
    atomic_json(toc_path, {"translations": [{"id": toc["id"], "text": "Yuri — Contents."}]})
    store.register_chunks(book)
    store.save_job("pass5/ch0001_c0000", "fingerprint-0", toc_path, {})
    store.finish_chunk(toc_chunk["id"], str(toc_path.relative_to(root)), [], "lexical")
    store.save_job("pass5/ch0001_c0001", "fingerprint-1", paths[0], {})
    store.save_job("pass5/ch0001_c0002", "fingerprint-2", paths[1], {})
    store.close()
    atomic_json(root / "lexicon.approved.json", {"terms": [
        {"id": "T000005", "source": "Yuri", "aliases": ["Yuri Alster", "Mr. Alster"], "polish": "Yuri"},
        {"id": "T000030", "source": "Connexion Exosolar Security Division",
         "aliases": ["Exosolar Security"], "polish": "Dział Bezpieczeństwa Pozasłonecznego Connexion"},
        {"id": "T000031", "source": "Extrasolar Security", "aliases": [],
         "polish": "Bezpieczeństwa Pozasłonecznego"},
        {"id": "T000049", "source": "Olyix", "aliases": [], "polish": "Olyix"},
        {"id": "T000040", "source": "Shared One", "aliases": [], "polish": "Wspólna Nazwa"},
        {"id": "T000041", "source": "Shared Two", "aliases": [], "polish": "Wspólna Nazwa"},
    ]})
    atomic_json(root / "book_memory.json", {
        "format_version": 1,
        "terms": [
            {
                "id": "T000005", "source": "Yuri", "aliases": ["Yuri Alster"],
                "category": "name", "choice": "Yuri", "approved": True,
                "evidence": [
                    {"block_id": "B0000000", "chapter_id": "ch0001", "order": 0},
                    {"block_id": "B0000001", "chapter_id": "ch0001", "order": 1},
                    {"block_id": "B0000002", "chapter_id": "ch0001", "order": 2},
                    {"block_id": "B0000003", "chapter_id": "ch0001", "order": 3},
                ],
                "meanings": [
                    {"text": "Contents-only garbage.", "confidence": "high", "evidence": ["B0000000"]},
                    {"text": "Known before the selection.", "confidence": "high", "evidence": ["B0000001"]},
                    {"text": "Learned in the current block.", "confidence": "high", "evidence": ["B0000002"]},
                    {"text": "Learned in the future.", "confidence": "high", "evidence": ["B0000003"]},
                    {"text": "Needs early and future evidence.", "confidence": "high", "evidence": ["B0000001", "B0000003"]},
                ],
                "candidates": [],
            },
            {
                "id": "T000030", "source": "Connexion Exosolar Security Division", "aliases": ["Exosolar Security"],
                "category": "organization", "choice": "Dział Bezpieczeństwa Pozasłonecznego Connexion",
                "approved": True,
                "evidence": [{"block_id": "B0000001", "chapter_id": "ch0001", "order": 1}],
                "candidates": [],
                "meanings": [{"text": "Known organization.", "confidence": "high", "evidence": ["B0000001"]}],
            },
            {
                "id": "T000031", "source": "Extrasolar Security", "aliases": [],
                "category": "organization", "choice": "Bezpieczeństwa Pozasłonecznego",
                "approved": True, "evidence": [], "candidates": [],
                "meanings": [{"text": "Short overlapping record.", "confidence": "high", "evidence": ["B0000001"]}],
            },
            {
                "id": "T000049", "source": "Olyix", "aliases": [], "category": "people",
                "choice": "Olyix", "approved": True,
                "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}],
                "candidates": [],
                "meanings": [{"text": "Current Olyix knowledge.", "confidence": "high", "evidence": ["B0000002"]}],
            },
            {
                "id": "T000040", "source": "Shared One", "aliases": [], "category": "name",
                "choice": "Wspólna Nazwa", "approved": True, "evidence": [], "candidates": [], "meanings": [],
            },
            {
                "id": "T000041", "source": "Shared Two", "aliases": [], "category": "name",
                "choice": "Wspólna Nazwa", "approved": True, "evidence": [], "candidates": [], "meanings": [],
            },
        ],
        "observations": [
            {"about": ["Yuri"], "kind": "reference", "statement": "Contents observation.",
             "confidence": "high", "evidence": ["B0000000"], "available_from_order": 0},
            {"about": ["Yuri"], "kind": "continuity", "statement": "Earlier observation.",
             "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
            {"about": ["Yuri"], "kind": "continuity", "statement": "Current observation.",
             "confidence": "high", "evidence": ["B0000002"], "available_from_order": 2},
            {"about": ["Yuri"], "kind": "continuity", "statement": "Future observation.",
             "confidence": "high", "evidence": ["B0000003"], "available_from_order": 3},
            {"about": ["Yuri"], "kind": "continuity", "statement": "Mislabelled future observation.",
             "confidence": "high", "evidence": ["B0000003"], "available_from_order": 1},
            {"about": ["Yuri Alster"], "kind": "reference", "statement": "Future alias relationship.",
             "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
        ],
    })
    return root


def test_context_helper_returns_only_complete_pre_cutoff_evidence(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    result = context.context("ch0001", "B0000002", text.index("Alster") + 2)
    assert result == {
        "recognized": True,
        "matched_text": "Yuri Alster",
        "display_name": "Yuri Alster",
        "title": "Yuri Alster",
        "attributes": [],
        "statements": ["Known before the selection.", "Earlier observation."],
        "earlier_mentions": [{
            "chapter_id": "ch0001", "chapter_title": "Rozdział Łódź",
            "block_id": "B0000001",
            "text": "Yuri pojawił się wcześniej. Działu Bezpieczeństwa Pozasłonecznego Connexion używano wcześniej. Statek wrócił.",
        }],
        "same_block_context": [],
        "range": {"start": 0, "end": 11},
    }
    assert not any("contents" in statement.lower() or "current" in statement.lower() or "future" in statement.lower()
                   for statement in result["statements"])


def test_context_helper_resolves_both_words_of_visible_alias_but_not_future_long_name(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    for word in ("Yuri", "Alster"):
        result = context.context("ch0001", "B0000002", text.index(word) + 1)
        assert result["recognized"] is True
        assert result["title"] == "Yuri Alster"
        assert result["range"] == {"start": 0, "end": 11}

    earlier = context.block_text("ch0001", "B0000001")
    result = context.context("ch0001", "B0000001", earlier.index("Yuri") + 1)
    assert result == {
        "recognized": True, "matched_text": "Yuri", "display_name": "Yuri", "title": "Yuri",
        "attributes": [], "statements": [], "earlier_mentions": [], "same_block_context": [],
        "range": {"start": 0, "end": 4},
    }


def test_context_helper_resolves_every_word_of_a_multiword_entity_and_prefers_longest(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    phrase = "Dział Bezpieczeństwa Pozasłonecznego Connexion"
    expected_range = {"start": text.index(phrase), "end": text.index(phrase) + len(phrase)}
    for word in phrase.split():
        result = context.context("ch0001", "B0000002", text.index(word, expected_range["start"]) + 1)
        assert result["title"] == phrase
        assert result["range"] == expected_range
        assert result["statements"] == ["Known organization."]
        assert "Short overlapping record." not in result["statements"]


def test_context_helper_recognizes_known_entities_without_prior_context(tmp_path):
    root = make_context_project(tmp_path)
    memory = read_json(root / "book_memory.json")
    organization = next(term for term in memory["terms"] if term["id"] == "T000030")
    organization["evidence"] = [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}]
    organization["meanings"][0]["evidence"] = ["B0000002"]
    atomic_json(root / "book_memory.json", memory)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    for term in ("Dział", "Olyix"):
        result = context.context("ch0001", "B0000002", text.index(term) + 1)
        assert result["recognized"] is True
        assert result["statements"] == []
        assert result["earlier_mentions"] == []


def test_context_helper_rejects_arbitrary_common_word_despite_earlier_occurrence(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    assert context.context("ch0001", "B0000002", text.index("Statek") + 2) == {"recognized": False}


def test_context_helper_rejects_ambiguous_identical_entity_span(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    assert context.context("ch0001", "B0000002", text.index("Wspólna") + 1) == {"recognized": False}


def test_context_helper_uses_evidence_mentions_despite_polish_inflection(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    text = context.block_text("ch0001", "B0000002")
    known = context.context("ch0001", "B0000002", text.index("Connexion"))
    assert [mention["block_id"] for mention in known["earlier_mentions"]] == ["B0000001"]
    assert "Działu Bezpieczeństwa" in known["earlier_mentions"][0]["text"]


def test_context_helper_rejects_invalid_position_and_never_calls_a_model(tmp_path, monkeypatch):
    from bookpipe.client import Client

    root = make_context_project(tmp_path)
    monkeypatch.setattr(Client, "generate", lambda *args, **kwargs: pytest.fail("Context Helper called a model"))
    context = ReaderContext(root)
    assert context.context("ch0001", "B0000002", 2)["recognized"] is True
    with pytest.raises(PipelineError, match="outside"):
        context.context("ch0001", "B0000002", 999)


def test_reader_startup_and_chapter_loading_do_not_match_entities(tmp_path, monkeypatch):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    monkeypatch.setattr(context, "_lexicon", lambda: pytest.fail("entity catalog loaded at startup"))
    context.metadata()
    context.progress()
    context.chapter("ch0001")
    assert context._lexicon_stamp is None
    assert context._memory_stamp is None


def make_inflected_context_project(tmp_path):
    root = make_context_project(tmp_path)
    text = (
        "Callum czekał. Callum Hepburn wszedł. Później Callum odpowiedział. "
        "Yuri czekał. Yuri Alster wszedł. Później Yuri odpowiedział. "
        "Eldlund weszło. Eldlund było omnią: łączyło płeć męską i żeńską w cyklu tysiąca dni. "
        "To wyjaśnienie było już przeczytane. Później Eldlund odpowiedziało. "
        "TAJNA INFORMACJA PO DOTKNIĘCIU. "
        "Biura Obserwacji Obcych Olyix pilnowano. W Biurze Obserwacji Obcych Olyix pracowała Jessika. "
        "Zwykłymi osobami nikt się nie zajmował. Nocny Wilk odszedł."
    )
    path = root / "artifacts" / "pass5" / "ch0001_c0002" / "result.json"
    atomic_json(path, {"translations": [{"id": "B0000002", "text": text}]})
    store = Store(root)
    store.save_job("pass5/ch0001_c0002", "fingerprint-2", path, {})
    store.close()

    lexicon = read_json(root / "lexicon.approved.json")
    lexicon["terms"].extend([
        {"id": "T000004", "source": "Callum", "aliases": ["Callum Hepburn"], "polish": "Callum"},
        {"id": "T000066", "source": "Eldlund", "aliases": [], "polish": "Eldlund"},
        {"id": "T000068", "source": "omnia", "aliases": ["omnias"], "polish": "omnia"},
        {"id": "T000072", "source": "Olyix Alien Observation Bureau", "aliases": [],
         "polish": "Biuro Obserwacji Obcych Olyix"},
        {"id": "T000090", "source": "ordinary person", "aliases": [], "polish": "osoba"},
        {"id": "T000091", "source": "Alpha", "aliases": ["Nocny Wilk"], "polish": "Alpha"},
    ])
    atomic_json(root / "lexicon.approved.json", lexicon)

    memory = read_json(root / "book_memory.json")
    memory["terms"].extend([
        {"id": "T000004", "source": "Callum", "aliases": ["Callum Hepburn"], "category": "name",
         "choice": "Callum", "approved": True,
         "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}],
         "meanings": [], "candidates": []},
        {"id": "T000066", "source": "Eldlund", "aliases": [], "category": "name",
         "choice": "Eldlund", "approved": True,
         "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}],
         "meanings": [], "candidates": []},
        {"id": "T000068", "source": "omnia", "aliases": ["omnias"], "category": "people",
         "choice": "omnia", "approved": True,
         "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}],
         "meanings": [], "candidates": []},
        {"id": "T000072", "source": "Olyix Alien Observation Bureau", "aliases": [],
         "category": "organization", "choice": "Biuro Obserwacji Obcych Olyix", "approved": True,
         "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2}],
         "meanings": [{"text": "Current-block organization detail.", "evidence": ["B0000002"]}],
         "candidates": []},
        {"id": "T000090", "source": "ordinary person", "aliases": [], "category": "other",
         "choice": "osoba", "approved": True,
         "evidence": [{"block_id": "B0000003", "chapter_id": "ch0001", "order": 3}],
         "meanings": [], "candidates": []},
        {"id": "T000091", "source": "Alpha", "aliases": ["Nocny Wilk"], "category": "name",
         "choice": "Alpha", "approved": True,
         "evidence": [{"block_id": "B0000002", "chapter_id": "ch0001", "order": 2,
                       "excerpt": "The cover identity appears here."}],
         "meanings": [], "candidates": []},
    ])
    memory["observations"].extend([
        {"about": ["Callum"], "kind": "gender", "statement": "Callum is explicitly male.",
         "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
        {"about": ["Callum"], "kind": "gender", "statement": "Future statement says female.",
         "confidence": "high", "evidence": ["B0000003"], "available_from_order": 3},
        {"about": ["Eldlund"], "kind": "gender",
         "statement": "Eldlund uses sie/hir; omnia biology follows a thousand-day gender cycle, not a fixed binary identity.",
         "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
    ])
    atomic_json(root / "book_memory.json", memory)
    return root, text


def test_context_helper_recognizes_conservative_polish_inflection(tmp_path):
    root, text = make_inflected_context_project(tmp_path)
    context = ReaderContext(root)
    omnia = context.context("ch0001", "B0000002", text.index("omnią") + 2)
    assert omnia["recognized"] is True
    assert omnia["matched_text"] == "omnią"
    assert omnia["display_name"] == "omnia"

    for phrase in ("Biura Obserwacji Obcych Olyix", "Biurze Obserwacji Obcych Olyix"):
        start = text.index(phrase)
        for word in phrase.split():
            result = context.context("ch0001", "B0000002", text.index(word, start) + 1)
            assert result["recognized"] is True
            assert result["matched_text"] == phrase
            assert result["display_name"] == "Biuro Obserwacji Obcych Olyix"
            assert result["statements"] == []
            if phrase.startswith("Biura"):
                assert result["attributes"] == []
                assert result["earlier_mentions"] == []
                assert result["same_block_context"] == []

    assert context.context("ch0001", "B0000002", text.index("osobami") + 2) == {"recognized": False}


def test_context_helper_uses_only_prior_same_block_sentences(tmp_path):
    root, text = make_inflected_context_project(tmp_path)
    context = ReaderContext(root)
    later = text.index("Eldlund", text.index("Eldlund") + 1)
    later = text.index("Eldlund", later + 1)
    result = context.context("ch0001", "B0000002", later + 2)
    local = result["same_block_context"]
    assert local and "omnią" in local[0]["text"] and "cyklu tysiąca dni" in local[0]["text"]
    assert "TAJNA INFORMACJA" not in local[0]["text"]
    assert "TAJNA INFORMACJA" not in str(result)

    first = text.index("Eldlund")
    first_result = context.context("ch0001", "B0000002", first + 2)
    assert first_result["recognized"] is True
    assert first_result["same_block_context"] == []


def test_context_helper_promotes_only_safely_seen_full_personal_names(tmp_path):
    root, text = make_inflected_context_project(tmp_path)
    context = ReaderContext(root)
    first_callum = text.index("Callum")
    later_callum = text.index("Callum", text.index("Callum Hepburn") + len("Callum Hepburn"))
    assert context.context("ch0001", "B0000002", first_callum + 1)["display_name"] == "Callum"
    assert context.context("ch0001", "B0000002", later_callum + 1)["display_name"] == "Callum Hepburn"

    first_yuri = text.index("Yuri")
    later_yuri = text.index("Yuri", text.index("Yuri Alster") + len("Yuri Alster"))
    assert context.context("ch0001", "B0000002", first_yuri + 1)["display_name"] == "Yuri"
    assert context.context("ch0001", "B0000002", later_yuri + 1)["display_name"] == "Yuri Alster"
    assert context.context("ch0001", "B0000002", text.index("Nocny Wilk") + 2) == {"recognized": False}


def test_context_helper_structures_only_safe_gender_knowledge(tmp_path):
    root, text = make_inflected_context_project(tmp_path)
    context = ReaderContext(root)
    callum = context.context("ch0001", "B0000002", text.index("Callum") + 1)
    assert callum["attributes"] == [{"label": "Gender", "value": "male"}]
    assert not any("Future" in value for attribute in callum["attributes"] for value in attribute.values())

    eldlund = context.context("ch0001", "B0000002", text.index("Eldlund") + 1)
    assert eldlund["attributes"][0]["label"] == "Gender system"
    assert "gender cycle" in eldlund["attributes"][0]["value"]
    assert eldlund["attributes"][0]["value"] not in {"male", "female"}


def test_reader_labels_checkpointed_stale_translation(tmp_path):
    root, _ = make_reader_project(tmp_path)
    store = Store(root)
    with store.db:
        store.db.execute("UPDATE chunks SET status='stale' WHERE id='ch0001_c0001'")
    store.close()
    chapter = ReaderContext(root).chapter("ch0001")
    assert chapter["stale"] is True
    assert chapter["complete"] is False
    assert "stale translation units" in chapter["warning"]


@pytest.mark.parametrize("failure", ["corrupt", "missing"])
def test_reader_rejects_corrupt_or_missing_registered_final(tmp_path, failure):
    root, paths = make_reader_project(tmp_path)
    if failure == "corrupt":
        atomic_json(paths[0], {"translations": [{"id": "B0000001", "text": "Changed"}]})
    else:
        paths[0].unlink()
    with pytest.raises(PipelineError, match="checksum|missing"):
        ReaderContext(root).chapter("ch0001")


def test_marker_repository_unicode_validation_idempotency_and_atomic_write(tmp_path, monkeypatch):
    root, _ = make_reader_project(tmp_path)
    repository = MarkerRepository(root)
    initial = repository.load()
    text = "Zażółć 😀 gęślą jaźń."
    start = text.index("😀")
    end = text.index(" jaźń")
    calls = []
    real_atomic_json = reader_application.atomic_json

    def recording_atomic(path, value):
        calls.append(path)
        real_atomic_json(path, value)

    monkeypatch.setattr(reader_application, "atomic_json", recording_atomic)
    payload = {"chapter_id": "ch0001", "block_id": "B0000001", "start": start, "end": end, "text": text[start:end]}
    created = repository.create(payload, initial["_revision"])
    duplicate = repository.create(payload, created["revision"])
    assert created["created"] is True and duplicate["created"] is False
    assert created["marker"]["text"] == "😀 gęślą"
    assert calls == [root / "translation.review.json"]
    saved = read_json(root / "translation.review.json")
    assert saved["book_fingerprint"] == "source-fingerprint"
    assert saved["markers"] == [created["marker"]]


@pytest.mark.parametrize("patch, message", [
    ({"start": True}, "integer"),
    ({"end": 999}, "outside"),
    ({"text": "wrong"}, "no longer matches"),
    ({"chapter_id": "unknown"}, "Unknown chapter"),
    ({"block_id": "unknown"}, "Unknown or unavailable"),
])
def test_marker_repository_rejects_invalid_locations(tmp_path, patch, message):
    root, _ = make_reader_project(tmp_path)
    repository = MarkerRepository(root)
    state = repository.load()
    payload = {"chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6, "text": "Zażółć"}
    payload.update(patch)
    with pytest.raises(PipelineError, match=message):
        repository.create(payload, state["_revision"])


def test_marker_fingerprint_revision_and_targeted_deletion(tmp_path):
    root, _ = make_reader_project(tmp_path)
    repository = MarkerRepository(root)
    state = repository.load()
    one = repository.create(
        {"chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6, "text": "Zażółć"},
        state["_revision"],
    )
    with pytest.raises(MarkerConflict, match="another tab"):
        repository.create(
            {"chapter_id": "ch0001", "block_id": "B0000002", "start": 0, "end": 5, "text": "Drugi"},
            state["_revision"],
        )
    two = repository.create(
        {"chapter_id": "ch0001", "block_id": "B0000002", "start": 0, "end": 5, "text": "Drugi"},
        one["revision"],
    )
    deleted = repository.delete(one["marker"]["id"], two["revision"])
    loaded = repository.load()
    assert deleted["deleted"] == one["marker"]["id"]
    assert [marker["id"] for marker in loaded["markers"]] == [two["marker"]["id"]]
    with pytest.raises(PipelineError, match="Unknown marker ID"):
        repository.delete("M999999", loaded["_revision"])
    saved = read_json(root / "translation.review.json")
    saved["book_fingerprint"] = "other-book"
    atomic_json(root / "translation.review.json", saved)
    with pytest.raises(PipelineError, match="different book fingerprint"):
        repository.load()


def test_outdated_marker_does_not_block_reader_and_can_be_deleted(tmp_path):
    root, _ = make_reader_project(tmp_path)
    repository = MarkerRepository(root)
    state = repository.load()
    current = repository.create(
        {"chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6, "text": "Zażółć"},
        state["_revision"],
    )
    other = repository.create(
        {"chapter_id": "ch0001", "block_id": "B0000002", "start": 0, "end": 5, "text": "Drugi"},
        current["revision"],
    )

    store = Store(root)
    chunk = read_json(root / "book.json")["chunks"][0]
    replacement = root / "artifacts" / "pass5" / chunk["id"] / "replacement" / "result.json"
    atomic_json(replacement, {"translations": [{"id": "B0000001", "text": "Poprawiony polski akapit."}]})
    store.save_job("pass5/replacement", "replacement-fingerprint", replacement, {})
    store.finish_chunk(chunk["id"], str(replacement.relative_to(root)), [], "new-lexical")
    store.close()

    assert ReaderContext(root).chapter("ch0001")["blocks"][0]["text"].startswith("Poprawiony")
    loaded = repository.load()
    assert [marker["id"] for marker in loaded["markers"]] == [current["marker"]["id"], other["marker"]["id"]]
    server = ReaderServer(("127.0.0.1", 0), repository)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", timeout=5) as client:
            assert client.get("/api/reader").status_code == 200
            assert client.get("/api/chapters/ch0001").json()["blocks"][0]["text"].startswith("Poprawiony")
            deleted = client.request(
                "DELETE", f"/api/markers/{current['marker']['id']}", json={"revision": loaded["_revision"]},
            )
            assert deleted.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert [marker["id"] for marker in repository.load()["markers"]] == [other["marker"]["id"]]


def test_marker_mutation_does_not_resolve_every_historical_anchor(tmp_path):
    root, _ = make_reader_project(tmp_path)

    class CountingContext:
        def __init__(self):
            self.metadata_calls = 0
            self.block_text_calls = 0
            self.structure_calls = 0

        def metadata(self):
            self.metadata_calls += 1
            return {"book_fingerprint": "source-fingerprint"}

        def has_canonical_block(self, chapter_id, block_id):
            self.structure_calls += 1
            return chapter_id == "ch0001" and block_id == "B0000001"

        def block_text(self, chapter_id, block_id):
            self.block_text_calls += 1
            return "Zażółć 😀 gęślą jaźń."

    atomic_json(root / "translation.review.json", {
        "format_version": 1,
        "book_fingerprint": "source-fingerprint",
        "markers": [
            {"id": f"M{index:06d}", "chapter_id": "ch0001", "block_id": "B0000001",
             "start": 0, "end": 6, "text": "historical text may be outdated"}
            for index in range(1, 101)
        ],
    })
    context = CountingContext()
    repository = MarkerRepository(root, context=context)
    revision = repository.load()["_revision"]
    context.metadata_calls = context.block_text_calls = context.structure_calls = 0
    repository.create(
        {"chapter_id": "ch0001", "block_id": "B0000001", "start": 7, "end": 8, "text": "😀"},
        revision,
    )
    assert context.metadata_calls == 1
    assert context.block_text_calls == 1
    assert context.structure_calls == 100


def test_reader_context_reuses_frozen_book_manifest(tmp_path, monkeypatch):
    root, _ = make_reader_project(tmp_path)
    import bookpipe.reader_context as context_module
    real_read_json = context_module.read_json
    calls = []

    def recording_read_json(path):
        calls.append(path)
        return real_read_json(path)

    monkeypatch.setattr(context_module, "read_json", recording_read_json)
    context = ReaderContext(root)
    context.metadata()
    context.metadata()
    context.chapter("ch0001")
    assert calls == [root / "book.json"]


def test_reader_can_read_checkpoint_while_pipeline_sqlite_writer_is_active(tmp_path):
    root, _ = make_reader_project(tmp_path)
    writer = Store(root)
    try:
        writer.db.execute("BEGIN IMMEDIATE")
        writer.db.execute("UPDATE chunks SET status='stale' WHERE id='ch0001_c0001'")
        chapter = ReaderContext(root).chapter("ch0001")
        assert chapter["blocks"][0]["text"] == "Zażółć 😀 gęślą jaźń."
        assert chapter["stale"] is False  # The Reader sees the last committed WAL snapshot.
        writer.db.rollback()
    finally:
        writer.close()


def test_reader_http_api_round_trip_and_no_arbitrary_file_access(tmp_path):
    root, _ = make_reader_project(tmp_path)
    repository = MarkerRepository(root)
    context_service = ReaderContext(root)

    class PublicReaderSession:
        path = repository.path

        def __init__(self):
            self.calls = []

        def load(self):
            self.calls.append("load")
            return repository.load()

        def metadata(self):
            self.calls.append("metadata")
            return context_service.metadata()

        def progress(self):
            self.calls.append("progress")
            return context_service.progress()

        def chapter(self, chapter_id):
            self.calls.append("chapter")
            return context_service.chapter(chapter_id)

        def context(self, chapter_id, block_id, position):
            self.calls.append("context")
            return context_service.context(chapter_id, block_id, position)

        def create_marker(self, payload, revision):
            self.calls.append("create_marker")
            return repository.create(payload, revision)

        def delete_marker(self, marker_id, revision):
            self.calls.append("delete_marker")
            return repository.delete(marker_id, revision)

    session = PublicReaderSession()
    server = ReaderServer(("127.0.0.1", 0), session)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(base_url=base, timeout=5) as client:
            index = client.get("/")
            assert index.status_code == 200
            assert "Intelitex Reader" in index.text
            assert 'charset="utf-8"' in index.text
            assert client.get("/assets/reader.js").status_code == 200
            assert client.get("/assets/reader.css").status_code == 200
            metadata = client.get("/api/reader").json()
            assert metadata["progress"]["total_words"] == 6
            assert metadata["progress"]["last_chapter"]["id"] == "ch0001"
            chapter = client.get("/api/chapters/ch0001")
            assert chapter.status_code == 200
            assert chapter.json()["blocks"][0]["id"] == "B0000001"
            context = client.post("/api/context", json={
                "chapter_id": "ch0001", "block_id": "B0000001", "position": 2,
            })
            assert context.status_code == 200 and context.json() == {"recognized": False}
            invalid_context = client.post("/api/context", json={
                "chapter_id": "ch0001", "block_id": "B0000001", "position": 999,
            })
            assert invalid_context.status_code == 400 and "outside" in invalid_context.json()["error"]
            payload = {
                "chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6,
                "text": "Zażółć", "revision": metadata["marker_state"]["_revision"],
            }
            created = client.post("/api/markers", json=payload)
            assert created.status_code == 201
            bad = client.post("/api/markers", json={**payload, "revision": created.json()["revision"], "end": 999})
            assert bad.status_code == 400 and "outside" in bad.json()["error"]
            traversal = client.get("/api/chapters/%2E%2E%2Fbook.json")
            assert traversal.status_code in {400, 404}
            arbitrary = client.get("/assets/../book.json")
            assert arbitrary.status_code == 404
            removed = client.request(
                "DELETE", f"/api/markers/{created.json()['marker']['id']}",
                json={"revision": created.json()["revision"]},
            )
            assert removed.status_code == 200
            assert read_json(root / "translation.review.json")["markers"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert {"load", "metadata", "progress", "chapter", "context",
            "create_marker", "delete_marker"} <= set(session.calls)


def test_reader_javascript_range_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is optional; install it to run the pure Reader range tests.")
    path = Path(__file__).with_name("test_reader_ranges.cjs")
    result = subprocess.run([node, "--test", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reader_cli_dispatches_with_reader_options(tmp_path, monkeypatch):
    root, _ = make_reader_project(tmp_path)
    book = read_json(root / "book.json")
    book["content_fingerprint"] = "test-plan"
    atomic_json(root / "book.json", book)
    monkeypatch.setattr(cli_module, "plan_fingerprint", lambda value: "test-plan")
    called = {}

    def fake_server(session, bind, port, open_browser, ui):
        progress = session.progress()
        called.update(
            project=session.project, bind=bind, port=port, open_browser=open_browser,
            total_words=progress["total_words"],
        )

    monkeypatch.setattr(cli_module, "run_reader_server", fake_server)
    monkeypatch.setattr(cli_module, "Store", lambda project: pytest.fail("Reader must not open the read-write Store"))
    # Simulate an already-running translate process holding the normal project lock.
    with project_lock(root):
        assert main([
            "reader", "--project", str(root), "--bind", "0.0.0.0", "--reader-port", "0", "--no-browser", "--quiet",
        ]) == 0
    assert called == {
        "project": root.resolve(), "bind": "0.0.0.0", "port": 0, "open_browser": False,
        "total_words": 6,
    }


def test_reader_lock_allows_pipeline_lock_but_rejects_second_reader(tmp_path):
    root = tmp_path / "project"
    with project_lock(root):
        with reader_lock(root):
            with pytest.raises(PipelineError, match="Another Reader"):
                with reader_lock(root):
                    pass
