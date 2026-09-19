from __future__ import annotations

import json
import threading

import httpx
import pytest

from bookpipe.review import ReviewRepository, ReviewServer, ensure_review_state, review_summary
from bookpipe.util import PipelineError, atomic_json, read_json


def sample_review():
    return {
        "format_version": 1,
        "book_fingerprint": "book",
        "analysis_revision": "analysis",
        "confirmed": False,
        "terms": [
            {
                "id": "T000001",
                "source": "The Cat",
                "aliases": [],
                "category": "people",
                "meaning_notes": [{"text": "A person.", "confidence": "high", "evidence": ["B1"]}],
                "candidates": [
                    {"number": 1, "text": "Kot", "confidence": "high", "reasons": ["Literal"], "evidence": ["B1"]},
                ],
                "select": 1,
                "custom": "",
                "evidence": [{"block_id": "B1", "chapter_id": "ch1", "excerpt": "The Cat smiled.", "order": 1}],
            },
            {
                "id": "T000002",
                "source": "Void",
                "aliases": [],
                "category": "place",
                "meaning_notes": [{"text": "A region.", "confidence": "medium", "evidence": ["B2"]}],
                "candidates": [
                    {"number": 1, "text": "Pustka", "confidence": "medium", "reasons": ["Standard"], "evidence": ["B2"]},
                ],
                "select": 1,
                "custom": "",
                "evidence": [{"block_id": "B2", "chapter_id": "ch1", "excerpt": "The Void shifted.", "order": 2}],
            },
        ],
    }


def test_review_state_migration_and_summary(tmp_path):
    path = tmp_path / "terms.review.json"
    atomic_json(path, sample_review())
    review = ensure_review_state(path)
    assert all(t["reviewed"] is False for t in review["terms"])
    summary = review_summary(review)
    assert summary == {
        "total": 2,
        "reviewed": 0,
        "unreviewed": 2,
        "uncertain": 1,
        "noted": 0,
        "categories": {"people": 1, "place": 1},
        "confirmed": False,
    }


def test_patch_choice_reopens_review_and_saves_custom(tmp_path):
    path = tmp_path / "terms.review.json"
    review = sample_review()
    for term in review["terms"]:
        term["reviewed"] = True
    review["confirmed"] = True
    atomic_json(path, review)
    repo = ReviewRepository(path)
    result = repo.patch_term("T000001", {"custom": "Kotka"})
    assert result["term"]["custom"] == "Kotka"
    assert result["term"]["reviewed"] is False
    saved = read_json(path)
    assert saved["confirmed"] is False


def test_confirm_requires_all_terms_reviewed(tmp_path):
    path = tmp_path / "terms.review.json"
    atomic_json(path, sample_review())
    repo = ReviewRepository(path)
    repo.load()
    with pytest.raises(PipelineError, match="still unreviewed"):
        repo.set_confirmed(True)
    repo.patch_term("T000001", {"reviewed": True})
    repo.patch_term("T000002", {"reviewed": True})
    result = repo.set_confirmed(True)
    assert result["summary"]["confirmed"] is True
    assert read_json(path)["confirmed"] is True



def test_review_state_hydrates_pass1_observations_and_notes(tmp_path):
    path = tmp_path / "terms.review.json"
    atomic_json(path, sample_review())
    atomic_json(tmp_path / "book_memory.json", {
        "format_version": 1,
        "terms": [],
        "observations": [
            {
                "about": ["The Cat"],
                "kind": "gender",
                "statement": "The Cat is female.",
                "confidence": "high",
                "evidence": ["B1"],
            }
        ],
    })
    review = ensure_review_state(path)
    cat = review["terms"][0]
    assert cat["user_notes"] == ""
    assert cat["observations"][0]["kind"] == "gender"
    assert cat["observations"][0]["statement"] == "The Cat is female."


def test_notes_do_not_reopen_review_or_change_reviewed_state(tmp_path):
    path = tmp_path / "terms.review.json"
    review = sample_review()
    for term in review["terms"]:
        term["reviewed"] = True
    review["confirmed"] = True
    atomic_json(path, review)
    repo = ReviewRepository(path)
    result = repo.patch_term("T000001", {"user_notes": "Use canonical published form: Kotka."})
    assert result["term"]["user_notes"].startswith("Use canonical")
    assert result["term"]["reviewed"] is True
    assert read_json(path)["confirmed"] is True
    assert result["summary"]["noted"] == 1

def test_review_http_api_round_trip(tmp_path):
    path = tmp_path / "terms.review.json"
    atomic_json(path, sample_review())
    server = ReviewServer(("127.0.0.1", 0), ReviewRepository(path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with httpx.Client(base_url=base, timeout=5) as client:
            index = client.get("/")
            assert index.status_code == 200
            assert "Terminology Review" in index.text
            assert "Reviewer notes" in index.text
            assert "At a glance" in index.text
            data = client.get("/api/review").json()
            assert data["terms"][0]["reviewed"] is False
            changed = client.patch("/api/terms/T000001", json={"custom": "Kotka", "reviewed": True})
            assert changed.status_code == 200
            assert changed.json()["term"]["custom"] == "Kotka"
            assert changed.json()["term"]["reviewed"] is True
            bad = client.post("/api/confirm", json={"confirmed": True})
            assert bad.status_code == 400
            client.patch("/api/terms/T000002", json={"reviewed": True})
            ok = client.post("/api/confirm", json={"confirmed": True})
            assert ok.status_code == 200
            assert ok.json()["summary"]["confirmed"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
