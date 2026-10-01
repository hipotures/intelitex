from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from bookpipe import codex_cache_shared as shared, codex_cache_v2 as v2, p1_compact as p1
from bookpipe.codex_cache import encode_value
from bookpipe.codex_transport import CodexAppServerClient
from bookpipe.engine import Runner, _decode_transport_result, response_schema, translate, validate_result
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, digest, read_json
from test_codex_cache import PROMPTS, RESULTS, SETTINGS, SyntheticCodex, client, inputs_for, project
from test_codex_cache_v2 import OfflineV2, fixture_book, rich_inputs


@pytest.fixture
def tasks(rich_inputs):
    tasks, results = rich_inputs
    tasks[1] = {"SOURCE_BLOCKS": copy.deepcopy(tasks[2]["SOURCE_BLOCKS"]), "SECTION_ID": "chapter",
                "EXISTING_MEMORY": {"catalogue": [{"id": "old-term", "source": "She", "aliases": ["Her"]}],
                    "matched": [{"id": "old-term", "source": "She", "aliases": ["Her"], "chosen": "Ona",
                                 "candidates": [{"text": "Ona", "reason": '“Reason”.\n'}],
                                 "meaning_notes": [{"text": "Unknown.\n", "evidence": ["historical/B"]}]}],
                    "catalogue_incomplete": True}}
    results[1] = {"terms": [{"source": "Żuraw", "aliases": [], "category": "name", "meaning": '“Meaning”.\n',
                            "confidence": "medium", "candidates": [{"text": "Żuraw", "reason": '„Quote”.\n'}], "evidence": ["block/z"]}],
                  "observations": [{"about": ["Żuraw"], "kind": "gender", "statement": "Unknown.\n", "confidence": "low", "evidence": ["block/z"]}]}
    return tasks, results


def provider(root):
    return client(root, p1_wire_format=shared.WIRE_FORMAT, translation_wire_format=shared.WIRE_FORMAT)


def test_prefixes_schema_and_developer_identity_all_five_passes(project, tasks):
    inputs, _ = tasks
    layouts = [shared.encode_input(inputs[n], n)[0] for n in range(1, 6)]
    bodies = [provider(project).body(PROMPTS[n], inputs[n], response_schema(n, inputs[n]), n) for n in range(1, 6)]
    assert len({x.source_prefix.encode() for x in layouts}) == 1
    assert len({b["developer_instructions"].encode() for b in bodies}) == 1
    assert len({encode_value(b["output_schema"]).encode() for b in bodies}) == 1
    assert len({b["cache_diagnostics"]["source_prefix_sha256"] for b in bodies}) == 1
    assert len({x.translation_common_prefix.encode() for x in layouts[1:]}) == 1
    assert len({b["cache_diagnostics"]["translation_common_prefix_sha256"] for b in bodies[1:]}) == 1
    assert layouts[3].draft_prefix == layouts[4].draft_prefix
    assert bodies[3]["cache_diagnostics"]["draft_prefix_sha256"] == bodies[4]["cache_diagnostics"]["draft_prefix_sha256"]
    assert layouts[0].translation_common_prefix is None
    assert "translation_common_prefix_sha256" not in bodies[0]["cache_diagnostics"]
    for n, (layout, body) in enumerate(zip(layouts, bodies), 1):
        diag = body["cache_diagnostics"]
        assert layout.text.startswith(layout.source_prefix)
        assert diag["full_input_sha256"] == digest(body["input"])
        assert diag["full_input_utf8_bytes"] == len(body["input"].encode())
        assert diag["pass_no"] == n and diag["model"] == "gpt-6.1-sol" and diag["effort"] == "low"
        assert diag["developer_instructions_sha256"] == digest(body["developer_instructions"])
        assert diag["transport_output_schema_sha256"] == digest(encode_value(shared.TRANSPORT_SCHEMA))
        assert diag["source_prefix_utf8_bytes"] == len(layout.source_prefix.encode())
        assert body["wire_format"] == shared.WIRE_FORMAT
        assert "CANONICAL_OUTPUT_SCHEMA" not in body["input"] and "payload_json" not in body["input"]


@pytest.mark.parametrize("n", range(1, 6))
@pytest.mark.parametrize("retry", [False, True])
def test_complete_roundtrips_with_unicode_and_presence(tasks, n, retry):
    inputs, results = tasks
    inputs[n]["SOURCE_BLOCKS"].append({"id": "without-scene", "kind": "paragraph", "text": ' e\u0301 „ą”.\n\n'})
    if n in (3, 5):
        results[n]["translations"].append({"id": "without-scene", "text": ' e\u0301 „ą”.\n\n'})
    if n in (4, 5):
        inputs[n]["POLISH_DRAFT"]["translations"].append({"id": "without-scene", "text": ' e\u0301 „ą”.\n\n'})
    if retry:
        inputs[n].update(VALIDATION_ERROR='Quote contains "block/z"', RETRY_INSTRUCTION="original", ALLOWED_EVIDENCE_IDS=["block/z"])
    before = copy.deepcopy(inputs[n])
    layout, ctx = shared.encode_input(inputs[n], n)
    assert shared.decode_input(layout, ctx) == before == inputs[n]
    assert shared.CodecContext.from_dict(ctx.as_dict()) == ctx
    compact = shared.encode_output(results[n], n, ctx)
    assert shared.decode_output(v2.parse_output(encode_value(compact)), n, ctx) == results[n]
    validate_result(n, results[n], inputs[n])
    jsonschema.validate(results[n], response_schema(n, inputs[n]))


def test_exact_order_and_source_only_lookup(tasks):
    inputs, _ = tasks
    for n in range(1, 6):
        layout, _ = shared.encode_input(inputs[n], n)
        wire = json.loads(layout.text)
        fields = list(wire)
        assert fields[:3] == ["CACHE_SHARED_V1", "SOURCE_BLOCKS", "SOURCE_LOOKUP"]
        assert wire["SOURCE_LOOKUP"] == {"k": ["paragraph", "separator"]}
        assert fields[-1] == "ACTIVE_PASS"
        if n == 1:
            assert fields[3:-1] == ["EXISTING_MEMORY", "SECTION_ID"]
            assert "historical/B" not in layout.source_prefix
        else:
            assert fields[3:8] == list(shared.COMMON_ORDER)
            assert fields[8:-1] == list(v2.STAGE_FIELDS[n])
            assert "older-block" not in layout.source_prefix
            assert "older-block" in wire["HISTORICAL_LOOKUP"]
        assert layout.source_prefix.endswith(',')
        assert wire["SOURCE_BLOCKS"][0][:4] == [0, 0, 0, 1]
        assert wire["SOURCE_BLOCKS"][1][:4] == [1, 1, 1, 2]


def test_chapter_vs_smaller_chunk_and_common_changes(tasks):
    inputs, _ = tasks
    chapter = shared.encode_input(inputs[1], 1)[0]
    inputs[1]["SOURCE_BLOCKS"].append({"id": "extra", "kind": "paragraph", "text": "Later."})
    assert shared.encode_input(inputs[1], 1)[0].source_prefix != chapter.source_prefix
    assert shared.encode_input(inputs[2], 2)[0].source_prefix == chapter.source_prefix
    for field in ("APPROVED_LEXICON", "OBSERVATIONS", "PREVIOUS_CONTEXT", "CHUNK_ID"):
        changed = copy.deepcopy(inputs[2])
        if field == "APPROVED_LEXICON": changed[field][0]["polish"] += " x"
        elif field == "OBSERVATIONS": changed[field][0]["statement"] += " x"
        elif field == "PREVIOUS_CONTEXT": changed[field]["english"] += " x"
        else: changed[field] += " x"
        layout, _ = shared.encode_input(changed, 2)
        assert layout.source_prefix == chapter.source_prefix
        assert layout.translation_common_prefix != shared.encode_input(inputs[2], 2)[0].translation_common_prefix


def test_suffix_retry_and_artifact_changes_cannot_renumber_maps_or_prefixes(tasks):
    inputs, _ = tasks
    layouts = {n: shared.encode_input(inputs[n], n)[0] for n in range(1, 6)}
    for n in range(1, 6):
        original_ctx = shared.context_for(inputs[n], n)
        inputs[n].update(VALIDATION_ERROR="Bad canonical B/S IDs", RETRY_INSTRUCTION="retry")
        if n in (3, 4): inputs[n]["SEMANTIC_AUDIT"]["checks"].reverse()
        if n == 5: inputs[n]["CORRECTION_LEDGER"]["checks"].reverse()
        layout, ctx = shared.encode_input(inputs[n], n)
        assert layout.source_prefix == layouts[n].source_prefix
        assert layout.translation_common_prefix == layouts[n].translation_common_prefix
        assert ctx == original_ctx
        if n in (4, 5): assert layout.draft_prefix == layouts[n].draft_prefix
        wire = json.loads(layout.text)
        assert "canonical B/S" not in wire["VALIDATION_ERROR"]
        assert "p/a/o/c/i/e/t" in wire["RETRY_INSTRUCTION"]
        assert shared.decode_input(layout, ctx) == inputs[n]
    for n in (4, 5):
        inputs[n]["POLISH_DRAFT"]["translations"][0]["text"] += "Changed."
        layout, _ = shared.encode_input(inputs[n], n)
        assert layout.common_prefix == layouts[n].common_prefix
        assert layout.draft_prefix != layouts[n].draft_prefix


def test_fixed_relaxed_schema_has_no_dynamic_constraints():
    def check(value):
        if isinstance(value, dict):
            assert not set(value) & {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern", "anyOf", "oneOf", "allOf", "$ref", "$defs", "uniqueItems"}
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            if "enum" in value: assert all(type(x) is int for x in value["enum"])
            for v in value.values(): check(v)
        elif isinstance(value, list):
            for v in value: check(v)
    check(shared.TRANSPORT_SCHEMA)
    assert set(shared.TRANSPORT_SCHEMA["properties"]) == {"p", "a", "o", "c", "i", "e", "t"}
    assert len(encode_value(shared.TRANSPORT_SCHEMA).encode()) == 2758


def test_adapted_p1_contract_and_shared_legend_are_not_contradictory():
    contract = shared.developer_contract(PROMPTS)
    assert "ALL fields p,a,o,c,i,e,t" in contract
    assert "P1 uses a,o" in contract
    assert "SOURCE_LOOKUP.h" not in contract and "BLOCK_KINDS" not in contract
    assert "Root: t=terms" not in contract and "scene-start flag 0/1" not in contract
    assert "HISTORICAL_LOOKUP historical identifiers" in contract
    assert "P1 evidence e is an array of local block indices" in contract


def test_source_and_common_bytes_do_not_depend_on_model_or_effort(project):
    instance = provider(project)
    a = instance.body(PROMPTS[4], inputs_for(4), response_schema(4, inputs_for(4)), 4)
    instance.model = "user-selected-model"
    instance.settings["reasoning_effort"] = "user-selected-effort"
    b = instance.body(PROMPTS[4], inputs_for(4), response_schema(4, inputs_for(4)), 4)
    assert a["input"] == b["input"] and a["developer_instructions"] == b["developer_instructions"]
    assert a["output_schema"] == b["output_schema"]
    for name in ("source", "translation_common", "draft"):
        assert a["cache_diagnostics"][f"{name}_prefix_sha256"] == b["cache_diagnostics"][f"{name}_prefix_sha256"]
    assert b["model"] == "user-selected-model" and b["effort"] == "user-selected-effort"


@pytest.mark.parametrize("n", range(1, 6))
@pytest.mark.parametrize("bad", [True, False, 0.0, -1, 999, "0", None])
def test_invalid_references_and_inactive_arrays(tasks, n, bad):
    inputs, results = tasks
    ctx = shared.context_for(inputs[n], n)
    output = shared.encode_output(results[n], n, ctx)
    if n == 1: output["a"][0]["e"][0] = bad
    elif n in (2, 4): output["c"][0]["s"] = bad
    else: output["t"][0]["b"] = bad
    with pytest.raises((PipelineError, jsonschema.ValidationError)): shared.decode_output(output, n, ctx)
    output = shared.encode_output(results[n], n, ctx)
    output["p"] = (n % 5) + 1
    with pytest.raises(PipelineError, match="pass"): shared.decode_output(output, n, ctx)
    output = shared.encode_output(results[n], n, ctx)
    key = "t" if n == 1 else "a"
    output[key] = [{"b": 0, "t": "extra"}] if n == 1 else shared.encode_output(results[1], 1, ctx)["a"]
    with pytest.raises(PipelineError, match="inactive"): shared.decode_output(output, n, ctx)


@pytest.mark.parametrize("n", range(1, 6))
def test_custom_substantive_rules_preserved_and_unsupported_contracts_rejected(n):
    prompts = copy.deepcopy(PROMPTS)
    rule = "\nKeep the narrator's dry irony and literal braces { and }.\n"
    prompts[n] = rule + prompts[n] + rule
    assert shared.developer_contract(prompts).count(encode_value(rule)[1:-1]) == 2
    for extra in ('\nReturn only a string.', '\nReturn {"surprise":true}.'):
        prompts[n] = PROMPTS[n] + extra
        with pytest.raises(PipelineError, match="contract"): shared.developer_contract(prompts)
    prompts[n] = PROMPTS[n].replace('"source block ID"' if n == 1 else ('"supplied sentence ID"' if n in (2, 4) else '"unchanged source block ID"'), '"custom ID"')
    with pytest.raises(PipelineError): shared.developer_contract(prompts)


class OfflineShared(CodexAppServerClient):
    def __init__(self, root, invalid=None):
        real = provider(root)
        super().__init__(real.settings, Display(True))
        self.calls, self.invalid = [], invalid

    def generate(self, body, directory, recorder):
        self.calls.append(body)
        n = body["pass_no"]
        compact = shared.encode_output(copy.deepcopy(RESULTS[n]), n, shared.CodecContext.from_dict(body["codec_context"]))
        if self.invalid and len(self.calls) == 1: compact = self.invalid(compact)
        raw = compact if isinstance(compact, str) else encode_value(compact)
        recorder.transport_request({k: v for k, v in body.items() if k != "codec_context"}, body["output_schema"])
        recorder.write_answer(raw)
        return raw, {"wire_format": shared.WIRE_FORMAT, "provider": "codex", "finish_reason": "stop", "status": "completed",
                     "usage_status": "reported", **body["cache_diagnostics"]}


@pytest.mark.parametrize("n", range(1, 6))
def test_canonical_validation_evidence_checkpoints_and_recovery(project, n):
    store = Store(project)
    old = OfflineShared(project)
    try:
        runner = Runner(store, old, SETTINGS, Display(True))
        inputs = inputs_for(n)
        expected_fp = digest({"prompt": PROMPTS[n], "inputs": inputs, "schema": response_schema(n, inputs)})
        assert runner.fingerprint(n, inputs) == expected_fp
        accepted = runner.run(n, f"pass{n}/chunk1", inputs)
        path = next((project / "artifacts").rglob("decoded.canonical.json")).parent
        assert read_json(path / "decoded.canonical.json") == RESULTS[n]
        assert read_json(path / "schema.canonical.json") == response_schema(n, inputs)
        assert read_json(path / "codec.context.json")["wire_format"] == shared.WIRE_FORMAT
        assert read_json(path / "validation.json")["final_validation"] == "passed"
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        new = OfflineV2(project, "canonical") if n > 1 else OfflineShared(project)
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs) == accepted
        assert new.calls == []
    finally: store.close()


@pytest.mark.parametrize("n", range(2, 6))
@pytest.mark.parametrize("wire", ["canonical", "cache-v1", "cache-v2"])
def test_old_translation_attempts_recover_into_shared_without_inference(project, n, wire):
    store = Store(project)
    try:
        old = Runner(store, OfflineV2(project, wire), SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n))
        new = OfflineShared(project)
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n)) == old
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n)) == old
        assert not new.calls
    finally: store.close()


@pytest.mark.parametrize("wire", ["canonical", "compact-v1", "cache-v1"])
def test_old_p1_completed_attempts_recover_without_inference(project, wire):
    class OldP1(SyntheticCodex):
        def generate(self, body, directory, recorder):
            if body["wire_format"] != "compact-v1":
                return super().generate(body, directory, recorder)
            self.calls.append(body)
            encoded = shared.encode_output(RESULTS[1], 1, shared.context_for(inputs_for(1), 1))
            raw = encode_value({"t": encoded["a"], "o": encoded["o"]})
            recorder.transport_request(body, body["output_schema"])
            recorder.write_answer(raw)
            return raw, {"wire_format": wire, "provider": "codex", "finish_reason": "stop", "usage_status": "reported"}
    store = Store(project)
    try:
        old = Runner(store, OldP1(project, wire=wire), SETTINGS, Display(True)).run(1, "pass1/unit", inputs_for(1))
        new = OfflineShared(project)
        assert Runner(store, new, SETTINGS, Display(True)).run(1, "pass1/unit", inputs_for(1)) == old
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        assert Runner(store, new, SETTINGS, Display(True)).run(1, "pass1/unit", inputs_for(1)) == old
        assert not new.calls
    finally: store.close()


@pytest.mark.parametrize("n,mutate", [
    (1, lambda v: v["terms"][0].update(candidates=[])),
    (1, lambda v: v["terms"][0].update(source="Unattested invented name")),
    (1, lambda v: v["observations"][0].update(about=[])),
    (2, lambda v: v["checks"].pop()), (2, lambda v: v["checks"].append(v["checks"][0])),
    (2, lambda v: v["issues"][0].update(source_span="unsupported words")),
    (4, lambda v: v["checks"][0].update(status="ok")),
    (4, lambda v: v["corrections"][0].update(draft_span="unsupported draft words")),
    (3, lambda v: v["translations"].reverse()), (5, lambda v: v["translations"].pop()),
    (5, lambda v: v["translations"][0].update(text=" ")),
])
def test_decoded_results_still_require_all_canonical_constraints(tasks, n, mutate):
    inputs, results = tasks
    ctx = shared.context_for(inputs[n], n)
    compact = shared.encode_output(results[n], n, ctx)
    decoded = shared.decode_output(compact, n, ctx)
    mutate(decoded)
    with pytest.raises((PipelineError, jsonschema.ValidationError)):
        jsonschema.validate(decoded, response_schema(n, inputs[n]))
        validate_result(n, decoded, inputs[n])


@pytest.mark.parametrize("n", range(1, 6))
def test_corrupted_versioned_maps_are_local_errors_without_retry(tasks, n):
    inputs, results = tasks
    ctx = shared.context_for(inputs[n], n)
    wire = shared.encode_output(results[n], n, ctx)
    for field, bad in (("map_version", True), ("map_version", 99), ("wire_format", "cache-v2"), ("block_ids", ["corrupt"])):
        saved = ctx.as_dict()
        saved[field] = bad
        with pytest.raises(EvidenceError):
            _decode_transport_result(wire, shared.WIRE_FORMAT, inputs[n], saved, expected_pass=n)


@pytest.mark.parametrize("n", range(1, 6))
def test_dictionary_order_and_schema_size_independent_of_source_length(tasks, n):
    inputs, _ = tasks
    def reverse(value):
        if isinstance(value, dict): return {k: reverse(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list): return [reverse(v) for v in value]
        return value
    assert shared.encode_input(inputs[n], n)[0].text == shared.encode_input(reverse(inputs[n]), n)[0].text
    schema = encode_value(shared.TRANSPORT_SCHEMA)
    inputs[n]["SOURCE_BLOCKS"][0]["text"] += " Large source. " * 500
    shared.encode_input(inputs[n], n)
    assert encode_value(shared.TRANSPORT_SCHEMA) == schema


def test_all_reference_domains_are_independent_even_when_strings_collide(tasks):
    inputs, _ = tasks
    task = inputs[4]
    task["SOURCE_BLOCKS"][0]["scene_id"] = "block/z"
    task["APPROVED_LEXICON"][0]["id"] = "block/z"
    task["SOURCE_SENTENCES"][0]["id"] = "block/z"
    task["SEMANTIC_AUDIT"]["checks"][0]["sid"] = "block/z"
    task["SEMANTIC_AUDIT"]["issues"][0]["sid"] = "block/z"
    layout, ctx = shared.encode_input(task, 4)
    assert ctx.block_ids[0] == ctx.scene_ids[0] == ctx.term_ids[0] == ctx.sentence_ids[0] == "block/z"
    assert shared.decode_input(layout, ctx) == task


def test_offline_size_report_cannot_write_into_project_and_preserves_artifacts(project, tmp_path):
    from experiments.codex_cache_ab import shared_sizes
    store = Store(project)
    try:
        for n in range(1, 6):
            Runner(store, OfflineShared(project), SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n))
        files = {p: p.read_bytes() for p in (project / "artifacts").rglob("*") if p.is_file()}
        report = shared_sizes(project, "chunk1", "chunk1", tmp_path / "offline-sizes")
        assert report["live_model_calls"] == 0 and len(report["rows"]) == 10
        assert all(row["roundtrip_valid"] for row in report["rows"])
        assert files == {p: p.read_bytes() for p in (project / "artifacts").rglob("*") if p.is_file()}
        with pytest.raises(PipelineError, match="outside"):
            shared_sizes(project, "chunk1", "chunk1", project / "bad-report")
        assert not (project / "bad-report").exists()
    finally: store.close()


@pytest.mark.parametrize("bad", [lambda v: {**v, "t": []}, lambda v: {**v, "t": v["t"] * 2},
                                  lambda v: '{"p":3,"p":3}', lambda v: '{"p":NaN}', lambda v: '{"p":3'])
def test_malformed_or_incomplete_response_never_checkpointed(project, bad):
    store = Store(project)
    instance = OfflineShared(project, invalid=bad)
    try:
        with pytest.raises(PipelineError, match="failed validation"):
            Runner(store, instance, SETTINGS, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(instance.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


def test_shared_retry_and_repaired_artifact_input(project):
    store = Store(project)
    instance = OfflineShared(project, invalid=lambda v: {**v, "i": [{"s": 0, "x": "Żuraw … waits", "k": 1, "m": "Meaning", "c": "Keep", "q": 1}]})
    try:
        result, _, _ = Runner(store, instance, SETTINGS, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        assert result["issues"][0]["source_span"] == "Żuraw waits"
        task = inputs_for(3)
        task["SEMANTIC_AUDIT"] = result
        layout, ctx = shared.encode_input(task, 3)
        assert shared.decode_input(layout, ctx)["SEMANTIC_AUDIT"] == result
        retry = OfflineShared(project, invalid=lambda v: {**v, "t": []})
        Runner(store, retry, {**SETTINGS, "json_retries": 1}, Display(True)).run(3, "pass3/chunk1", task)
        assert len(retry.calls) == 2
        assert retry.calls[0]["cache_diagnostics"]["translation_common_prefix_sha256"] == retry.calls[1]["cache_diagnostics"]["translation_common_prefix_sha256"]
    finally: store.close()


@pytest.mark.parametrize("method", ["codec_context", "decoded_canonical", "validation"])
def test_evidence_failure_never_causes_model_retry(project, monkeypatch, method):
    store = Store(project)
    instance = OfflineShared(project)
    monkeypatch.setattr(AttemptRecorder, method, lambda *a: (_ for _ in ()).throw(EvidenceError("disk")))
    try:
        with pytest.raises(EvidenceError): Runner(store, instance, {**SETTINGS, "json_retries": 2}, Display(True)).run(1, "pass1/unit", inputs_for(1))
        assert len(instance.calls) == (0 if method == "codec_context" else 1)
    finally: store.close()


def test_options_fail_closed_and_defaults_unchanged(project):
    instance = provider(project)
    validate_profiles({"profiles": {"test": instance.resolved_profile}, "default_profile": "test"})
    settings = {"profiles": {}, "default_profile": "codex-sol-high", "passes": SETTINGS["passes"]}
    _, default, _ = resolve_profile(settings, 1, project=project)
    assert default["options"]["p1_wire_format"] == "compact-v1"
    assert default["options"]["translation_wire_format"] == "cache-v2"
    for option in ("p1_wire_format", "translation_wire_format"):
        for bad in ("future-v1", None, [], 3):
            instance.settings["options"][option] = bad
            with pytest.raises(PipelineError): instance.body(PROMPTS[1 if option == "p1_wire_format" else 2], inputs_for(1 if option == "p1_wire_format" else 2), {}, 1 if option == "p1_wire_format" else 2)
            with pytest.raises(PipelineError): validate_profiles({"profiles": {"bad": instance.settings}, "default_profile": "bad"})
            instance.settings["options"][option] = shared.WIRE_FORMAT


def test_non_codex_transports_still_send_canonical_inputs_and_schema(monkeypatch):
    from bookpipe.client import Client
    from bookpipe.openai_transport import OpenAIResponsesClient
    from bookpipe.vllm_transport import VLLMClient
    monkeypatch.setenv("TEST_OFFLINE_OPENAI_KEY", "test-placeholder")
    settings = {"profile_name": "test", "resolved_profile": {}, "model": "model", "context_size": 100000,
                "planning_output_reserve": 1000, "max_output_tokens": None, "request_timeout": 1,
                "endpoint": "http://127.0.0.1:9/v1", "credential_env": "TEST_OFFLINE_OPENAI_KEY",
                "passes": SETTINGS["passes"], "options": {}}
    for cls in (Client, OpenAIResponsesClient, VLLMClient):
        a = cls(copy.deepcopy(settings), Display(True), settings) if cls is VLLMClient else cls(copy.deepcopy(settings), Display(True))
        try:
            for n in range(1, 6):
                args = (PROMPTS[n], inputs_for(n), response_schema(n, inputs_for(n)), n)
                body = a.body(*args)
                if cls is OpenAIResponsesClient:
                    assert body["instructions"] == PROMPTS[n]
                    assert json.loads(body["input"][0]["content"][0]["text"]) == inputs_for(n)
                    assert body["text"]["format"]["schema"] == args[2]
                else:
                    assert body["messages"][0]["content"] == PROMPTS[n]
                    assert json.loads(body["messages"][1]["content"]) == inputs_for(n)
                    assert body["response_format"].get("schema", body["response_format"].get("json_schema", {}).get("schema")) == args[2]
        finally:
            a.close()


def test_full_pipeline_and_targeted_paths_keep_dependencies(project):
    from bookpipe.application.pipeline import execute_target_pass
    store = Store(project)
    book = fixture_book(store)
    instance = OfflineShared(project)
    try:
        translate(store, book, instance, SETTINGS, Display(True), 1)
        assert [b["pass_no"] for b in instance.calls] == [2, 3, 4, 5]
        for n in range(2, 6):
            wire = json.loads(instance.calls[n-2]["input"])
            assert ("SOURCE_SENTENCES" in wire) == (n in (2, 4))
            assert ("SEMANTIC_AUDIT" in wire) == (n in (3, 4))
            assert ("POLISH_DRAFT" in wire) == (n in (4, 5))
            assert ("CORRECTION_LEDGER" in wire) == (n == 5)
        execute_target_pass(store, book, instance, SETTINGS, Display(True), "chunk1", 4, True)
        execute_target_pass(store, book, instance, SETTINGS, Display(True), "chunk1", 5, True)
        assert [b["pass_no"] for b in instance.calls] == [2, 3, 4, 5, 4, 5]
    finally: store.close()


def test_targeted_p5_accepts_mixed_canonical_v1_v2_history(project):
    from bookpipe.application.pipeline import execute_target_pass
    from bookpipe.engine import previous_context, source_blocks
    store = Store(project)
    book = fixture_book(store)
    instance = OfflineShared(project)
    try:
        chunk = book["chunks"][0]
        context = previous_context(store, book, chunk, instance, 1000)
        memory, _ = store.translation_memory(chunk, context["english"], len, 1000)
        common = {"CHUNK_ID": "chunk1", "SOURCE_BLOCKS": source_blocks(chunk["blocks"]), **memory, "PREVIOUS_CONTEXT": context}
        Runner(store, OfflineV2(project, "canonical"), SETTINGS, Display(True)).run(2, "pass2/chunk1", {**common, "SOURCE_SENTENCES": chunk["sentences"]})
        Runner(store, OfflineV2(project, "cache-v1"), SETTINGS, Display(True)).run(3, "pass3/chunk1", {**common, "SEMANTIC_AUDIT": RESULTS[2]})
        Runner(store, OfflineV2(project, "cache-v2"), SETTINGS, Display(True)).run(4, "pass4/chunk1", {**common, "SOURCE_SENTENCES": chunk["sentences"], "POLISH_DRAFT": RESULTS[3], "SEMANTIC_AUDIT": RESULTS[2]})
        execute_target_pass(store, book, instance, SETTINGS, Display(True), "chunk1", 5, False)
        assert [b["pass_no"] for b in instance.calls] == [5]
        wire = json.loads(instance.calls[0]["input"])
        assert "SEMANTIC_AUDIT" not in wire and "SOURCE_SENTENCES" not in wire
        assert store.chunk("chunk1")["status"] == "done"
    finally: store.close()


def test_interrupted_shared_attempt_never_retries_or_checkpoints(project, monkeypatch):
    store = Store(project)
    instance = OfflineShared(project)
    def interrupt(body, directory, recorder):
        instance.calls.append(body)
        recorder.append_text("answer.partial.txt", '{"p":3')
        raise PipelineError("Codex turn ended with status interrupted.")
    monkeypatch.setattr(instance, "generate", interrupt)
    try:
        with pytest.raises(PipelineError, match="interrupted"):
            Runner(store, instance, {**SETTINGS, "json_retries": 2}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(instance.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


@pytest.mark.parametrize("n", range(1, 6))
def test_actual_rpc_evidence_has_fixed_schema_and_no_maps(project, n):
    from test_transports import _write_fake_codex
    executable = project / "fake-codex"
    _write_fake_codex(executable)
    script = executable.read_text().replace('assert message["params"]["developerInstructions"] == "Trusted pass instructions"',
                                            'assert message["params"]["developerInstructions"].startswith("IntelliTex cache-shared-v1")')
    script = script.replace('assert message["params"]["input"][0]["text"] == \'{"SOURCE":"payload"}\'',
                            'assert "CACHE_SHARED_V1" in message["params"]["input"][0]["text"]')
    executable.write_text(script)
    instance = provider(project)
    instance.executable = str(executable)
    instance.settings["runtime_root"] = str(project / "runtimes")
    instance.settings["options"]["late_usage_wait"] = 0.01
    directory = project / f"wire{n}"
    recorder = AttemptRecorder(directory, {"pass_no": n})
    body = instance.body(PROMPTS[n], inputs_for(n), response_schema(n, inputs_for(n)), n)
    instance.preflight(body, recorder)
    _, meta = instance.generate(body, directory, recorder)
    plan = read_json(directory / "request.transport.json")
    events = [json.loads(line)["payload"] for line in (directory / "transport.jsonl").read_text().splitlines()
              if json.loads(line)["kind"] == "rpc" and json.loads(line)["direction"] == "outbound"]
    assert [r["params"] for r in events if r.get("method") == "thread/start"] == [plan["thread"]]
    assert [r["params"] for r in events if r.get("method") == "turn/start"] == [plan["turn"]]
    assert not any(r.get("method") in ("thread/fork", "thread/resume") for r in events)
    assert plan["turn"]["input"][0]["text"] == body["input"]
    assert plan["turn"]["outputSchema"] == shared.TRANSPORT_SCHEMA == read_json(directory / "schema.transport.json")
    assert read_json(directory / "codec.context.json") == body["codec_context"]
    assert "codec_context" not in plan and "cache_diagnostics" not in plan["thread"] and "cache_diagnostics" not in plan["turn"]
    assert meta["wire_format"] == shared.WIRE_FORMAT
    assert all(k in read_json(directory / "usage.json") for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"))
