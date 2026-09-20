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
from bookpipe.reader_context import ReaderContext
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, read_json


def make_reader_project(tmp_path, *, pieces=False, translated=2):
    root = tmp_path / "project"
    root.mkdir()
    canonical = [
        {"id": "B0000001", "kind": "p", "text": "English one."},
        {"id": "B0000002", "kind": "p", "text": "English two."},
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


def test_reader_exposes_only_verified_prefix_when_translation_is_incomplete(tmp_path):
    root, _ = make_reader_project(tmp_path, translated=1)
    chapter = ReaderContext(root).chapter("ch0001")
    assert chapter["complete"] is False
    assert [block["id"] for block in chapter["blocks"]] == ["B0000001"]
    assert "unavailable" in chapter


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
            chapter = client.get("/api/chapters/ch0001")
            assert chapter.status_code == 200
            assert chapter.json()["blocks"][0]["id"] == "B0000001"
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
    assert main([
        "reader", "--project", str(root), "--bind", "0.0.0.0", "--reader-port", "0", "--no-browser", "--quiet",
    ]) == 0
    assert called == {"project": root.resolve(), "bind": "0.0.0.0", "port": 0, "open_browser": False}
