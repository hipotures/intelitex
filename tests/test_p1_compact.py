from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from bookpipe.codex_transport import CodexAppServerClient
from bookpipe.contracts import SemanticRequest
from bookpipe.engine import Runner, response_schema, translate
from bookpipe.evidence import AttemptRecorder
from bookpipe.p1_compact import (
    CATEGORIES,
    COMPACT_SCHEMA,
    CONFIDENCES,
    OBSERVATION_KINDS,
    build_transport,
    decode_output,
    encode_input,
)
from bookpipe.schemas import SCHEMAS
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, digest, dumps, read_json


ROOT = Path(__file__).resolve().parents[1]
P1_PROMPT = (ROOT / "prompts" / "pass1.txt").read_text(encoding="utf-8")


@pytest.fixture
def canonical_inputs():
    return {
        "SECTION_ID": "ch0001_a001",
        "SOURCE_BLOCKS": [
            {"id": "B0000001", "kind": "heading", "scene_id": "SC1", "scene_start": True, "text": "Żuraw"},
            {"id": "B0000002", "kind": "paragraph", "scene_id": "SC1", "text": "M-sink waits."},
            {"id": "B0000003", "kind": "paragraph", "text": "No scene here — unchanged."},
            {"id": "B0000004", "kind": "heading", "text": "Still no scene."},
            {"id": "B0000005", "kind": "paragraph", "scene_id": "SC2", "text": "Next scene."},
        ],
        "EXISTING_MEMORY": {
            "catalogue": [{"id": "T1", "source": "Alpha", "aliases": ["A"]}],
            "matched": [{
                "id": "T2", "source": "M-sink", "aliases": [], "candidates": ["M-zlew"],
                "chosen": None, "meaning_notes": ["unknown", None],
            }],
            "catalogue_incomplete": True,
        },
    }


def test_compact_input_exact_mapping_and_determinism(canonical_inputs):
    compact, block_ids, scenes = encode_input(canonical_inputs)
    assert compact == {
        "BLOCK_KINDS": ["heading", "paragraph"],
        "EXISTING_MEMORY": {
            "c": [["T1", "Alpha", ["A"]]],
            "i": 1,
            "m": [["T2", "M-sink", [], ["M-zlew"], None, ["unknown", None]]],
        },
        "SECTION_ID": "ch0001_a001",
        "SOURCE_BLOCKS": [
            [0, 0, 0, 1, "Żuraw"],
            [1, 1, 0, 0, "M-sink waits."],
            [2, 1, 1, 0, "No scene here — unchanged."],
            [3, 0, 1, 0, "Still no scene."],
            [4, 1, 2, 0, "Next scene."],
        ],
    }
    assert block_ids == ("B0000001", "B0000002", "B0000003", "B0000004", "B0000005")
    assert scenes == ("SC1", None, "SC2")
    assert encode_input(copy.deepcopy(canonical_inputs)) == (compact, block_ids, scenes)


def test_compact_input_retry_omits_giant_evidence_list(canonical_inputs):
    inputs = copy.deepcopy(canonical_inputs)
    inputs.update({
        "VALIDATION_ERROR": "bad evidence",
        "RETRY_INSTRUCTION": "canonical retry text",
        "ALLOWED_EVIDENCE_IDS": [f"B{i:07d}" for i in range(500)],
    })
    compact, _, _ = encode_input(inputs)
    assert compact["VALIDATION_ERROR"] == "bad evidence"
    assert "local integer indices" in compact["RETRY_INSTRUCTION"]
    assert "ALLOWED_EVIDENCE_IDS" not in compact


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda x: x["EXISTING_MEMORY"].update(extra=[]), "Unsupported EXISTING_MEMORY"),
        (lambda x: x["EXISTING_MEMORY"]["matched"][0].update(extra="x"), "matched-memory"),
        (lambda x: x["SOURCE_BLOCKS"][0].update(extra="x"), "SOURCE_BLOCKS"),
        (lambda x: x.update(extra="x"), "top-level"),
    ],
)
def test_compact_input_unsupported_shape_fails_closed(canonical_inputs, mutate, match):
    mutate(canonical_inputs)
    with pytest.raises(PipelineError, match=match):
        encode_input(canonical_inputs)


def _complete_compact_output():
    return {
        "t": [{
            "s": f"term-{code}", "a": [], "c": code, "m": "meaning", "q": (code % 3) + 1,
            "p": [{"t": "kandydat", "r": "powód"}], "e": [0],
        } for code in CATEGORIES],
        "o": [{
            "a": [f"term-{code}"], "k": code, "s": "statement", "q": (code % 3) + 1, "e": [1],
        } for code in OBSERVATION_KINDS],
    }


def test_compact_output_decodes_all_codes_and_evidence():
    decoded = decode_output(_complete_compact_output(), ["B-A", "B-B"])
    assert [row["category"] for row in decoded["terms"]] == list(CATEGORIES.values())
    assert [row["confidence"] for row in decoded["terms"]] == [CONFIDENCES[(code % 3) + 1] for code in CATEGORIES]
    assert [row["confidence"] for row in decoded["observations"]] == [
        CONFIDENCES[(code % 3) + 1] for code in OBSERVATION_KINDS
    ]
    assert [row["kind"] for row in decoded["observations"]] == list(OBSERVATION_KINDS.values())
    assert all(row["evidence"] == ["B-A"] for row in decoded["terms"])
    assert all(row["evidence"] == ["B-B"] for row in decoded["observations"])
    assert decoded["terms"][0]["candidates"] == [{"text": "kandydat", "reason": "powód"}]


@pytest.mark.parametrize("bad", [-1, 2, True, 0.0, "0", None, [0]])
def test_compact_output_rejects_invalid_evidence_indices(bad):
    value = _complete_compact_output()
    value["t"][0]["e"] = [bad]
    with pytest.raises(PipelineError, match="[Ee]vidence|Malformed"):
        decode_output(value, ["B-A", "B-B"])


@pytest.mark.parametrize(
    "path,bad",
    [("category", 99), ("confidence", 99), ("kind", 99)],
)
def test_compact_output_rejects_unknown_codes(path, bad):
    value = _complete_compact_output()
    if path == "category":
        value["t"][0]["c"] = bad
    elif path == "confidence":
        value["t"][0]["q"] = bad
    else:
        value["o"][0]["k"] = bad
    with pytest.raises(PipelineError, match="Malformed compact-v1"):
        decode_output(value, ["B-A", "B-B"])


def test_compact_schema_is_exactly_flat_and_request_independent(canonical_inputs):
    encoded = json.dumps(COMPACT_SCHEMA, sort_keys=True)
    for forbidden in ("$ref", "$defs", "oneOf", "anyOf", "allOf", "pattern", "dependentSchemas", "if"):
        assert f'"{forbidden}"' not in encoded
    assert "B0000001" not in encoded
    assert COMPACT_SCHEMA["properties"]["t"]["items"]["properties"]["e"]["items"] == {"type": "integer"}
    assert COMPACT_SCHEMA["properties"]["o"]["items"]["properties"]["e"]["items"] == {"type": "integer"}
    assert response_schema(1, canonical_inputs)["properties"]["terms"]["items"]["properties"]["evidence"]["items"]["enum"] == [
        block["id"] for block in canonical_inputs["SOURCE_BLOCKS"]
    ]
    assert SCHEMAS[1]["properties"]["terms"]["items"]["properties"]["evidence"]["items"] == {"type": "string"}
    assert len(dumps(COMPACT_SCHEMA).encode("utf-8")) == 1717
    assert digest(COMPACT_SCHEMA) == "b4cc2e8b58fad09eeddeeeeac9fc70a2bf045188b182e180c705a3293cd76100"


def _codex_client(tmp_path, options=None):
    return CodexAppServerClient({
        "profile_name": "codex", "resolved_profile": {}, "provider": "codex", "model": "test-model",
        "context_size": 100000, "planning_output_reserve": 1000, "request_timeout": 5,
        "reasoning_effort": "low", "options": options or {}, "project_root": str(tmp_path),
    }, Display(True))


def test_codex_p1_defaults_compact_but_other_passes_and_rollback_are_canonical(tmp_path, canonical_inputs):
    schema = response_schema(1, canonical_inputs)
    client = _codex_client(tmp_path)
    compact = client.body(P1_PROMPT, canonical_inputs, schema, 1)
    assert compact["wire_format"] == "compact-v1"
    assert compact["output_schema"] == COMPACT_SCHEMA
    assert json.loads(compact["input"])["SOURCE_BLOCKS"][0] == [0, 0, 0, 1, "Żuraw"]
    assert "Compact JSON contract" in compact["developer_instructions"]
    assert client.preflight(compact) == (
        len(compact["developer_instructions"].encode("utf-8")) + len(compact["input"].encode("utf-8"))
    )

    rollback = _codex_client(tmp_path, {"p1_wire_format": "canonical"}).body(P1_PROMPT, canonical_inputs, schema, 1)
    assert rollback["wire_format"] == "canonical"
    assert rollback["output_schema"] == schema
    assert json.loads(rollback["input"]) == canonical_inputs

    for pass_no in range(2, 6):
        body = _codex_client(tmp_path).body("canonical prompt", {"SOURCE_BLOCKS": []}, {"type": "object"}, pass_no)
        assert body["wire_format"] == "canonical"
        assert json.loads(body["input"]) == {"SOURCE_BLOCKS": []}


def test_canonical_fingerprint_regression_and_wire_switch(canonical_inputs, tmp_path):
    schema = response_schema(1, canonical_inputs)
    fingerprint = digest({"prompt": P1_PROMPT, "inputs": canonical_inputs, "schema": schema})
    # Fixture generated by the pre-compact canonical formula. A transport body
    # or p1_wire_format option entering task identity changes this hash.
    assert fingerprint == "5b200d8da6cc3b34c99fcc89a69f7a5d9675c927424eadb437583c0f273895af"
    compact = _codex_client(tmp_path).body(P1_PROMPT, canonical_inputs, schema, 1)
    canonical = _codex_client(tmp_path, {"p1_wire_format": "canonical"}).body(P1_PROMPT, canonical_inputs, schema, 1)
    assert compact != canonical
    assert digest({"prompt": P1_PROMPT, "inputs": canonical_inputs, "schema": schema}) == fingerprint


def test_p2_p5_canonical_fingerprint_fixtures():
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "kind": "paragraph", "text": "Source."}]}
    expected = {
        2: "d3773c0dad1004e6ffb7c71b25242c1b8eb217c92e0cd0b5a2490ae825bd9d28",
        3: "fd05f80be6d9f198df97766157933af7818db271c81f3e96fc580495b91ae76d",
        4: "2473eb20cb1a4d7742a326ccc7799f96a231e3f6ba925185daaa08c125466195",
        5: "684468563fc13bcda41a1357e1f3bfc1ca476d5ee24d541b5dcebe74a48a794d",
    }
    for pass_no in range(2, 6):
        prompt = (ROOT / "prompts" / f"pass{pass_no}.txt").read_text(encoding="utf-8")
        assert digest({"prompt": prompt, "inputs": inputs, "schema": response_schema(pass_no, inputs)}) == expected[pass_no]


def test_build_transport_retains_decoder_map_outside_model_payload(canonical_inputs):
    transport = build_transport(P1_PROMPT, canonical_inputs)
    assert transport.block_ids == tuple(block["id"] for block in canonical_inputs["SOURCE_BLOCKS"])
    assert transport.block_id_to_index == {block["id"]: index for index, block in enumerate(canonical_inputs["SOURCE_BLOCKS"])}
    assert all("B000" not in json.dumps(row) for row in transport.input_payload["SOURCE_BLOCKS"])
    assert "scene_ids" not in transport.input_payload


def test_unknown_wire_format_fails_closed(tmp_path, canonical_inputs):
    client = _codex_client(tmp_path, {"p1_wire_format": "future-v9"})
    with pytest.raises(PipelineError, match="Unknown Codex Pass-1 wire format"):
        client.body(P1_PROMPT, canonical_inputs, response_schema(1, canonical_inputs), 1)


class FakeCompactCodex:
    provider = "codex"
    preflight_input_unit = "utf8_bytes"
    preflight_input_quality = "conservative_upper_bound"
    preflight_input_method = "test UTF-8 bytes"

    def __init__(self, root: Path, raw_value: dict | None = None, *, wire_format="compact-v1"):
        options = {"p1_wire_format": wire_format}
        self.profile_name = "codex-test"
        self.model = "test-model"
        self.timeout = 5.0
        self.resolved_profile = {
            "provider": "codex", "model": self.model, "reasoning_effort": "low",
            "planning_output_reserve": 100, "max_output_tokens": None, "options": options,
        }
        self._client = CodexAppServerClient({
            **self.resolved_profile, "profile_name": self.profile_name,
            "resolved_profile": self.resolved_profile, "context_size": 100000,
            "request_timeout": self.timeout, "project_root": str(root),
        }, Display(True))
        self.raw_value = raw_value
        self.calls = 0

    def body(self, prompt, inputs, schema, pass_no):
        return self._client.body(prompt, inputs, schema, pass_no)

    def preflight(self, body, recorder=None):
        return len(body["developer_instructions"].encode()) + len(body["input"].encode())

    def generate(self, body, directory, recorder):
        self.calls += 1
        if self.raw_value is None:
            raise AssertionError("unexpected model call")
        plan = {
            "wire_format": body["wire_format"],
            "thread": {"developerInstructions": body["developer_instructions"]},
            "turn": {"input": [{"type": "text", "text": body["input"]}], "outputSchema": body["output_schema"]},
        }
        recorder.transport_request(plan, body["output_schema"])
        raw = dumps(self.raw_value)
        recorder.write_answer(raw)
        return raw, {
            "provider": "codex", "finish_reason": "stop", "status": "completed",
            "usage_status": "reported", "wire_format": body["wire_format"],
        }


def _runner_project(tmp_path):
    root = tmp_path / "project"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "pass1.txt").write_text(P1_PROMPT, encoding="utf-8")
    store = Store(root)
    settings = {"json_retries": 0, "passes": {str(i): {"max_tokens": 100, "temperature": 0} for i in range(1, 6)}}
    return root, store, settings


def _small_inputs():
    return {
        "SECTION_ID": "unit",
        "SOURCE_BLOCKS": [{"id": "B1", "kind": "paragraph", "text": "M-sink waits."}],
        "EXISTING_MEMORY": {"catalogue": [], "matched": [], "catalogue_incomplete": False},
    }


def _compact_answer(source="M-sink"):
    return {"t": [{
        "s": source, "a": [], "c": 7, "m": "A device.", "q": 2,
        "p": [{"t": "M-sink", "r": "Coinage"}], "e": [0],
    }], "o": []}


def _canonical_answer(source="M-sink"):
    return {"terms": [{
        "source": source, "aliases": [], "category": "technology", "meaning": "A device.",
        "confidence": "medium", "candidates": [{"text": "M-sink", "reason": "Coinage"}],
        "evidence": ["B1"],
    }], "observations": []}


def test_compact_attempt_evidence_and_accepted_result_stay_canonical(tmp_path):
    root, store, settings = _runner_project(tmp_path)
    provider = FakeCompactCodex(root, _compact_answer())
    try:
        value, relative, _ = Runner(store, provider, settings, Display(True)).run(1, "pass1/unit", _small_inputs())
        assert value == _canonical_answer()
        assert read_json(root / relative) == _canonical_answer()
        attempt = next((root / "artifacts" / "pass1" / "unit").rglob("attempt_001"))
        semantic = read_json(attempt / "request.semantic.json")
        transport = read_json(attempt / "request.transport.json")
        assert semantic["trusted_instructions"] == P1_PROMPT
        assert semantic["input_payload"] == _small_inputs()
        assert semantic["output_schema"] == response_schema(1, _small_inputs())
        assert read_json(attempt / "schema.canonical.json") == response_schema(1, _small_inputs())
        assert read_json(attempt / "schema.transport.json") == COMPACT_SCHEMA
        assert json.loads(transport["turn"]["input"][0]["text"])["SOURCE_BLOCKS"] == [[0, 0, 0, 0, "M-sink waits."]]
        assert read_json(attempt / "decoded.canonical.json") == _canonical_answer()
        assert json.loads((attempt / "answer.txt").read_text()) == _compact_answer()
        assert read_json(attempt / "response_meta.json")["wire_format"] == "compact-v1"
        assert read_json(attempt / "attempt.json")["lifecycle"]["acceptance"] == "checkpointed"
    finally:
        store.close()


def test_existing_canonical_p1_checkpoint_returns_before_provider_call(tmp_path):
    root, store, settings = _runner_project(tmp_path)
    inputs = _small_inputs()
    fingerprint = digest({"prompt": P1_PROMPT, "inputs": inputs, "schema": response_schema(1, inputs)})
    result = root / "old" / "result.json"
    atomic_json(result, _canonical_answer())
    store.save_job("pass1/unit", fingerprint, result, {"wire_format": "canonical"})
    provider = FakeCompactCodex(root)
    try:
        value, _, got = Runner(store, provider, settings, Display(True)).run(1, "pass1/unit", inputs)
        assert got == fingerprint and value == _canonical_answer()
        assert provider.calls == 0
    finally:
        store.close()


def _write_completed_attempt(root, directory, provider, inputs, value, wire_format):
    schema = response_schema(1, inputs)
    fingerprint = digest({"prompt": P1_PROMPT, "inputs": inputs, "schema": schema})
    old_profile = copy.deepcopy(provider.resolved_profile)
    old_profile["options"].pop("p1_wire_format", None)
    semantic = SemanticRequest(
        task_key="pass1/unit", task_fingerprint=fingerprint, pass_no=1, attempt_no=1,
        trusted_instructions=P1_PROMPT, input_payload=inputs, output_schema=schema, schema_version=1,
        profile="old-codex", provider="codex", requested_model=provider.model,
        reasoning_effort="low", planning_output_reserve=100, enforced_output_cap=None,
        timeout_seconds=5.0, resolved_profile=old_profile,
    )
    recorder = AttemptRecorder(directory, {"provider": "codex"})
    recorder.semantic(semantic.as_dict(), schema)
    recorder.transport_request({"old_style": wire_format}, schema if wire_format == "canonical" else COMPACT_SCHEMA)
    recorder.write_answer(dumps(value))
    recorder.finish(
        generation="completed", validation="passed",
        metadata={"provider": "codex", "finish_reason": "stop", "wire_format": wire_format},
    )


def test_old_verbose_completed_attempt_recovers_without_model_call(tmp_path):
    root, store, settings = _runner_project(tmp_path)
    inputs = _small_inputs()
    provider = FakeCompactCodex(root)
    fingerprint = digest({"prompt": P1_PROMPT, "inputs": inputs, "schema": response_schema(1, inputs)})
    old = root / "artifacts" / "pass1" / "unit" / fingerprint[:20] / "attempt_001"
    _write_completed_attempt(root, old, provider, inputs, _canonical_answer(), "canonical")
    try:
        value, relative, got = Runner(store, provider, settings, Display(True)).run(1, "pass1/unit", inputs)
        assert value == _canonical_answer() and got == fingerprint
        assert provider.calls == 0
        assert read_json(root / relative) == _canonical_answer()
        assert read_json(root / relative).keys() == {"terms", "observations"}
        assert store.job("pass1/unit", fingerprint) is not None
    finally:
        store.close()


def test_mixed_verbose_and_compact_attempts_recover_latest_compact(tmp_path):
    root, store, settings = _runner_project(tmp_path)
    inputs = _small_inputs()
    provider = FakeCompactCodex(root)
    fingerprint = digest({"prompt": P1_PROMPT, "inputs": inputs, "schema": response_schema(1, inputs)})
    base = root / "artifacts" / "pass1" / "unit" / fingerprint[:20]
    old = base / "attempt_001"
    new = base / "attempt_002"
    _write_completed_attempt(root, old, provider, inputs, _canonical_answer(), "canonical")
    _write_completed_attempt(root, new, provider, inputs, _compact_answer(), "compact-v1")
    old_time = old.stat().st_mtime - 10
    os.utime(old, (old_time, old_time))
    try:
        value, _, _ = Runner(store, provider, settings, Display(True)).run(1, "pass1/unit", inputs)
        assert value == _canonical_answer()
        assert provider.calls == 0
        recovery = next((root / "artifacts" / "pass1" / "unit").rglob("recovery.json"))
        assert "attempt_002" in read_json(recovery)["source_attempt"]
    finally:
        store.close()


def test_codec_failure_records_attempt_and_does_not_checkpoint(tmp_path):
    root, store, settings = _runner_project(tmp_path)
    inputs = _small_inputs()
    inputs["EXISTING_MEMORY"]["unexpected"] = []
    provider = FakeCompactCodex(root, _compact_answer())
    try:
        with pytest.raises(PipelineError, match="Unsupported EXISTING_MEMORY"):
            Runner(store, provider, settings, Display(True)).run(1, "pass1/unit", inputs)
        attempt = next((root / "artifacts" / "pass1" / "unit").rglob("attempt_001"))
        manifest = read_json(attempt / "attempt.json")
        assert manifest["lifecycle"]["generation"] == "not_submitted"
        assert read_json(attempt / "response_meta.json")["status"] == "preflight_failed"
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert provider.calls == 0
    finally:
        store.close()


def test_12_of_34_translation_units_resume_at_first_unfinished(tmp_path, monkeypatch):
    root = tmp_path / "resume-project"
    root.mkdir()
    store = Store(root)
    chunks = []
    for index in range(34):
        cid = f"ch0001_c{index + 1:04d}"
        chunks.append({
            "id": cid, "chapter_id": "ch0001", "index_in_chapter": index + 1,
            "blocks": [{"id": f"B{index + 1}", "kind": "paragraph", "text": "Source", "parent_id": f"B{index + 1}"}],
            "sentences": [{"id": f"S{index + 1}", "text": "Source"}],
        })
    book = {"chunks": chunks, "chapters": [{"id": "ch0001", "number": 1, "chunk_ids": [c["id"] for c in chunks]}]}
    store.register_chunks(book)
    with store.db:
        store.set("analysis_done", True)
        store.set("approved", True)
        for chunk in chunks[:12]:
            store.db.execute("UPDATE chunks SET status='done' WHERE id=?", (chunk["id"],))

    called = []
    def fake_run(_runner, pass_no, key, inputs):
        called.append((pass_no, key))
        if pass_no in (3, 5):
            value = {"translations": [{"id": inputs["SOURCE_BLOCKS"][0]["id"], "text": "Polish"}]}
        elif pass_no == 2:
            value = {"checks": [], "issues": []}
        else:
            value = {"checks": [], "corrections": []}
        return value, "synthetic/result.json", f"fp-{pass_no}"

    monkeypatch.setattr(Runner, "run", fake_run)
    monkeypatch.setattr("bookpipe.engine.previous_context", lambda *_: {"english": "", "polish": ""})
    monkeypatch.setattr(store, "translation_memory", lambda *_: ({"APPROVED_LEXICON": [], "OBSERVATIONS": []}, []))
    monkeypatch.setattr("bookpipe.engine.export_text", lambda *_: None)
    settings = {"continuity_tokens": 10, "memory_tokens": 10, "passes": {str(i): {"max_tokens": 10} for i in range(1, 6)}}
    try:
        client = type("CountClient", (), {"count": staticmethod(lambda text: len(text))})()
        translate(store, book, client, settings, Display(True), 1)
        assert called == [(stage, f"pass{stage}/ch0001_c0013") for stage in range(2, 6)]
        assert sum(store.chunk(chunk["id"])["status"] == "done" for chunk in chunks) == 13
        assert all(store.chunk(chunk["id"])["status"] == "done" for chunk in chunks[:12])
    finally:
        store.close()
