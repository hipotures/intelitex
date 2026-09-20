from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

import bookpipe.reader as reader_module
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
    atomic_json(paths[0], {"translations": [{"id": "B0000001", "text": "Drugi pojawił się wcześniej."}]})
    book = read_json(root / "book.json")
    future = {"id": "B0000003", "kind": "p", "text": "English future.", "order": 3}
    future_chunk = {
        "id": "ch0001_c0003", "chapter_id": "ch0001", "number": 3,
        "blocks": [{**future, "parent_id": future["id"]}],
    }
    book["chapters"][0]["blocks"].append(future)
    book["chapters"][0]["chunk_ids"].append(future_chunk["id"])
    book["chunks"].append(future_chunk)
    atomic_json(root / "book.json", book)
    store = Store(root)
    store.save_job("pass5/ch0001_c0001", "fingerprint-1", paths[0], {})
    store.register_chunks(book)
    store.close()
    atomic_json(root / "book_memory.json", {
        "format_version": 1,
        "terms": [{
            "id": "T000001", "source": "Second", "aliases": ["Later identity"],
            "category": "other", "choice": "Drugi", "approved": True,
            "evidence": [
                {"block_id": "B0000001", "chapter_id": "ch0001", "order": 1},
                {"block_id": "B0000002", "chapter_id": "ch0001", "order": 2},
                {"block_id": "B0000003", "chapter_id": "ch0001", "order": 3},
            ],
            "meanings": [
                {"text": "Known before the selection.", "confidence": "high", "evidence": ["B0000001"]},
                {"text": "Learned in the current block.", "confidence": "high", "evidence": ["B0000002"]},
                {"text": "Learned in the future.", "confidence": "high", "evidence": ["B0000003"]},
                {"text": "Needs early and future evidence.", "confidence": "high", "evidence": ["B0000001", "B0000003"]},
            ],
            "candidates": [],
        }],
        "observations": [
            {"about": ["Second"], "kind": "continuity", "statement": "Earlier observation.",
             "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
            {"about": ["Second"], "kind": "continuity", "statement": "Current observation.",
             "confidence": "high", "evidence": ["B0000002"], "available_from_order": 2},
            {"about": ["Second"], "kind": "continuity", "statement": "Future observation.",
             "confidence": "high", "evidence": ["B0000003"], "available_from_order": 3},
            {"about": ["Second"], "kind": "continuity", "statement": "Mislabelled future observation.",
             "confidence": "high", "evidence": ["B0000003"], "available_from_order": 1},
            {"about": ["Later identity"], "kind": "reference", "statement": "Future alias relationship.",
             "confidence": "high", "evidence": ["B0000001"], "available_from_order": 1},
        ],
    })
    return root


def test_context_helper_returns_only_complete_pre_cutoff_evidence(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    context.progress()
    result = context.context("ch0001", "B0000002", 0, 5, "Drugi")
    assert result == {
        "available": True,
        "title": "Drugi",
        "statements": ["Known before the selection.", "Earlier observation."],
        "earlier_mentions": [{
            "chapter_id": "ch0001", "chapter_title": "Rozdział Łódź",
            "block_id": "B0000001", "text": "Drugi pojawił się wcześniej.",
        }],
    }
    assert not any("current" in statement.lower() or "future" in statement.lower()
                   for statement in result["statements"])


def test_context_helper_returns_unavailable_without_earlier_safe_knowledge(tmp_path):
    root = make_context_project(tmp_path)
    context = ReaderContext(root)
    context.progress()
    assert context.context("ch0001", "B0000002", 6, 12, "polski") == {"available": False}


def test_context_helper_rejects_stale_selection_and_never_calls_a_model(tmp_path, monkeypatch):
    from bookpipe.client import Client

    root = make_context_project(tmp_path)
    monkeypatch.setattr(Client, "generate", lambda *args, **kwargs: pytest.fail("Context Helper called a model"))
    context = ReaderContext(root)
    assert context.context("ch0001", "B0000002", 0, 5, "Drugi")["available"] is True
    with pytest.raises(PipelineError, match="no longer matches"):
        context.context("ch0001", "B0000002", 0, 5, "Inny")


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
    real_atomic_json = reader_module.atomic_json

    def recording_atomic(path, value):
        calls.append(path)
        real_atomic_json(path, value)

    monkeypatch.setattr(reader_module, "atomic_json", recording_atomic)
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
    server = ReaderServer(("127.0.0.1", 0), MarkerRepository(root))
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
                "chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6, "text": "Zażółć",
            })
            assert context.status_code == 200 and context.json() == {"available": False}
            stale_context = client.post("/api/context", json={
                "chapter_id": "ch0001", "block_id": "B0000001", "start": 0, "end": 6, "text": "Changed",
            })
            assert stale_context.status_code == 400 and "no longer matches" in stale_context.json()["error"]
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

    def fake_server(project, bind, port, open_browser, ui):
        called.update(project=project, bind=bind, port=port, open_browser=open_browser)

    monkeypatch.setattr(cli_module, "run_reader_server", fake_server)
    monkeypatch.setattr(cli_module, "Store", lambda project: pytest.fail("Reader must not open the read-write Store"))
    # Simulate an already-running translate process holding the normal project lock.
    with project_lock(root):
        assert main([
            "reader", "--project", str(root), "--bind", "0.0.0.0", "--reader-port", "0", "--no-browser", "--quiet",
        ]) == 0
    assert called == {"project": root.resolve(), "bind": "0.0.0.0", "port": 0, "open_browser": False}


def test_reader_lock_allows_pipeline_lock_but_rejects_second_reader(tmp_path):
    root = tmp_path / "project"
    with project_lock(root):
        with reader_lock(root):
            with pytest.raises(PipelineError, match="Another Reader"):
                with reader_lock(root):
                    pass
