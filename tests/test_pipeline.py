from __future__ import annotations

import copy
import json
import re
import threading
import zipfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bookpipe.cli import main
from bookpipe.application import (
    AnalyzeCommand, ApproveCommand, ExportCommand, ReaderSessionCommand, ReviewSessionCommand,
    PublicationStatusCommand, PublishCommand, StatusCommand, TranslateCommand,
)
from bookpipe.bootstrap import create_application
from bookpipe.client import Client
from bookpipe.engine import (Runner, _vary_llamacpp_sampling, analysis_plan, conservative_repair,
                             response_schema, source_blocks, validate_result)
from bookpipe.importer import extract_blocks, import_folder, pack_blocks, reading_order, split_long
from bookpipe.infrastructure.epub_publisher import EpubPublicationBuilder
from bookpipe.schemas import SCHEMAS
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, atomic_text, digest, dumps, read_json


class MockState:
    def __init__(self):
        self.calls = Counter()
        self.requests = []
        self.fail_once = None
        self.bad_json_once = None
        self.unknown_evidence_once = False


@pytest.fixture
def server():
    state = MockState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send_json(self, value, status=200):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/v1/models":
                self.send_json({"data": [{"id": "test-model", "meta": {"n_vocab": 1000}}]})
            elif self.path == "/props":
                self.send_json({"default_generation_settings": {"n_ctx": 65536}, "model_path": "test-model.gguf"})
            else:
                self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tokenize":
                self.send_json({"tokens": list(range(max(1, len(body["content"]) // 4)))})
                return
            if self.path == "/v1/chat/completions/input_tokens":
                self.send_json({"input_tokens": len(json.dumps(body["messages"])) // 4})
                return
            if self.path == "/apply-template":
                self.send_json({"prompt": str(body["messages"])})
                return
            if self.path != "/v1/chat/completions":
                self.send_json({"error": "not found"}, 404)
                return
            prompt = body["messages"][0]["content"]
            stage = int(re.search(r"PASS ([1-5])", prompt).group(1))
            inputs = json.loads(body["messages"][1]["content"])
            state.calls[stage] += 1
            state.requests.append((stage, inputs, body))
            if stage == 1:
                blocks = inputs["SOURCE_BLOCKS"]
                evidence = [b["id"] for b in blocks if "Relay" in b["text"]]
                if state.unknown_evidence_once:
                    evidence = ["FAKE_ID"]
                    state.unknown_evidence_once = False
                output = {"terms": [{"source": "Relay", "aliases": [], "category": "technology",
                          "meaning": "A fictional relay system.", "confidence": "medium",
                          "candidates": [{"text": "Relay-A", "reason": "First candidate"},
                                         {"text": "Relay-B", "reason": "Alternative label"}],
                          "evidence": evidence}] if evidence else [], "observations": []}
            elif stage == 2:
                output = {"checks": [{"sid": s["id"], "risk": "low"} for s in inputs["SOURCE_SENTENCES"]], "issues": []}
            elif stage == 3:
                choices = {t["source"]: t["polish"] for t in inputs["APPROVED_LEXICON"]}
                output = {"translations": []}
                for block in inputs["SOURCE_BLOCKS"]:
                    text = block["text"]
                    for key, val in choices.items():
                        text = text.replace(key, val)
                    output["translations"].append({"id": block["id"], "text": "Translated: " + text})
            elif stage == 4:
                output = {"checks": [{"sid": s["id"], "status": "ok"} for s in inputs["SOURCE_SENTENCES"]], "corrections": []}
            else:
                output = inputs["POLISH_DRAFT"]
            raw = json.dumps(output)
            if state.bad_json_once == stage:
                state.bad_json_once = None
                raw = '{"incomplete":'
            finish = "stop"
            if state.fail_once == stage:
                state.fail_once = None
                finish = "length"
                raw = raw[:20]
            events = []
            # Multiple tiny events exercise streaming, not just a single JSON blob.
            for offset in range(0, len(raw), 80):
                events.append({"id": "mock-id", "choices": [{"index": 0, "delta": {"content": raw[offset:offset+80]}, "finish_reason": None}]})
            events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                           "usage": {"completion_tokens": len(raw)//4, "prompt_tokens": 20}})
            wire = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
            data = wire.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, httpd.server_port
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.fixture
def project(tmp_path, server):
    state, port = server
    source = tmp_path / "source"
    source.mkdir()
    # Short boundaries make at least four chunks while remaining valid prose.
    for index in (1, 2):
        paras = "".join(f"<p>Relay moved steadily. Sentence {n} was reported clearly. The crew waited for a response.</p>" for n in range(8))
        (source / f"section_{index}.html").write_text(f"<html><body><h1>Chapter {index}</h1>{paras}</body></html>")
    root = tmp_path / "project"
    args = ["--project", str(root), "--quiet"]
    assert main(["import", str(source), *args, "--host", "127.0.0.1", "--port", str(port)]) == 0
    return root, args, state


def make_epub_source(tmp_path: Path) -> Path:
    source = tmp_path / "epub-source"
    (source / "META-INF").mkdir(parents=True)
    (source / "EPUB" / "text").mkdir(parents=True)
    (source / "EPUB" / "styles").mkdir(parents=True)
    (source / "EPUB" / "images").mkdir(parents=True)
    (source / "mimetype").write_bytes(b"application/epub+zip")
    (source / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>'
        '</rootfiles></container>', encoding="utf-8",
    )
    (source / "EPUB" / "package.opf").write_text(
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:test:relay</dc:identifier>'
        '<dc:title>Relay Book</dc:title><dc:creator>Test Author</dc:creator><dc:language>en</dc:language></metadata>'
        '<manifest><item id="css" href="styles/book.css" media-type="text/css"/>'
        '<item id="cover" href="images/cover.bin" media-type="application/octet-stream" properties="cover-image"/>'
        '<item id="one" href="text/one.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="one"/><itemref idref="two"/></spine></package>', encoding="utf-8",
    )
    (source / "EPUB" / "styles" / "book.css").write_bytes(b"p { color: #123456; }\n")
    (source / "EPUB" / "images" / "cover.bin").write_bytes(b"fixture-cover-bytes\x00\x01")
    for number, name in ((1, "one"), (2, "two")):
        (source / "EPUB" / "text" / f"{name}.xhtml").write_text(
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head>'
            f'<title>Chapter {number}</title><link rel="stylesheet" href="../styles/book.css"/></head>'
            f'<body><h1>Chapter {number}</h1><p>Relay moved steadily in section {number}.</p></body></html>',
            encoding="utf-8",
        )
    return source



def test_p1_schema_constrains_evidence_to_current_blocks():
    inputs = {"SOURCE_BLOCKS": [{"id": "B0000001", "text": "One"}, {"id": "B0000002", "text": "Two"}]}
    schema = response_schema(1, inputs)
    term_items = schema["properties"]["terms"]["items"]["properties"]["evidence"]["items"]
    obs_items = schema["properties"]["observations"]["items"]["properties"]["evidence"]["items"]
    assert term_items["enum"] == ["B0000001", "B0000002"]
    assert obs_items["enum"] == ["B0000001", "B0000002"]


def test_successive_llamacpp_attempts_vary_seed_and_temperature():
    provider = type("Provider", (), {"provider": "llamacpp"})()
    base = {"seed": 42, "temperature": 0.05, "messages": []}

    assert _vary_llamacpp_sampling(provider, base, 1) == base
    assert _vary_llamacpp_sampling(provider, base, 2)["seed"] == 43
    assert _vary_llamacpp_sampling(provider, base, 2)["temperature"] == 0.1
    assert _vary_llamacpp_sampling(provider, base, 4)["seed"] == 45
    assert _vary_llamacpp_sampling(provider, base, 4)["temperature"] == 0.2
    assert _vary_llamacpp_sampling(provider, {**base, "temperature": 1.99}, 4)["temperature"] == 2.0
    assert base == {"seed": 42, "temperature": 0.05, "messages": []}


def test_attempt_sampling_does_not_change_unverified_cloud_transports():
    provider = type("Provider", (), {"provider": "openai"})()
    base = {"seed": 42, "temperature": 0.05}
    assert _vary_llamacpp_sampling(provider, base, 3) is base


def test_p1_conservative_repair_drops_only_bad_observations():
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "text": "Alice looked up."}]}
    value = {
        "terms": [{"source": "Alice", "aliases": [], "category": "name", "meaning": "A person.",
                   "confidence": "high", "candidates": [{"text": "Alice", "reason": "Name"}],
                   "evidence": ["B1"]}],
        "observations": [
            {"about": ["Alice"], "kind": "continuity", "statement": "Valid.", "confidence": "high", "evidence": ["B1"]},
            {"about": ["Alice"], "kind": "continuity", "statement": "Bad evidence.", "confidence": "low", "evidence": ["B999"]},
        ],
    }
    repaired, repairs = conservative_repair(1, value, inputs)
    assert [x["statement"] for x in repaired["observations"]] == ["Valid."]
    assert repairs[0]["invalid_evidence_ids"] == ["B999"]
    validate_result(1, repaired, inputs)



def test_p1_conservative_repair_prunes_aliases_and_drops_unattested_terms():
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "text": "The Silfen Motherholme watched over the path."}],
              "EXISTING_MEMORY": {"matched": [], "catalogue": []}}
    value = {
        "terms": [
            {"source": "Silfen Motherholme", "aliases": ["Mother"], "category": "place",
             "meaning": "A Silfen concept.", "confidence": "medium",
             "candidates": [{"text": "Silfen Motherholme", "reason": "Retain source form"}],
             "evidence": ["B1"]},
            {"source": "InventedName", "aliases": [], "category": "name",
             "meaning": "Unsupported.", "confidence": "low",
             "candidates": [{"text": "InventedName", "reason": "Unsupported"}],
             "evidence": ["B1"]},
        ],
        "observations": [],
    }
    repaired, repairs = conservative_repair(1, value, inputs)
    assert len(repaired["terms"]) == 1
    assert repaired["terms"][0]["source"] == "Silfen Motherholme"
    assert repaired["terms"][0]["aliases"] == []
    assert {r["action"] for r in repairs} == {"remove_unattested_aliases", "drop_unattested_term"}
    validate_result(1, repaired, inputs)


def test_p1_conservative_repair_adds_exact_term_evidence_from_same_unit():
    inputs = {"SOURCE_BLOCKS": [
        {"id": "B1", "text": "Mother entered the chamber."},
        {"id": "B2", "text": "She spoke quietly to the others."},
    ], "EXISTING_MEMORY": {"matched": [], "catalogue": []}}
    value = {
        "terms": [{"source": "Mother", "aliases": [], "category": "name",
                   "meaning": "A designation used for a person.", "confidence": "medium",
                   "candidates": [{"text": "Matka", "reason": "Literal designation"}],
                   "evidence": ["B2"]}],
        "observations": [],
    }
    repaired, repairs = conservative_repair(1, value, inputs)
    assert repaired["terms"][0]["evidence"] == ["B2", "B1"]
    assert repairs[0]["action"] == "add_exact_term_evidence"
    assert repairs[0]["matched_form"] == "Mother"
    validate_result(1, repaired, inputs)


def test_runner_recovers_failed_attempt_from_current_fingerprint_without_model_call(project):
    root, args, state = project
    settings = read_json(root / "settings.json")
    book = read_json(root / "book.json")
    with Display(True) as ui:
        client = Client(settings, ui)
        client.discover()
        store = Store(root)
        try:
            plan = analysis_plan(store, book, client, settings)
            unit = plan[0]
            source = "\n\n".join(b["text"] for b in unit["blocks"])
            memory = store.analysis_memory(source, client.count, settings["memory_tokens"])
            inputs = {"SECTION_ID": unit["id"], "SOURCE_BLOCKS": source_blocks(unit["blocks"]), "EXISTING_MEMORY": memory}
            prompt = (root / "prompts" / "pass1.txt").read_text(encoding="utf-8")
            schema = response_schema(1, inputs)
            fingerprint = digest({"prompt": prompt, "inputs": inputs, "schema": schema})
            work = root / "artifacts" / "pass1" / unit["id"] / fingerprint[:20]
            attempt = work / "attempt_002"
            attempt.mkdir(parents=True, exist_ok=True)
            body = client.body(prompt, inputs, schema, 1)
            atomic_json(attempt / "request.json", body)
            answer = {
                "terms": [{"source": "Mother", "aliases": [], "category": "name",
                           "meaning": "Unsupported lexical delta.", "confidence": "low",
                           "candidates": [{"text": "Matka", "reason": "Literal"}],
                           "evidence": [inputs["SOURCE_BLOCKS"][0]["id"]]}],
                "observations": [],
            }
            atomic_text(attempt / "answer.txt", json.dumps(answer) + "\n")
            atomic_json(attempt / "response_meta.json", {"model": "test-model", "finish_reason": "stop", "elapsed_seconds": 1.0})
            runner = Runner(store, client, settings, ui)
            value, _, _ = runner.run(1, "pass1/" + unit["id"], inputs)
            assert value["terms"] == []
            assert state.calls[1] == 0
            recovery = read_json(work / "recovery.json")
            assert recovery["source_attempt"].endswith("attempt_002")
            assert recovery["repairs"][0]["action"] == "drop_unattested_term"
        finally:
            store.close()
            client.close()

def test_analysis_recovers_completed_v12_attempt_without_regeneration(project):
    root, args, state = project
    settings = read_json(root / "settings.json")
    book = read_json(root / "book.json")
    with Display(True) as ui:
        client = Client(settings, ui)
        client.discover()
        store = Store(root)
        try:
            plan = analysis_plan(store, book, client, settings)
            unit = plan[0]
            source = "\n\n".join(b["text"] for b in unit["blocks"])
            memory = store.analysis_memory(source, client.count, settings["memory_tokens"])
            inputs = {"SECTION_ID": unit["id"], "SOURCE_BLOCKS": source_blocks(unit["blocks"]), "EXISTING_MEMORY": memory}
            prompt = (root / "prompts" / "pass1.txt").read_text(encoding="utf-8")
            legacy_body = client.body(prompt, inputs, SCHEMAS[1], 1)
            # Reproduce a real v1.2 retry request: the retry-only fields lived
            # inside the serialized user payload, which v1.3 accidentally
            # compared literally and therefore failed to recover.
            retry_payload = json.loads(legacy_body["messages"][1]["content"])
            retry_payload["VALIDATION_ERROR"] = "Observation has invented evidence IDs."
            retry_payload["RETRY_INSTRUCTION"] = "Correct the previous validation error."
            retry_payload["ALLOWED_EVIDENCE_IDS"] = [b["id"] for b in inputs["SOURCE_BLOCKS"]]
            legacy_body["messages"][1]["content"] = dumps(retry_payload)
            attempt = root / "artifacts" / "pass1" / unit["id"] / "legacy-v12" / "attempt_002"
            attempt.mkdir(parents=True)
            atomic_json(attempt / "request.json", legacy_body)
            relay_ids = [b["id"] for b in unit["blocks"] if "Relay" in b["text"]]
            answer = {
                "terms": [{"source": "Relay", "aliases": [], "category": "technology",
                           "meaning": "A fictional relay system.", "confidence": "medium",
                           "candidates": [{"text": "Relay-A", "reason": "Candidate"}],
                           "evidence": relay_ids}],
                "observations": [{"about": ["Relay"], "kind": "technical", "statement": "Legacy bad observation.",
                                  "confidence": "low", "evidence": ["FAKE_ID"]}],
            }
            atomic_text(attempt / "answer.txt", json.dumps(answer) + "\n")
            atomic_json(attempt / "response_meta.json", {"model": "test-model", "finish_reason": "stop", "elapsed_seconds": 1.0})
        finally:
            store.close()
            client.close()
    assert main(["analyze", *args]) == 0
    # Section 1 was recovered from the completed old attempt; only section 2 called the model.
    assert state.calls[1] == 1
    recoveries = list((root / "artifacts" / "pass1" / plan[0]["id"]).rglob("recovery.json"))
    assert recoveries
    recovery = read_json(recoveries[0])
    assert recovery["repairs"][0]["action"] == "drop_observation"



def test_analysis_plan_reuses_imported_section_token_count(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    store = Store(root)

    class NoRetokenizeClient:
        context = 131072
        tokenizer_identity = {"provider": "llamacpp", "model": "test"}
        def count(self, text):
            raise AssertionError("analysis planner should reuse source_tokens for fitting sections")

    book = {
        "chapters": [{
            "id": "ch0001",
            "number": 1,
            "source_tokens": 9000,
            "source_tokens_tokenizer": {"provider": "llamacpp", "model": "test"},
            "blocks": [
                {"id": "B0000001", "kind": "p", "text": "First paragraph."},
                {"id": "B0000002", "kind": "p", "text": "Second paragraph."},
            ],
        }]
    }
    settings = {
        "memory_tokens": 12000,
        "analysis_source_limit": 0,
        "passes": {"1": {"max_tokens": 12000}},
    }
    try:
        plan = analysis_plan(store, book, NoRetokenizeClient(), settings)
        assert len(plan) == 1
        assert plan[0]["parts"] == 1
        assert [b["id"] for b in plan[0]["blocks"]] == ["B0000001", "B0000002"]
    finally:
        store.close()

def test_extract_unicode_and_inline():
    raw = b'<html><head><style>BAD STYLE</style><title>BAD TITLE</title></head><body><p>In<i>side</i> A &amp; B. \xc5\xbb\xc3\xb3\xc5\x82w.</p><script>BAD SCRIPT</script><p hidden>HIDDEN</p><hr><p>Last<br>line</p></body></html>'
    blocks, encoding, notes = extract_blocks(raw)
    text = "\n".join(b["text"] for b in blocks)
    assert "BAD" not in text and "HIDDEN" not in text
    assert "Inside" in text.replace("*", "")
    assert "A & B" in text and "\u017b\u00f3\u0142w" in text
    assert blocks[-1]["scene_start"] is True
    assert "Last\nline" in text


def test_encoding_override():
    raw = '<html><body><p>Za\u017c\u00f3\u0142\u0107</p></body></html>'.encode("iso-8859-2")
    blocks, _, _ = extract_blocks(raw, encoding="iso-8859-2")
    assert blocks[0]["text"] == 'Za\u017c\u00f3\u0142\u0107'


def test_natural_order(tmp_path):
    for name in ("x10.html", "x2.html", "x1.xhtml"):
        (tmp_path / name).write_text("<p>Hello</p>")
    paths, metadata, warnings = reading_order(tmp_path)
    assert [p.name for p in paths] == ["x1.xhtml", "x2.html", "x10.html"]
    assert metadata["order_method"] == "natural_filename" and warnings


def test_opf_order_and_ncx_boundaries(tmp_path):
    (tmp_path / "a.html").write_text('<body><p id="a1">Part one</p><p>A</p><p id="a2">Part two</p><p>B</p></body>')
    (tmp_path / "z.html").write_text('<body><p>Z first</p></body>')
    (tmp_path / "toc.ncx").write_text('<ncx><navMap><navPoint><navLabel><text>First</text></navLabel><content src="a.html#a1"/></navPoint><navPoint><navLabel><text>Second</text></navLabel><content src="a.html#a2"/></navPoint></navMap></ncx>')
    (tmp_path / "content.opf").write_text('<package><metadata><title>Test</title></metadata><manifest><item id="a" href="a.html"/><item id="z" href="z.html"/><item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine><itemref idref="z"/><itemref idref="a"/></spine></package>')
    paths, meta, warnings = reading_order(tmp_path)
    assert [p.name for p in paths] == ["z.html", "a.html"]
    out = tmp_path.parent / (tmp_path.name + "_output")
    with Display(True) as ui:
        book = import_folder(tmp_path, out, lambda text: len(text.split()),
                             {"whole_section_char_limit": 10000}, ui)
    assert len(book["chapters"]) == 3
    assert book["chapters"][-1]["title"] == "Second"


def test_path_escape_rejected(tmp_path):
    (tmp_path / "x.opf").write_text('<package><manifest><item id="a" href="../outside.html"/></manifest><spine><itemref idref="a"/></spine></package>')
    with pytest.raises(PipelineError):
        reading_order(tmp_path)


def test_lossless_long_paragraph_split():
    text = "Dr. Stone counted 4.3 units. " + ("An unusually extended sentence continues across the page, " * 40) + "and finally ends."
    count = lambda value: max(1, len(value) // 4)
    parts = split_long(text, count, 45)
    assert "".join(text[a:b] for a, b in parts) == text
    assert all(count(text[a:b]) <= 45 for a, b in parts)
    assert len(parts) > 1


def test_import_is_not_inference(project):
    root, args, state = project
    assert not state.calls
    book = read_json(root / "book.json")
    assert len(book["chapters"]) == 2
    assert len(book["chunks"]) == 2
    assert (root / "extracted" / "section_1.txt").exists()


def test_stop_review_and_resume_analysis(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    first = state.calls.copy()
    assert first[1] == 2 and sum(first.values()) == 2
    assert not (root / "translation.txt").exists()
    assert main(["translate", *args, "--continue", "1"]) == 1
    assert main(["analyze", *args]) == 0
    assert state.calls == first
    review = read_json(root / "terms.review.json")
    assert len(review["terms"]) == 1
    assert len(review["terms"][0]["candidates"]) == 2
    assert len(review["terms"][0]["evidence"]) >= 2


def test_user_selection_and_incremental_continue(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["confirmed"] = True
    review["terms"][0]["select"] = 2
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args]) == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    first = state.calls.copy()
    text = (root / "translation.txt").read_text()
    assert "Relay-B" in text
    assert main(["translate", *args, "--continue", "1"]) == 0
    assert state.calls[1] == first[1]
    for stage in (2, 3, 4, 5):
        assert state.calls[stage] == first[stage]+1
    assert len((root / "translation.txt").read_text()) > len(text)
    assert main(["translate", *args, "--continue", "0"]) == 0
    completed = state.calls.copy()
    assert main(["translate", *args, "--continue", "0"]) == 0
    assert state.calls == completed


def test_final_translation_unit_auto_publishes_and_stale_retranslation_republishes(tmp_path, server):
    state, port = server
    source = make_epub_source(tmp_path)
    root = tmp_path / "epub-project"
    args = ["--project", str(root), "--quiet"]
    assert main(["import", str(source), *args, "--host", "127.0.0.1", "--port", str(port)]) == 0
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["confirmed"] = True
    review["terms"][0]["select"] = 1
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args]) == 0

    assert main(["translate", *args, "--continue", "1"]) == 0
    assert not (root / "publication.json").exists()
    assert not (root / "published").exists()
    assert main(["translate", *args, "--continue", "1"]) == 0

    app = create_application()
    status = app.projects.status(StatusCommand(root))
    assert status.translation_complete is True
    assert status.publication.state == "published" and status.publication.current is True
    published = status.publication.output_path
    assert published == root / "published" / "Relay Book [PL].epub"
    with zipfile.ZipFile(published) as epub:
        assert epub.read("EPUB/styles/book.css") == b"p { color: #123456; }\n"
        assert epub.read("EPUB/images/cover.bin") == b"fixture-cover-bytes\x00\x01"

    calls_before_publish = state.calls.copy()
    assert main(["publish", *args]) == 0
    explicit = app.publishing.publish(PublishCommand(root))
    assert explicit.built is False
    assert state.calls == calls_before_publish

    changed = read_json(root / "terms.review.json")
    changed["confirmed"] = True
    changed["terms"][0]["select"] = 2
    changed["terms"][0]["reviewed"] = True
    atomic_json(root / "terms.review.json", changed)
    assert main(["approve", *args]) == 0
    stale = app.publishing.status(PublicationStatusCommand(root))
    assert stale.state == "stale" and stale.current is False
    assert stale.output_path == published and published.is_file()

    assert main(["translate", *args, "--continue", "1"]) == 0
    still_stale = app.publishing.status(PublicationStatusCommand(root))
    assert still_stale.state == "stale" and still_stale.output_path == published
    assert main(["translate", *args, "--continue", "1"]) == 0
    current = app.publishing.status(PublicationStatusCommand(root))
    assert current.state == "published" and current.current is True


def test_automatic_publish_failure_keeps_translation_and_explicit_retry_needs_no_model(tmp_path, server):
    state, port = server
    source = make_epub_source(tmp_path)
    root = tmp_path / "epub-project"
    args = ["--project", str(root), "--quiet"]
    assert main(["import", str(source), *args, "--host", "127.0.0.1", "--port", str(port)]) == 0
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["confirmed"] = True
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args]) == 0

    class BrokenPublisher:
        def __init__(self):
            self.delegate = EpubPublicationBuilder()

        def inspect(self, *args, **kwargs):
            return self.delegate.inspect(*args, **kwargs)

        def build(self, request):
            raise PipelineError("publisher offline for test")

    broken_app = create_application(publication_builder=BrokenPublisher())
    partial = broken_app.pipeline.translate(TranslateCommand(root, chunk_limit=1))
    assert partial.publication is None
    completed = broken_app.pipeline.translate(TranslateCommand(root, chunk_limit=1))
    assert completed.publication.state == "failed"
    assert completed.publication.translation_complete is True
    store = Store(root)
    try:
        assert all(store.chunk(chunk["id"])["status"] == "done" for chunk in read_json(root / "book.json")["chunks"])
    finally:
        store.close()

    calls_after_translation = state.calls.copy()
    retry = create_application().publishing.publish(PublishCommand(root))
    assert retry.status.state == "published" and retry.status.current is True
    assert state.calls == calls_after_translation


def test_interrupted_pass_restarts_only_that_pass(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    state.fail_once = 4
    assert main(["translate", *args, "--continue", "1"]) == 1
    before = state.calls.copy()
    assert before[2] == 1 and before[3] == 1 and before[5] == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    assert state.calls[2] == before[2] and state.calls[3] == before[3]
    assert state.calls[4] == before[4]+1 and state.calls[5] == 1
    assert list((root / "artifacts" / "pass4").rglob("answer.partial.txt"))


def test_bad_completed_json_has_bounded_retry(project):
    root, args, state = project
    state.bad_json_once = 1
    assert main(["analyze", *args]) == 0
    assert state.calls[1] == 3
    pass1_requests = [(inputs, body) for stage, inputs, body in state.requests if stage == 1]
    initial_inputs, initial_body = pass1_requests[0]
    retry_inputs, retry_body = pass1_requests[1]
    assert retry_inputs["SOURCE_BLOCKS"] == initial_inputs["SOURCE_BLOCKS"]
    assert "VALIDATION_ERROR" in retry_inputs
    assert retry_body["seed"] == initial_body["seed"] + 1
    assert retry_body["temperature"] == pytest.approx(initial_body["temperature"] + 0.05)
    assert list((root / "artifacts").rglob("validation_error.txt"))


def test_evidence_ids_validated(project):
    root, args, state = project
    state.unknown_evidence_once = True
    assert main(["analyze", *args]) == 0
    assert state.calls[1] == 3


def test_custom_choice_marks_affected_chunks_stale(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    before = (root / "translation.txt").read_text()
    review = read_json(root / "terms.review.json")
    review["terms"][0]["custom"] = "My-Relay"
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args]) == 0
    db = Store(root)
    first = read_json(root / "book.json")["chunks"][0]["id"]
    assert db.chunk(first)["status"] == "stale"
    assert len(db.terms()[0]["candidates"]) == 2
    db.close()
    assert (root / "translation.txt").read_text() == before
    assert main(["translate", *args, "--continue", "1"]) == 0
    assert "My-Relay" in (root / "translation.txt").read_text()
    assert list((root / "history").glob("before_approval_*.sqlite3"))


def test_no_cross_story_continuity(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    assert main(["translate", *args, "--continue", "0"]) == 0
    second = next((inp for stage, inp, _ in state.requests if stage == 2 and inp["CHUNK_ID"] == "ch0002_c0001"))
    assert second["PREVIOUS_CONTEXT"]["english"] == ""


def test_schema_requires_complete_coverage():
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "text": "Hello world."}],
              "SOURCE_SENTENCES": [{"id": "S1", "text": "Hello world."}]}
    with pytest.raises(PipelineError):
        validate_result(2, {"checks": [], "issues": []}, inputs)
    with pytest.raises(PipelineError):
        validate_result(3, {"translations": [{"id": "OTHER", "text": "Something"}]}, inputs)


def test_corrupt_final_is_not_silently_accepted(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    store = Store(root)
    chunk = read_json(root / "book.json")["chunks"][0]
    path = root / store.chunk(chunk["id"])["final_path"]
    store.close()
    raw = read_json(path)
    raw["translations"][0]["text"] = "Accidental manual change"
    atomic_json(path, raw)
    assert main(["export", *args]) == 1


def test_manifest_source_change_rejected(project):
    root, args, state = project
    book = read_json(root / "book.json")
    book["chunks"][0]["blocks"][0]["text"] = "Changed source"
    atomic_json(root / "book.json", book)
    assert main(["status", *args]) == 1


def test_title_and_thread_tags_can_be_set(project):
    root, args, state = project
    book = read_json(root / "book.json")
    book["chapters"][0]["title"] = "Custom title"
    book["chapters"][0]["thread_id"] = "thread-A"
    atomic_json(root / "book.json", book)
    assert main(["status", *args]) == 0


def test_pending_review_edits_survive_analyze_repeat(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["terms"][0]["custom"] = "Do-not-erase"
    atomic_json(root / "terms.review.json", review)
    assert main(["analyze", *args]) == 0
    assert read_json(root / "terms.review.json")["terms"][0]["custom"] == "Do-not-erase"


def test_case_inflected_candidate_not_global_substitution(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["terms"][0]["custom"] = "Human-choice"
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args, "--accept-defaults"]) == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    requests = [data for stage, data, body in state.requests if stage == 3]
    assert requests[0]["APPROVED_LEXICON"][0]["polish"] == "Human-choice"


def test_later_meaning_notes_are_not_passed_to_earlier_chunk(project):
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    store = Store(root)
    terms = store.terms()
    assert len(terms[0]["meanings"]) == 2
    book = read_json(root / "book.json")
    mem, deps = store.translation_memory(book["chunks"][0], "", lambda t: len(t)//4, 20000)
    assert all(not bid.startswith("NO_SUCH") for note in mem["APPROVED_LEXICON"][0]["meaning_notes"] for bid in note["evidence"])
    # Both notes cite their chapter's complete evidence. The later one cannot enter an earlier chunk.
    later_ids = {e["block_id"] for e in terms[0]["evidence"] if e["chapter_id"] == "ch0002"}
    assert not any(set(note["evidence"]) & later_ids for note in mem["APPROVED_LEXICON"][0]["meaning_notes"])
    store.close()


def test_dropcaps_and_image_alt_do_not_pollute_text():
    raw = b'<html><body><p><span>T</span><span>HE</span> CAT</p><p><img src="x.jpg" alt="Description: X:\\Data\\Books\\x.jpg"/></p><p class="calibre19">Next scene.</p></body></html>'
    blocks, _, notes = extract_blocks(raw)
    assert blocks[0]["text"] == "THE CAT"
    assert all("Description:" not in b["text"] for b in blocks)
    assert blocks[1]["text"] == "Next scene."
    assert blocks[1]["scene_start"] is True
    assert notes == []


def test_major_section_and_natural_scene_planning(tmp_path):
    source = tmp_path / "src_struct"
    source.mkdir()
    big = "A" * 6000
    small = "B" * 4000
    html = (
        '<html><body><h1>THREE</h1>'
        f'<p class="calibre19">{big}</p>'
        f'<p class="calibre21">{small}</p>'
        '<p class="calibre27"><i>Inigo\'s Dream</i></p>'
        '<p class="calibre23">tiny one</p>'
        '<p class="calibre21">tiny two</p></body></html>'
    )
    (source / "book.html").write_text(html)
    out = tmp_path / "project_struct"
    with Display(True) as ui:
        book = import_folder(source, out, lambda text: max(1, len(text)//4),
                             {"whole_section_char_limit": 10000}, ui)
    assert [c["title"] for c in book["chapters"]] == ["THREE", "Inigo's Dream"]
    assert len(book["chapters"][0]["scenes"]) == 2
    assert len(book["chapters"][0]["chunk_ids"]) == 2
    assert len(book["chapters"][1]["scenes"]) == 2
    assert len(book["chapters"][1]["chunk_ids"]) == 1


def test_auto_import_recognizes_numbered_h4_chapters_inside_spine_files(tmp_path):
    source = tmp_path / 'split_epub'
    source.mkdir()
    documents = {
        'split_000.html': '<p>Start and copyright.</p>',
        'split_001.html': '<h2>BOOK TITLE</h2>',
        'split_002.html': ('<h2>AUTHOR NAME</h2><h4>DRAMATIS PERSONAE</h4><p>Cast list.</p>'
                           '<h4>PROLOGUE</h4><p>Opening story.</p>'),
        'split_003.html': ('<h4>ONE</h4><p>First chapter.</p><h4>Scene caption</h4>'
                           '<p>Still the first chapter.</p><h4>TWO</h4><p>Second chapter.</p>'),
        'split_004.html': ('<h4>Chapter Three</h4><p>Third chapter.</p>'
                           '<h4>ABOUT THE AUTHOR</h4><p>Biography.</p>'),
    }
    for name, content in documents.items():
        (source / name).write_text(content)
    (source / 'content.opf').write_text(
        '<package><metadata><title>BOOK TITLE</title></metadata><manifest>'
        + ''.join(f'<item id="f{i}" href="{name}" media-type="application/xhtml+xml"/>'
                  for i, name in enumerate(documents))
        + '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        + '</manifest><spine>'
        + ''.join(f'<itemref idref="f{i}"/>' for i in range(len(documents)))
        + '</spine></package>')
    (source / 'toc.ncx').write_text(
        '<ncx><navMap><navPoint><navLabel><text>Start</text></navLabel>'
        '<content src="split_000.html"/></navPoint></navMap></ncx>')
    out = tmp_path / 'prepared'
    with Display(True) as ui:
        book = import_folder(source, out, lambda text: (len(text) + 3) // 4,
                             {'whole_section_char_limit': 10000}, ui)
    assert [section['title'] for section in book['chapters']] == ['PROLOGUE', 'ONE', 'TWO', 'Chapter Three']
    assert [section['role'] for section in book['non_narrative_sections']] == [
        'front_matter', 'front_matter', 'front_matter', 'back_matter']
    assert book['metadata']['toc'] == [{'file': 'split_000.html', 'anchor': '', 'label': 'Start'}]
    assert 'Scene caption' in [block['text'] for block in book['chapters'][1]['blocks']]
    assert 'Second chapter.' not in [block['text'] for block in book['chapters'][1]['blocks']]
    imported = [block['text'] for section in sorted(
        [*book['chapters'], *book['non_narrative_sections']],
        key=lambda item: item['blocks'][0]['order']) for block in section['blocks']]
    extracted = [block['text'] for name in sorted(documents)
                 for block in extract_blocks(documents[name].encode())[0]]
    assert imported == extracted


def test_auto_import_uses_opening_labels_in_split_files_without_heading_tags(tmp_path):
    source = tmp_path / 'split_epub'
    source.mkdir()
    documents = {
        'split_001.html': '<p>This book is a work of fiction.</p><p>Copyright © 2000.</p>',
        'split_002.html': '<p>Contents</p><p>Chapter 1</p>',
        'split_003.html': '<p>THE</p><p>RIVER</p><p>BOOK</p><p>ADA WRITER</p>',
        'split_004.html': '<p><strong>The River Book<br/>Contents</strong></p><p>Chapter 1</p>',
        'split_005.html': '<p><strong>Part 1: Dawn</strong></p><p><strong>1</strong></p><p>Opening prose.</p>',
        'split_006.html': '<p><strong>2</strong></p><p>Next prose.</p>',
    }
    for name, content in documents.items():
        (source / name).write_text(content)
    (source / 'content.opf').write_text(
        '<package><metadata><title>River Book</title><creator>Ada Writer</creator></metadata><manifest>'
        + ''.join(f'<item id="f{i}" href="{name}" media-type="application/xhtml+xml"/>'
                  for i, name in enumerate(documents))
        + '</manifest><spine>'
        + ''.join(f'<itemref idref="f{i}"/>' for i in range(len(documents)))
        + '</spine></package>')
    with Display(True) as ui:
        book = import_folder(source, tmp_path / 'prepared', lambda text: (len(text) + 3) // 4,
                             {'whole_section_char_limit': 10000}, ui)
    labels = {section['source_file']: section['title']
              for section in [*book['chapters'], *book['non_narrative_sections']]}
    assert labels == {
        'split_001.html': 'Copyright',
        'split_002.html': 'Contents',
        'split_003.html': 'THE RIVER BOOK',
        'split_004.html': 'The River Book · Contents',
        'split_005.html': 'Part 1: Dawn · Chapter 1',
        'split_006.html': 'Chapter 2',
    }
    assert len([*book['chapters'], *book['non_narrative_sections']]) == len(documents)


def test_large_scene_is_never_size_split(tmp_path):
    source = tmp_path / "src_large"
    source.mkdir()
    payload = "word " * 8000
    (source / "book.html").write_text(
        f'<html><body><h1>NINE</h1><p class="calibre19">{payload}</p></body></html>'
    )
    out = tmp_path / "project_large"
    with Display(True) as ui:
        book = import_folder(source, out, lambda text: max(1, len(text)//4),
                             {"whole_section_char_limit": 10000}, ui)
    assert len(book["chapters"][0]["scenes"]) == 1
    assert len(book["chunks"]) == 1
    assert book["chunks"][0]["source_chars"] > 10000


def test_front_and_back_matter_are_not_translation_units(tmp_path):
    source = tmp_path / "src_matter"
    source.mkdir()
    (source / "000.html").write_text('<p>Book title</p><p>Copyright</p>')
    (source / "001.html").write_text('<h1>ONE</h1><p>Story.</p>')
    (source / "002.html").write_text('<h1>ABOUT THE AUTHOR</h1><p>Biography.</p>')
    (source / "content.opf").write_text(
        '<package><metadata><title>Book title</title></metadata><manifest>'
        '<item id="a" href="000.html"/><item id="b" href="001.html"/><item id="c" href="002.html"/>'
        '</manifest><spine><itemref idref="a"/><itemref idref="b"/><itemref idref="c"/></spine></package>'
    )
    out = tmp_path / "project_matter"
    with Display(True) as ui:
        book = import_folder(source, out, lambda text: max(1, len(text)//4),
                             {"whole_section_char_limit": 10000}, ui)
    assert [c["title"] for c in book["chapters"]] == ["ONE"]
    assert {m["role"] for m in book["non_narrative_sections"]} == {"front_matter", "back_matter"}
    assert len(book["chunks"]) == 1


def test_bilingual_review_uses_pipeline_checkpoints_and_preserves_annotations(project):
    from bookpipe.review import ReviewRepository
    root, args, state = project
    assert main(["analyze", *args]) == 0
    assert main(["approve", *args, "--accept-defaults"]) == 0
    assert main(["translate", *args, "--continue", "1"]) == 0
    calls_before = state.calls.copy()
    repository = ReviewRepository(root / "terms.review.json")
    review = repository.load()
    term_id = review["terms"][0]["id"]
    context = repository.evidence(term_id)
    available = [e for e in context["entries"] if e["status"] == "available"]
    assert available and "Relay" in available[0]["source_text"]
    assert "Relay-A" in available[0]["polish_text"]
    assert state.calls == calls_before
    repository.patch_term(term_id, {"custom": "Revised-Relay", "reviewed": True,
                                     "user_notes": "Check the wording in translated context."})
    repository.set_confirmed(True)
    assert main(["approve", *args]) == 0
    assert read_json(root / "terms.review.json")["terms"][0]["user_notes"]
    assert any(e["status"] == "stale" for e in repository.evidence(term_id)["entries"])
    assert state.calls == calls_before  # Merely viewing/approving never calls the LLM.


def test_complete_workflow_through_direct_application_api(project):
    root, _args, state = project
    class RecordingProgress:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    progress = RecordingProgress()
    app = create_application(progress)

    analysis = app.pipeline.analyze(AnalyzeCommand(project=root))
    assert analysis.review_path == root / "terms.review.json"
    with app.review.open_session(ReviewSessionCommand(root)) as review:
        draft = review.load()
        reviewed = review.review_terms([term["id"] for term in draft["terms"]], draft["_revision"])
        confirmed = review.set_confirmed(True, reviewed["revision"])
        assert confirmed["summary"]["confirmed"] is True
    approval = app.review.approve(ApproveCommand(root))
    assert approval.approved_terms > 0 and approval.stale_chunks == 0

    translated = app.pipeline.translate(TranslateCommand(project=root, chunk_limit=1))
    assert translated.completed_units == 1
    exported = app.exports.export_text(ExportCommand(root))
    assert exported.internal_output.read_text(encoding="utf-8").startswith("Translated:")
    status = app.projects.status(StatusCommand(root))
    assert status.analysis_complete and status.approved
    assert sum(chunk.status == "done" for chunk in status.chunks) == 1

    with app.reader.open_session(ReaderSessionCommand(root)) as reader:
        metadata = reader.metadata()
        chapter = reader.chapter(metadata["chapters"][0]["id"])
        assert chapter["blocks"] and isinstance(chapter["complete"], bool)
        assert reader.load()["markers"] == []
    assert state.calls[1] > 0 and all(state.calls[number] > 0 for number in range(2, 6))
    started = [event for event in progress.events if event.kind == "pass_started"]
    assert {event.values["pass_no"] for event in started} == {1, 2, 3, 4, 5}
    assert all(event.values.get("chapter_id") for event in started)
    translated = [event for event in started if event.values["pass_no"] > 1]
    assert all(event.values.get("chunk_id") and event.values.get("task_key") for event in translated)
