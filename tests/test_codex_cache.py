from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from bookpipe.codex_cache import (
    COMMON_FIELDS, TRANSPORT_SCHEMA, decode_output, developer_contract, encode_value, serialize,
)
from bookpipe.codex_transport import BASE_INSTRUCTIONS, CodexAppServerClient
from bookpipe.engine import Runner, _semantic_execution_signature, response_schema, translate
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, digest, dumps, read_json


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {n: (ROOT / "prompts" / f"pass{n}.txt").read_text(encoding="utf-8") for n in range(1, 6)}
COMMON = {
    "SOURCE_BLOCKS": [{"id": "B1", "kind": "paragraph", "text": "Żuraw waits.\nA second line."}],
    "APPROVED_LEXICON": [], "OBSERVATIONS": [],
    "PREVIOUS_CONTEXT": {"english": "Earlier.", "polish": "Wcześniej."}, "CHUNK_ID": "chunk1",
}
SENTENCES = [{"id": "S1", "block_id": "B1", "text": "Żuraw waits."}]
AUDIT = {"checks": [{"sid": "S1", "risk": "low"}], "issues": []}
DRAFT = {"translations": [{"id": "B1", "text": "Żuraw czeka.\nDrugi wiersz."}]}
LEDGER = {"checks": [{"sid": "S1", "status": "ok"}], "corrections": []}
RESULTS = {
    1: {
        "terms": [{"source": "Żuraw", "aliases": [], "category": "name", "meaning": "Named entity.",
                   "confidence": "high", "candidates": [{"text": "Żuraw", "reason": "Retain the attested designation."}],
                   "evidence": ["B1"]}],
        "observations": [{"about": ["Żuraw"], "kind": "reference", "statement": "This entity waits.",
                          "confidence": "high", "evidence": ["B1"]}],
    },
    2: AUDIT, 3: DRAFT, 4: LEDGER, 5: DRAFT,
}


def inputs_for(n):
    stages = {
        2: {"SOURCE_SENTENCES": SENTENCES}, 3: {"SEMANTIC_AUDIT": AUDIT},
        4: {"POLISH_DRAFT": DRAFT, "SEMANTIC_AUDIT": AUDIT, "SOURCE_SENTENCES": SENTENCES},
        5: {"POLISH_DRAFT": DRAFT, "CORRECTION_LEDGER": LEDGER},
    }
    if n == 1:
        return copy.deepcopy({"SOURCE_BLOCKS": COMMON["SOURCE_BLOCKS"], "SECTION_ID": "unit1",
                              "EXISTING_MEMORY": {"matched": [], "catalogue": [], "catalogue_incomplete": False}})
    return copy.deepcopy({**COMMON, **stages[n]})


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    for n, prompt in PROMPTS.items():
        path = root / "prompts" / f"pass{n}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    return root


def client(root, **options):
    profile = {
        "provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "low",
        "context_size": 100000, "planning_output_reserve": 1000, "max_output_tokens": None,
        "request_timeout": 5, "options": {"translation_wire_format": "cache-v1", "p1_wire_format": "cache-v1", **options},
    }
    return CodexAppServerClient({**profile, "resolved_profile": copy.deepcopy(profile),
                                 "profile_name": "test", "project_root": str(root)}, Display(True))


def body_for(provider, n, inputs=None):
    inputs = inputs or inputs_for(n)
    return provider.body(PROMPTS[n], inputs, response_schema(n, inputs), n)


def test_common_prefix_schema_and_developer_are_identical_across_all_translation_passes(project):
    provider = client(project)
    bodies = [body_for(provider, n) for n in range(1, 6)]
    layouts = [serialize(inputs_for(n), response_schema(n, inputs_for(n)), n) for n in range(2, 6)]
    assert len({layout.common_prefix.encode("utf-8") for layout in layouts}) == 1
    for key in ("cache_prefix_sha256", "cache_prefix_utf8_bytes", "cache_identity_sha256"):
        assert len({body["cache_diagnostics"][key] for body in bodies[1:]}) == 1
    assert len({body["developer_instructions"] for body in bodies}) == 1
    assert len({encode_value(body["output_schema"]) for body in bodies}) == 1
    for body in bodies:
        diag = body["cache_diagnostics"]
        assert diag["developer_instructions_sha256"] == digest(body["developer_instructions"])
        assert diag["transport_output_schema_sha256"] == digest(encode_value(body["output_schema"]))
        assert diag["base_instructions_sha256"] == digest(BASE_INSTRUCTIONS.read_text(encoding="utf-8").strip())
        assert diag["requested_model"] == "gpt-6.1-sol" and diag["requested_effort"] == "low"
    assert bodies[0]["cache_diagnostics"]["analysis_unit_id"] == "unit1"


def test_wire_order_and_exact_semantic_inputs(project):
    provider = client(project)
    for n in range(2, 6):
        inputs = inputs_for(n)
        body = body_for(provider, n)
        fields = list(json.loads(body["input"]))
        assert fields[:6] == ["CACHE_V1", *COMMON_FIELDS]
        assert fields[-2:] == ["CANONICAL_OUTPUT_SCHEMA", "ACTIVE_PASS"]
        wire = json.loads(body["input"])
        assert wire.pop("ACTIVE_PASS") == n
        assert wire.pop("CACHE_V1") == 1
        assert wire.pop("CANONICAL_OUTPUT_SCHEMA") == response_schema(n, inputs)
        assert wire == inputs
        # Reversing dictionaries at every level cannot change deterministic encoding.
        def reverse(value):
            if isinstance(value, dict):
                return {k: reverse(v) for k, v in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse(v) for v in value]
            return value
        assert body_for(provider, n, reverse(inputs))["input"] == body["input"]


@pytest.mark.parametrize("n,field", [(3, "SEMANTIC_AUDIT"), (4, "SEMANTIC_AUDIT"),
                                    (4, "POLISH_DRAFT"), (5, "POLISH_DRAFT"), (5, "CORRECTION_LEDGER")])
def test_artifact_changes_only_change_dynamic_suffix(project, n, field):
    provider = client(project)
    before = body_for(provider, n)
    inputs = inputs_for(n)
    inputs[field] = {"different": "artifact"}
    after = body_for(provider, n, inputs)
    assert before["cache_diagnostics"]["cache_prefix_sha256"] == after["cache_diagnostics"]["cache_prefix_sha256"]
    assert before["cache_diagnostics"]["dynamic_suffix_sha256"] != after["cache_diagnostics"]["dynamic_suffix_sha256"]


@pytest.mark.parametrize("field", COMMON_FIELDS)
def test_common_data_changes_prefix(project, field):
    provider = client(project)
    before = body_for(provider, 4)
    inputs = inputs_for(4)
    inputs[field] = "Changed"
    assert body_for(provider, 4, inputs)["cache_diagnostics"]["cache_prefix_sha256"] != before["cache_diagnostics"]["cache_prefix_sha256"]


@pytest.mark.parametrize("n", range(1, 6))
def test_retries_are_after_reusable_material(project, n):
    provider = client(project)
    before = body_for(provider, n)
    inputs = inputs_for(n)
    inputs.update(VALIDATION_ERROR="changed error", RETRY_INSTRUCTION="correct", ALLOWED_EVIDENCE_IDS=["B1"])
    after = body_for(provider, n, inputs)
    assert before["cache_diagnostics"]["cache_prefix_sha256"] == after["cache_diagnostics"]["cache_prefix_sha256"]
    assert before["cache_diagnostics"]["dynamic_suffix_sha256"] != after["cache_diagnostics"]["dynamic_suffix_sha256"]
    assert after["input"].index('"VALIDATION_ERROR":') > after["input"].index('"SOURCE_BLOCKS":')
    if n in (4, 5):
        assert before["cache_diagnostics"]["draft_prefix_sha256"] == after["cache_diagnostics"]["draft_prefix_sha256"]


def test_p4_p5_share_complete_draft_prefix(project):
    provider = client(project)
    bodies = [body_for(provider, n) for n in (4, 5)]
    layouts = [serialize(inputs_for(n), response_schema(n, inputs_for(n)), n) for n in (4, 5)]
    assert layouts[0].draft_prefix == layouts[1].draft_prefix
    assert layouts[0].draft_prefix.endswith('"POLISH_DRAFT":' + encode_value(DRAFT) + ',')
    for body in bodies:
        assert body["input"].startswith(layouts[0].draft_prefix)
        diag = body["cache_diagnostics"]
        assert diag["draft_prefix_sha256"] == digest(layouts[0].draft_prefix)
        assert diag["draft_prefix_utf8_bytes"] == len(layouts[0].draft_prefix.encode("utf-8"))
    assert bodies[0]["cache_diagnostics"]["draft_cache_identity_sha256"] == bodies[1]["cache_diagnostics"]["draft_cache_identity_sha256"]
    assert "SEMANTIC_AUDIT" not in json.loads(bodies[1]["input"])


def test_p1_source_prefix_and_rollback(project):
    provider = client(project)
    p1 = body_for(provider, 1)
    p2 = body_for(provider, 2)
    layout = serialize(inputs_for(1), response_schema(1, inputs_for(1)), 1)
    assert p1["input"].startswith(layout.common_prefix)
    assert p2["input"].startswith(layout.common_prefix)
    assert p1["input"].index('"SOURCE_BLOCKS":') < p1["input"].index('"EXISTING_MEMORY":')
    assert body_for(client(project, p1_wire_format="compact-v1"), 1)["wire_format"] == "compact-v1"
    canonical = body_for(client(project, p1_wire_format="canonical"), 1)
    assert json.loads(canonical["input"]) == inputs_for(1)
    provider.settings["options"].pop("p1_wire_format")
    assert body_for(provider, 1)["wire_format"] == "compact-v1"


def test_developer_contract_contains_every_complete_project_definition(project):
    prompts = copy.deepcopy(PROMPTS)
    prompts[5] += "\nCustom project rule: retain supported punctuation.\n"
    (project / "prompts" / "pass5.txt").write_text(prompts[5], encoding="utf-8")
    developer = client(project).body(prompts[5], inputs_for(5), response_schema(5, inputs_for(5)), 5)["developer_instructions"]
    assert developer == developer_contract(prompts)
    definitions = json.loads(developer[developer.index('{'):])
    assert definitions == {str(n): prompts[n] for n in range(1, 6)}
    with pytest.raises(PipelineError, match="differs"):
        body_for(client(project), 5)
    (project / "prompts" / "pass2.txt").unlink()
    with pytest.raises(PipelineError, match="Cannot read"):
        body_for(client(project), 1)


@pytest.mark.parametrize("value", [None, {}, {"payload_json": {}}, {"payload_json": "{}", "extra": 1},
                                   {"payload_json": "not JSON"}, {"payload_json": "[]"},
                                   {"payload_json": '{"a":1,"a":2}'}, {"payload_json": '{"a":NaN}'}])
def test_decode_fails_closed(value):
    with pytest.raises((PipelineError, json.JSONDecodeError, jsonschema.ValidationError)):
        decode_output(value)


@pytest.mark.parametrize("n", range(1, 6))
def test_canonical_decoding_has_no_shape_changes(n):
    assert decode_output({"payload_json": dumps(RESULTS[n])}) == RESULTS[n]


@pytest.mark.parametrize("option,value", [("p1_wire_format", "future"), ("translation_wire_format", "compact-v1"),
                                          ("translation_wire_format", None), ("translation_wire_format", [])])
def test_unknown_config_fails_closed(project, option, value):
    provider = client(project, **{option: value})
    with pytest.raises(PipelineError, match="Unknown Codex"):
        body_for(provider, 1 if option == "p1_wire_format" else 2)
    settings = {"profiles": {"test": provider.resolved_profile}, "default_profile": "test"}
    with pytest.raises(PipelineError, match="wire_format"):
        validate_profiles(settings)


def test_profile_resolution_records_both_wire_formats(project):
    settings = {"profiles": {}, "default_profile": "codex-sol-low", "passes": {"2": {"max_tokens": 1000}}}
    _, profile, _ = resolve_profile(settings, 2, project=project)
    assert profile["options"]["p1_wire_format"] == "compact-v1"
    assert profile["options"]["translation_wire_format"] == "canonical"


@pytest.mark.parametrize("setting,value", [("model", "another-model"), ("reasoning_effort", "high")])
def test_cache_identity_distinguishes_model_and_effort(project, setting, value):
    before = body_for(client(project), 2)["cache_diagnostics"]
    provider = client(project)
    provider.settings[setting] = value
    if setting == "model":
        provider.model = value
    after = body_for(provider, 2)["cache_diagnostics"]
    assert before["cache_prefix_sha256"] == after["cache_prefix_sha256"]
    assert before["cache_identity_sha256"] != after["cache_identity_sha256"]


@pytest.mark.parametrize("mutate", [
    lambda inputs: inputs.update(SEMANTIC_AUDIT=AUDIT),
    lambda inputs: inputs.pop("SOURCE_BLOCKS"),
    lambda inputs: inputs.update(ACTIVE_PASS=4),
])
def test_wire_codec_never_drops_or_injects_semantic_data(project, mutate):
    inputs = inputs_for(5)
    schema = response_schema(5, inputs)
    mutate(inputs)
    with pytest.raises(PipelineError, match="Invalid cache-v1 inputs"):
        client(project).body(PROMPTS[5], inputs, schema, 5)


def test_generation_failure_retains_layout_in_response_metadata(project, monkeypatch):
    provider = SyntheticCodex(project)
    def fail(body, directory, recorder):
        raise PipelineError("synthetic provider failure")
    monkeypatch.setattr(provider, "generate", fail)
    store = Store(project)
    try:
        with pytest.raises(PipelineError, match="synthetic provider failure"):
            Runner(store, provider, SETTINGS, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        meta = read_json(next((project / "artifacts").rglob("response_meta.json")))
        layout = read_json(next((project / "artifacts").rglob("cache_layout.json")))
        assert meta["wire_format"] == "cache-v1"
        assert meta["cache_identity_sha256"] == layout["cache_identity_sha256"]
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_execution_identity_ignores_only_wire_switch(project):
    profile = client(project).resolved_profile
    base = {"resolved_profile": profile, "input_payload": inputs_for(4), "requested_model": "gpt-6.1-sol"}
    switched = copy.deepcopy(base)
    switched["resolved_profile"]["options"].update(p1_wire_format="compact-v1", translation_wire_format="canonical")
    assert _semantic_execution_signature(base) == _semantic_execution_signature(switched)
    switched["resolved_profile"]["reasoning_effort"] = "high"
    assert _semantic_execution_signature(base) != _semantic_execution_signature(switched)


def test_recovery_identity_accepts_older_codex_profile_without_options():
    old = {"resolved_profile": {"provider": "codex", "model": "gpt-6.1-sol"}, "input_payload": inputs_for(2)}
    current = copy.deepcopy(old)
    current["resolved_profile"]["options"] = {"p1_wire_format": "compact-v1", "translation_wire_format": "cache-v1"}
    assert _semantic_execution_signature(old) == _semantic_execution_signature(current)
    current["resolved_profile"]["options"]["late_usage_wait"] = 1
    assert _semantic_execution_signature(old) != _semantic_execution_signature(current)


class SyntheticCodex(CodexAppServerClient):
    def __init__(self, root, *, wire="cache-v1", invalid_first=False):
        real = client(root, translation_wire_format=wire, p1_wire_format=wire)
        super().__init__(real.settings, Display(True))
        self.calls = []
        self.invalid_first = invalid_first

    def generate(self, body, directory, recorder):
        self.calls.append(body)
        n = body["pass_no"]
        result = RESULTS[n]
        if self.invalid_first and len(self.calls) == 1:
            result = {"translations": []}
        raw = dumps({"payload_json": dumps(result)} if body["wire_format"] == "cache-v1" else result)
        recorder.transport_request(body, body["output_schema"])
        recorder.write_answer(raw)
        return raw, {"provider": "codex", "wire_format": body["wire_format"], "status": "completed",
                     "usage_status": "reported", "finish_reason": "stop", **body.get("cache_diagnostics", {})}


SETTINGS = {"json_retries": 0, "continuity_tokens": 1000, "memory_tokens": 1000,
            "passes": {str(n): {"max_tokens": 1000, "temperature": 0} for n in range(1, 6)}}


@pytest.mark.parametrize("n", range(1, 6))
@pytest.mark.parametrize("first_wire", ["canonical", "cache-v1"])
def test_checkpoints_and_fingerprints_survive_wire_switch(project, n, first_wire):
    store = Store(project)
    first = SyntheticCodex(project, wire=first_wire)
    second = SyntheticCodex(project, wire="canonical" if first_wire == "cache-v1" else "cache-v1")
    inputs = inputs_for(n)
    key = f"pass{n}/" + ("unit1" if n == 1 else "chunk1")
    try:
        runner = Runner(store, first, SETTINGS, Display(True))
        expected = digest({"prompt": PROMPTS[n], "inputs": inputs, "schema": response_schema(n, inputs)})
        value, relative, fingerprint = runner.run(n, key, inputs)
        assert value == RESULTS[n] and fingerprint == expected
        assert read_json(project / relative) == RESULTS[n]
        again = Runner(store, second, SETTINGS, Display(True)).run(n, key, inputs)
        assert again == (value, relative, fingerprint) and second.calls == []
        semantic = read_json(next((project / "artifacts").rglob("request.semantic.json")))
        assert semantic["trusted_instructions"] == PROMPTS[n]
        assert semantic["input_payload"] == inputs and semantic["output_schema"] == response_schema(n, inputs)
        if first_wire == "cache-v1":
            assert read_json(next((project / "artifacts").rglob("decoded.canonical.json"))) == RESULTS[n]
    finally:
        store.close()


def test_retry_keeps_prefix_and_targeted_retry_semantics(project):
    store = Store(project)
    provider = SyntheticCodex(project, invalid_first=True)
    try:
        Runner(store, provider, {**SETTINGS, "json_retries": 1}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(provider.calls) == 2
        assert provider.calls[0]["cache_diagnostics"]["cache_prefix_sha256"] == provider.calls[1]["cache_diagnostics"]["cache_prefix_sha256"]
        retry = json.loads(provider.calls[1]["input"])
        assert "ID coverage mismatch" in retry["VALIDATION_ERROR"]
        assert "Return exactly 1 translations" in retry["RETRY_INSTRUCTION"]
    finally:
        store.close()


def test_failed_canonical_validation_never_checkpoints(project):
    store = Store(project)
    provider = SyntheticCodex(project, invalid_first=True)
    try:
        with pytest.raises(PipelineError, match="failed validation"):
            Runner(store, provider, SETTINGS, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert len(provider.calls) == 1
    finally:
        store.close()


def test_evidence_write_failure_does_not_retry_model(project, monkeypatch):
    store = Store(project)
    provider = SyntheticCodex(project)
    def fail(recorder, value):
        raise EvidenceError("synthetic write failure")
    monkeypatch.setattr(AttemptRecorder, "decoded_canonical", fail)
    try:
        with pytest.raises(EvidenceError, match="synthetic write failure"):
            Runner(store, provider, {**SETTINGS, "json_retries": 2}, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        assert len(provider.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()


def test_pipeline_semantic_dependency_graph_is_unchanged(project):
    store = Store(project)
    block = {**COMMON["SOURCE_BLOCKS"][0], "order": 1, "parent_id": "B1"}
    chunk = {"id": "chunk1", "number": 1, "chapter_id": "ch1", "index_in_chapter": 1,
             "blocks": [block], "sentences": SENTENCES}
    book = {"chunks": [chunk], "chapters": [{"id": "ch1", "number": 1, "chunk_ids": ["chunk1"]}]}
    store.register_chunks(book)
    with store.db:
        store.set("analysis_done", True)
        store.set("approved", True)
    provider = SyntheticCodex(project)
    try:
        translate(store, book, provider, SETTINGS, Display(True), 1)
        semantic = {n: read_json(next((project / "artifacts" / f"pass{n}").rglob("request.semantic.json")))["input_payload"]
                    for n in range(2, 6)}
        common = {name: semantic[2][name] for name in COMMON_FIELDS}
        assert semantic[2] == {**common, "SOURCE_SENTENCES": SENTENCES}
        assert semantic[3] == {**common, "SEMANTIC_AUDIT": AUDIT}
        assert semantic[4] == {**common, "POLISH_DRAFT": DRAFT, "SEMANTIC_AUDIT": AUDIT, "SOURCE_SENTENCES": SENTENCES}
        assert semantic[5] == {**common, "POLISH_DRAFT": DRAFT, "CORRECTION_LEDGER": LEDGER}
        assert store.chunk("chunk1")["status"] == "done"
    finally:
        store.close()


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_completed_attempt_recovery_survives_transport_switch(project, n):
    store = Store(project)
    key = f"pass{n}/" + ("unit1" if n == 1 else "chunk1")
    try:
        first = Runner(store, SyntheticCodex(project), SETTINGS, Display(True)).run(n, key, inputs_for(n))
        # Simulate a crash after durable model evidence but before the receipt.
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        provider = SyntheticCodex(project, wire="canonical")
        recovered = Runner(store, provider, SETTINGS, Display(True)).run(n, key, inputs_for(n))
        assert recovered == first and provider.calls == []
        assert read_json(next((project / "artifacts").rglob("recovery.json")))["source_attempt"].endswith("attempt_001")
    finally:
        store.close()


def test_actual_rpc_evidence_schema_and_independent_runtime(project):
    # Reuse the existing protocol fixture, which exercises interleaved events,
    # unsupported requests, late usage and graceful persisted-rollout flushing.
    from test_transports import _write_fake_codex
    executable = project / "fake-codex"
    _write_fake_codex(executable)
    script = executable.read_text(encoding="utf-8")
    script = script.replace('assert message["params"]["developerInstructions"] == "Trusted pass instructions"',
                            'assert message["params"]["developerInstructions"].startswith("IntelliTex cache-v1")')
    script = script.replace('assert message["params"]["input"][0]["text"] == \'{"SOURCE":"payload"}\'',
                            'assert json.loads(message["params"]["input"][0]["text"])["ACTIVE_PASS"] in (4, 5)')
    script = script.replace('json.dumps({"ok":True}, separators=(",", ":"))',
                            'json.dumps({"payload_json": json.dumps({"translations": []})})')
    executable.write_text(script, encoding="utf-8")
    provider = client(project, late_usage_wait=0.05)
    provider.executable = str(executable)
    provider.settings["runtime_root"] = str(project / "runtimes")
    runtimes = []
    for n in (4, 5):
        attempt = project / f"attempt_{n}"
        recorder = AttemptRecorder(attempt, {"pass_no": n})
        body = body_for(provider, n)
        provider.preflight(body, recorder)
        _, meta = provider.generate(body, attempt, recorder)
        recorder.finish(generation="completed", validation="not_run", metadata=meta)
        plan = read_json(attempt / "request.transport.json")
        events = [json.loads(line) for line in (attempt / "transport.jsonl").read_text(encoding="utf-8").splitlines()]
        rpc = [row["payload"] for row in events if row["direction"] == "outbound" and row["kind"] == "rpc"]
        thread = next(row["params"] for row in rpc if row.get("method") == "thread/start")
        turn = next(row["params"] for row in rpc if row.get("method") == "turn/start")
        assert thread == plan["thread"] and turn == plan["turn"]
        assert turn["outputSchema"] == TRANSPORT_SCHEMA == read_json(attempt / "schema.transport.json")
        assert turn["input"][0]["text"] == body["input"]
        assert meta["cache_prefix_sha256"] == body["cache_diagnostics"]["cache_prefix_sha256"]
        assert read_json(attempt / "cache_layout.json") == body["cache_diagnostics"]
        assert meta["wire_format"] == "cache-v1"
        assert not any(row.get("method") == "thread/resume" for row in rpc)
        assert not any("cache" in field.lower() for field in thread.keys() | turn.keys())
        usage = read_json(attempt / "usage.json")
        assert (usage["input_tokens"], usage["cached_input_tokens"], usage["cache_write_input_tokens"],
                usage["output_tokens"], usage["reasoning_output_tokens"], usage["total_tokens"]) == (11, 3, 2, 5, 4, 16)
        runtimes.append(plan["environment"]["CODEX_HOME"])
    assert runtimes[0] != runtimes[1]
    assert all(not Path(path).exists() for path in runtimes)
