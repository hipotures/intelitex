from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from bookpipe import codex_cache_v2 as v2
from bookpipe.codex_cache import encode_value
from bookpipe.codex_transport import CodexAppServerClient
from bookpipe.engine import Runner, response_schema, translate, validate_result
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, digest, read_json
from test_codex_cache import PROMPTS, SETTINGS, RESULTS, SyntheticCodex, client, inputs_for, project


@pytest.fixture
def rich_inputs():
    common = {
        "CHUNK_ID": "Unicode chunk", "SOURCE_BLOCKS": [
            {"id": "block/z", "kind": "paragraph", "scene_id": "scene-a", "scene_start": False, "text": '“She waits.”\n<i>Żuraw</i> — 23.\n'},
            {"id": "block/a", "kind": "separator", "scene_id": "scene-b", "scene_start": True, "text": "* * *"}],
        "APPROVED_LEXICON": [{"id": "term-a", "source": "Żuraw", "aliases": ["Żurawia"], "polish": "Żuraw",
            "category": "name", "meaning_notes": [{"text": 'Identity, not a “crane”.\n', "confidence": "high",
                "evidence": ["block/z", "earlier-block", {"block_id": "other-book/B", "chapter_id": "older-chapter", "order": 3, "excerpt": '“Exact\nquote.”'}],
                "series_inherited": True, "series_first_seen_volume": 1}]}],
        "OBSERVATIONS": [{"about": ["Żuraw"], "kind": "gender", "statement": "Gender remains unknown.\nDo not guess!",
            "confidence": "medium", "evidence": ["older-block"], "chapter_id": "chapter-before", "available_from_order": 9,
            "series_inherited": False, "series_first_seen_volume": 2}],
        "PREVIOUS_CONTEXT": {"source_chunk_id": "prior-chunk", "english": '  Previous “text”.\n', "polish": '  Poprzedni „tekst”.\n'},
    }
    sentences = [{"id": "sentence/z", "block_id": "block/z", "text": '“She waits.”'},
                 {"id": "sentence/a", "block_id": "block/z", "text": "<i>Żuraw</i> — 23."}]
    audit = {"checks": [{"sid": s["id"], "risk": "medium"} for s in sentences], "issues": [
        {"sid": "sentence/z", "source_span": "She", "type": "reference", "meaning": "Known referent.", "constraint": "Retain gender.", "confidence": "high"}]}
    draft = {"translations": [{"id": "block/z", "text": '„Ona czeka”.\n<i>Żuraw</i> — 23.\n'}, {"id": "block/a", "text": "* * *"}]}
    ledger = {"checks": [{"sid": "sentence/z", "status": "needs_correction"}, {"sid": "sentence/a", "status": "ok"}],
        "corrections": [{"sid": "sentence/z", "block_id": "block/z", "source_span": "waits", "draft_span": "czeka",
                         "problem": "Punctuation.", "constraint": "Keep exact quote boundary.", "severity": "minor", "confidence": "low"}]}
    stages = {2: {"SOURCE_SENTENCES": sentences}, 3: {"SEMANTIC_AUDIT": audit},
              4: {"POLISH_DRAFT": draft, "SEMANTIC_AUDIT": audit, "SOURCE_SENTENCES": sentences},
              5: {"POLISH_DRAFT": draft, "CORRECTION_LEDGER": ledger}}
    return {n: copy.deepcopy({**common, **stages[n]}) for n in range(2, 6)}, {2: audit, 3: draft, 4: ledger, 5: draft}


@pytest.mark.parametrize("n", [2, 3, 4, 5])
@pytest.mark.parametrize("retry", [False, True])
def test_complete_input_and_output_roundtrip(rich_inputs, n, retry):
    tasks, results = rich_inputs
    inputs = tasks[n]
    if retry:
        inputs.update(VALIDATION_ERROR='Invalid quote “block/z”.', RETRY_INSTRUCTION="Original diagnostic detail.", ALLOWED_EVIDENCE_IDS=["block/z"])
    before = copy.deepcopy(inputs)
    layout, context = v2.encode_input(inputs, n)
    assert v2.decode_input(layout, context) == inputs == before
    assert v2.CodecContext.from_dict(context.as_dict()) == context
    wire = v2.encode_output(results[n], n, context)
    assert v2.decode_output(v2.parse_output(encode_value(wire)), n, context) == results[n]
    validate_result(n, results[n], inputs)
    jsonschema.validate(results[n], response_schema(n, inputs))
    assert "block/z" not in layout.text
    assert "older-block" in layout.text  # Historical identity remains explicit.


def test_common_draft_prefixes_and_stable_maps(rich_inputs):
    tasks, _ = rich_inputs
    layouts = [v2.encode_input(tasks[n], n)[0] for n in range(2, 6)]
    assert len({x.common_prefix.encode() for x in layouts}) == 1
    assert len({digest(x.common_prefix) for x in layouts}) == 1
    assert layouts[2].draft_prefix == layouts[3].draft_prefix
    contexts = [v2.encode_input(tasks[n], n)[1] for n in range(2, 6)]
    assert all(ctx == contexts[0] for ctx in contexts)
    for n, field in ((3, "SEMANTIC_AUDIT"), (4, "SEMANTIC_AUDIT"), (5, "CORRECTION_LEDGER")):
        changed = copy.deepcopy(tasks[n])
        changed[field]["checks"].reverse()
        layout, ctx = v2.encode_input(changed, n)
        assert ctx == contexts[0]
        assert layout.common_prefix == layouts[0].common_prefix
        assert v2.decode_input(layout, ctx) == changed
    for n in (4, 5):
        tasks[n]["POLISH_DRAFT"]["translations"][0]["text"] += "Changed draft."
        layout, _ = v2.encode_input(tasks[n], n)
        assert layout.common_prefix == layouts[0].common_prefix
        assert layout.draft_prefix != layouts[2].draft_prefix
    for n in range(2, 6):
        tasks[n].update(VALIDATION_ERROR="different", RETRY_INSTRUCTION="retry")
        assert v2.encode_input(tasks[n], n)[0].common_prefix == layouts[0].common_prefix


@pytest.mark.parametrize("field", v2.COMMON_FIELDS)
def test_common_changes_change_prefix(rich_inputs, field):
    tasks, _ = rich_inputs
    original = v2.encode_input(tasks[4], 4)[0]
    changed = copy.deepcopy(tasks[4])
    if field == "SOURCE_BLOCKS": changed[field][0]["text"] += "Source."
    elif field == "APPROVED_LEXICON": changed[field][0]["polish"] += " term"
    elif field == "OBSERVATIONS": changed[field][0]["statement"] += " observation"
    elif field == "PREVIOUS_CONTEXT": changed[field]["english"] += " context"
    else: changed[field] += " chunk"
    assert digest(v2.encode_input(changed, 4)[0].common_prefix) != digest(original.common_prefix)


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_provider_contract_schema_and_no_canonical_schema_payload(project, n):
    provider = client(project, translation_wire_format="cache-v2", p1_wire_format="compact-v1")
    bodies = [provider.body(PROMPTS[x], inputs_for(x), response_schema(x, inputs_for(x)), x) for x in range(2, 6)]
    assert len({b["developer_instructions"] for b in bodies}) == 1
    assert len({encode_value(b["output_schema"]) for b in bodies}) == 1
    b = bodies[n - 2]
    assert b["output_schema"] == v2.TRANSPORT_SCHEMA
    assert "CANONICAL_OUTPUT_SCHEMA" not in b["input"] and "payload_json" not in b["input"]
    assert "PASS 1" not in b["developer_instructions"]
    assert list(json.loads(b["input"]))[:7] == ["CACHE_V2", "SOURCE_BLOCKS", "SOURCE_LOOKUP", "APPROVED_LEXICON", "OBSERVATIONS", "PREVIOUS_CONTEXT", "CHUNK_ID"]
    assert list(json.loads(b["input"]))[-1] == "ACTIVE_PASS"
    diag = b["cache_diagnostics"]
    assert diag["input_utf8_bytes"] == len(b["input"].encode())
    assert diag["codec_map_version"] == 1
    assert len({b["cache_diagnostics"]["cache_identity_sha256"] for b in bodies}) == 1
    def check(value):
        if isinstance(value, dict):
            assert not set(value) & {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern", "anyOf", "oneOf", "allOf", "$ref", "$defs", "uniqueItems"}
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            if "enum" in value: assert all(type(x) is int for x in value["enum"])
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(b["output_schema"])


@pytest.mark.parametrize("table", [v2.RISKS, v2.STATUSES, v2.ISSUE_TYPES, v2.SEVERITIES, v2.CONFIDENCES, v2.CATEGORIES, v2.OBSERVATION_KINDS])
def test_every_code_mapping(table):
    for code, canonical in table.items():
        assert v2._encode_code(canonical, table, "test") == code
        assert v2._code(code, table, "test") == canonical
    for bad in (-1, 999, True, False, 1.0, "1", None):
        with pytest.raises(PipelineError): v2._code(bad, table, "test")


@pytest.mark.parametrize("bad", [True, False, 0.0, 1.5, -1, 2, "0", None])
@pytest.mark.parametrize("n,field", [(2, "c"), (4, "c"), (4, "e"), (3, "t"), (5, "t")])
def test_invalid_references(rich_inputs, n, field, bad):
    tasks, results = rich_inputs
    _, ctx = v2.encode_input(tasks[n], n)
    wire = v2.encode_output(results[n], n, ctx)
    wire[field][0]["b" if field == "t" else "s"] = bad
    with pytest.raises((PipelineError, jsonschema.ValidationError)):
        v2.decode_output(wire, n, ctx)


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_wrong_pass_and_inactive_arrays(rich_inputs, n):
    tasks, results = rich_inputs
    _, ctx = v2.encode_input(tasks[n], n)
    wire = v2.encode_output(results[n], n, ctx)
    wire["p"] = 3 if n != 3 else 5
    with pytest.raises(PipelineError, match="expected pass"): v2.decode_output(wire, n, ctx)
    wire["p"] = n
    wire["t" if n in (2, 4) else "c"] = [{"b": 0, "t": "extra"}] if n in (2, 4) else [{"s": 0, "v": 0}]
    with pytest.raises(PipelineError, match="inactive"): v2.decode_output(wire, n, ctx)


def test_p4_rejects_p2_only_check_code(rich_inputs):
    tasks, results = rich_inputs
    _, ctx = v2.encode_input(tasks[4], 4)
    wire = v2.encode_output(results[4], 4, ctx)
    wire["c"][0]["v"] = 2
    jsonschema.validate(wire, v2.TRANSPORT_SCHEMA)
    with pytest.raises(PipelineError, match="check code"): v2.decode_output(wire, 4, ctx)


@pytest.mark.parametrize("raw", ['{"p":3,"p":3}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":', '```json\n{}\n```'])
def test_strict_json(raw):
    with pytest.raises((PipelineError, json.JSONDecodeError)): v2.parse_output(raw)


@pytest.mark.parametrize("mutate", [
    lambda i: i["SOURCE_BLOCKS"].append(copy.deepcopy(i["SOURCE_BLOCKS"][0])),
    lambda i: i["SOURCE_BLOCKS"][0].update(unknown="no"),
    lambda i: i["SOURCE_SENTENCES"].append(copy.deepcopy(i["SOURCE_SENTENCES"][0])),
    lambda i: i["SOURCE_SENTENCES"][0].update(block_id="unknown"),
    lambda i: i["SEMANTIC_AUDIT"]["checks"].pop(),
    lambda i: i["APPROVED_LEXICON"][0].update(unknown="no"),
    lambda i: i["APPROVED_LEXICON"][0]["meaning_notes"][0].update(unknown="no"),
    lambda i: i["OBSERVATIONS"][0].update(unknown="no"),
    lambda i: i["PREVIOUS_CONTEXT"].update(unknown="no"),
])
def test_unknown_and_inconsistent_inputs_fail_before_inference(rich_inputs, mutate):
    tasks, _ = rich_inputs
    mutate(tasks[4])
    with pytest.raises(PipelineError): v2.encode_input(tasks[4], 4)


def test_custom_prompt_preservation_and_fail_closed():
    prompts = {n: PROMPTS[n] for n in range(2, 6)}
    prompts[3] = "Custom opening rule.\n" + prompts[3] + "\nPreserve every ellipsis and image.\n"
    developer = v2.developer_contract(prompts)
    assert "Custom opening rule." in developer and "Preserve every ellipsis and image." in developer
    for n in prompts:
        assert "STOP" in developer
        with pytest.raises(PipelineError, match="contract"):
            v2.compact_prompt(prompts[n].replace(v2._CONTRACTS[n], '{}'), n)
    with pytest.raises(PipelineError, match="additional"):
        v2.compact_prompt(PROMPTS[3] + '\nReturn {"translations":[]} instead.', 3)


class OfflineV2(CodexAppServerClient):
    def __init__(self, root, wire="cache-v2", invalid=None):
        provider = client(root, translation_wire_format=wire, p1_wire_format="compact-v1")
        super().__init__(provider.settings, Display(True))
        self.calls = []
        self.invalid = invalid

    def generate(self, body, directory, recorder):
        self.calls.append(body)
        n = body["pass_no"]
        result = copy.deepcopy(RESULTS[n])
        if body["wire_format"] == "cache-v2":
            result = v2.encode_output(result, n, v2.CodecContext.from_dict(body["codec_context"]))
        elif body["wire_format"] == "cache-v1":
            result = {"payload_json": encode_value(result)}
        if self.invalid and len(self.calls) == 1:
            result = self.invalid(result)
        raw = result if isinstance(result, str) else encode_value(result)
        recorder.transport_request({k: v for k, v in body.items() if k != "codec_context"}, body["output_schema"])
        recorder.write_answer(raw)
        return raw, {"provider": "codex", "wire_format": body["wire_format"], "finish_reason": "stop", "status": "completed",
                     "usage_status": "reported", **body.get("cache_diagnostics", {})}


@pytest.mark.parametrize("n", [2, 3, 4, 5])
@pytest.mark.parametrize("old", ["canonical", "cache-v1", "cache-v2"])
def test_fingerprints_checkpoints_and_completed_attempt_recovery(project, n, old):
    store = Store(project)
    try:
        first = OfflineV2(project, old)
        runner = Runner(store, first, SETTINGS, Display(True))
        fingerprint = runner.fingerprint(n, inputs_for(n))
        assert fingerprint == digest({"prompt": PROMPTS[n], "inputs": inputs_for(n), "schema": response_schema(n, inputs_for(n))})
        accepted = runner.run(n, f"pass{n}/chunk1", inputs_for(n))
        new = OfflineV2(project)
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n)) == accepted
        assert new.calls == []
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f"pass{n}/chunk1", inputs_for(n)) == accepted
        assert new.calls == []
        if old == "cache-v2":
            assert read_json(next((project / "artifacts").rglob("codec.context.json")))["map_version"] == 1
            assert read_json(next((project / "artifacts").rglob("decoded.canonical.json"))) == RESULTS[n]
    finally:
        store.close()


def test_retry_local_indices_preserves_prefix_and_original_diagnostics(project):
    provider = OfflineV2(project, invalid=lambda v: {**v, "t": []})
    store = Store(project)
    try:
        Runner(store, provider, {**SETTINGS, "json_retries": 1}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(provider.calls) == 2
        assert provider.calls[0]["cache_diagnostics"]["cache_prefix_sha256"] == provider.calls[1]["cache_diagnostics"]["cache_prefix_sha256"]
        wire = json.loads(provider.calls[1]["input"])
        assert "B1" not in wire["RETRY_INSTRUCTION"]
        assert "integer" not in wire["VALIDATION_ERROR"] or "index" in wire["VALIDATION_ERROR"]
        assert "Block b indices: 0 through 0" in wire["RETRY_INSTRUCTION"]
        semantic = read_json(sorted((project / "artifacts").rglob("request.semantic.json"))[-1])
        # Full local schema diagnostic retains canonical IDs; the provider
        # receives concise compact feedback instead of this verbose detail.
        assert "B1" in semantic["input_payload"]["VALIDATION_ERROR"]
        assert "minItems" in semantic["input_payload"]["VALIDATION_ERROR"]
        assert semantic["input_payload"]["RETRY_INSTRUCTION"] != wire["RETRY_INSTRUCTION"]
    finally: store.close()


@pytest.mark.parametrize("failure", [
    lambda v: {**v, "t": []}, lambda v: {**v, "t": v["t"] * 2},
    lambda v: {**v, "t": [{"b": 0, "t": " "}]}, lambda v: '{"p":3,"p":3}',
])
def test_invalid_result_never_checkpointed(project, failure):
    store = Store(project)
    provider = OfflineV2(project, invalid=failure)
    try:
        with pytest.raises(PipelineError, match="failed validation"):
            Runner(store, provider, SETTINGS, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert len(provider.calls) == 1
    finally: store.close()


def test_evidence_failure_never_retries(project, monkeypatch):
    store = Store(project)
    provider = OfflineV2(project)
    monkeypatch.setattr(AttemptRecorder, "decoded_canonical", lambda *a: (_ for _ in ()).throw(EvidenceError("disk failure")))
    try:
        with pytest.raises(EvidenceError):
            Runner(store, provider, {**SETTINGS, "json_retries": 2}, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        assert len(provider.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


def test_global_shared_default_and_p1_v2_rejected(project):
    provider = OfflineV2(project)
    validate_profiles({"profiles": {"test": provider.resolved_profile}, "default_profile": "test"})
    settings = {"profiles": {}, "default_profile": "codex-sol-high", "passes": SETTINGS["passes"]}
    _, resolved, _ = resolve_profile(settings, 3, project=project)
    assert resolved["options"]["translation_wire_format"] == "cache-shared-v2"
    assert resolved["options"]["p1_wire_format"] == "cache-shared-v2"
    provider.settings["options"]["p1_wire_format"] = "cache-v2"
    with pytest.raises(PipelineError): provider.body(PROMPTS[1], inputs_for(1), response_schema(1, inputs_for(1)), 1)
    with pytest.raises(PipelineError): validate_profiles({"profiles": {"bad": provider.settings}, "default_profile": "bad"})


@pytest.mark.parametrize("n,mutate", [
    (2, lambda v: v["checks"].pop()), (2, lambda v: v["checks"].append(copy.deepcopy(v["checks"][0]))),
    (2, lambda v: v["issues"][0].update(source_span="Invented source words")),
    (4, lambda v: v["corrections"][0].update(source_span="Invented source words")),
    (4, lambda v: v["corrections"][0].update(draft_span="Invented draft words")),
    (4, lambda v: v["checks"][0].update(status="ok")),
    (3, lambda v: v["translations"].reverse()), (5, lambda v: v["translations"].pop()),
    (3, lambda v: v["translations"].append(copy.deepcopy(v["translations"][0]))),
])
def test_decoded_invalid_semantics_remain_rejected(rich_inputs, n, mutate):
    tasks, results = rich_inputs
    _, ctx = v2.encode_input(tasks[n], n)
    invalid = copy.deepcopy(results[n])
    mutate(invalid)
    decoded = v2.decode_output(v2.encode_output(invalid, n, ctx), n, ctx)
    with pytest.raises((PipelineError, jsonschema.ValidationError)):
        validate_result(n, decoded, tasks[n])
        jsonschema.validate(decoded, response_schema(n, tasks[n]))


def fixture_book(store):
    from test_codex_cache import COMMON, SENTENCES
    block = {**COMMON["SOURCE_BLOCKS"][0], "order": 1, "parent_id": "B1"}
    chunk = {"id": "chunk1", "number": 1, "chapter_id": "ch1", "index_in_chapter": 1,
             "blocks": [block], "sentences": SENTENCES}
    book = {"chunks": [chunk], "chapters": [{"id": "ch1", "number": 1, "chunk_ids": ["chunk1"]}]}
    store.register_chunks(book)
    with store.db:
        store.set("analysis_done", True)
        store.set("approved", True)
    return book


def test_mixed_history_full_pipeline_and_targeted_paths(project):
    from bookpipe.application.pipeline import execute_target_pass
    store = Store(project)
    book = fixture_book(store)
    # Freeze the same common input the production orchestrator derives.
    from bookpipe.engine import previous_context, source_blocks
    chunk = book["chunks"][0]
    context = previous_context(store, book, chunk, OfflineV2(project), 1000)
    memory, _ = store.translation_memory(chunk, context["english"], len, 1000)
    common = {"CHUNK_ID": "chunk1", "SOURCE_BLOCKS": source_blocks(chunk["blocks"]), **memory, "PREVIOUS_CONTEXT": context}
    p2 = {**common, "SOURCE_SENTENCES": chunk["sentences"]}
    p3 = {**common, "SEMANTIC_AUDIT": RESULTS[2]}
    try:
        Runner(store, OfflineV2(project, "canonical"), SETTINGS, Display(True)).run(2, "pass2/chunk1", p2)
        Runner(store, OfflineV2(project, "cache-v1"), SETTINGS, Display(True)).run(3, "pass3/chunk1", p3)
        provider = OfflineV2(project)
        execute_target_pass(store, book, provider, SETTINGS, Display(True), "chunk1", 4, False)
        assert [c["pass_no"] for c in provider.calls] == [4]
        execute_target_pass(store, book, provider, SETTINGS, Display(True), "chunk1", 5, False)
        assert [c["pass_no"] for c in provider.calls] == [4, 5]
        semantic = {n: read_json(next((project / "artifacts" / f"pass{n}").rglob("request.semantic.json")))["input_payload"] for n in range(2, 6)}
        assert set(semantic[3]) == set(common) | {"SEMANTIC_AUDIT"}
        assert semantic[4] == {**common, "SOURCE_SENTENCES": chunk["sentences"], "POLISH_DRAFT": RESULTS[3], "SEMANTIC_AUDIT": RESULTS[2]}
        assert semantic[5] == {**common, "POLISH_DRAFT": RESULTS[3], "CORRECTION_LEDGER": RESULTS[4]}
        assert store.chunk("chunk1")["status"] == "done"
    finally: store.close()


def test_full_cache_v2_pipeline(project):
    store = Store(project)
    book = fixture_book(store)
    provider = OfflineV2(project)
    try:
        translate(store, book, provider, SETTINGS, Display(True), 1)
        assert [c["pass_no"] for c in provider.calls] == [2, 3, 4, 5]
        assert len({c["cache_diagnostics"]["cache_prefix_sha256"] for c in provider.calls}) == 1
        assert provider.calls[2]["cache_diagnostics"]["draft_prefix_sha256"] == provider.calls[3]["cache_diagnostics"]["draft_prefix_sha256"]
        assert store.chunk("chunk1")["status"] == "done"
    finally: store.close()


def test_interruption_never_retries_or_checkpoints(project, monkeypatch):
    provider = OfflineV2(project)
    def interrupt(body, directory, recorder):
        provider.calls.append(body)
        recorder.append_text("answer.partial.txt", '{"p":3')
        raise PipelineError("Codex turn ended with status interrupted.")
    monkeypatch.setattr(provider, "generate", interrupt)
    store = Store(project)
    try:
        with pytest.raises(PipelineError, match="interrupted"):
            Runner(store, provider, {**SETTINGS, "json_retries": 2}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(provider.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


def test_actual_rpc_has_fixed_schema_exact_evidence_and_no_decoder_metadata(project):
    from test_transports import _write_fake_codex
    executable = project / "fake-codex"
    _write_fake_codex(executable)
    script = executable.read_text()
    script = script.replace('assert message["params"]["developerInstructions"] == "Trusted pass instructions"',
                            'assert message["params"]["developerInstructions"].startswith("Compact cache-v2")')
    script = script.replace('assert message["params"]["input"][0]["text"] == \'{"SOURCE":"payload"}\'',
                            'assert json.loads(message["params"]["input"][0]["text"])["ACTIVE_PASS"] in (4, 5)')
    script = script.replace('json.dumps({"ok":True}, separators=(",", ":"))',
                            'json.dumps({"p":5,"c":[],"i":[],"e":[],"t":[]})')
    executable.write_text(script)
    provider = OfflineV2(project)
    # Exercise the actual production generate path, using the existing fake CLI.
    provider.executable = str(executable)
    provider.settings["runtime_root"] = str(project / "runtimes")
    provider.settings["options"]["late_usage_wait"] = 0.01
    for n in (4, 5):
        directory = project / f"wire{n}"
        recorder = AttemptRecorder(directory, {"pass_no": n})
        body = provider.body(PROMPTS[n], inputs_for(n), response_schema(n, inputs_for(n)), n)
        provider.preflight(body, recorder)
        CodexAppServerClient.generate(provider, body, directory, recorder)
        plan = read_json(directory / "request.transport.json")
        events = [json.loads(line) for line in (directory / "transport.jsonl").read_text().splitlines()]
        outbound = [r["payload"] for r in events if r["kind"] == "rpc" and r["direction"] == "outbound"]
        assert next(r["params"] for r in outbound if r.get("method") == "thread/start") == plan["thread"]
        assert next(r["params"] for r in outbound if r.get("method") == "turn/start") == plan["turn"]
        assert plan["turn"]["input"][0]["text"] == body["input"]
        assert plan["turn"]["outputSchema"] == v2.TRANSPORT_SCHEMA == read_json(directory / "schema.transport.json")
        assert "codec_context" not in plan and "block_ids" not in plan["turn"]
        assert read_json(directory / "codec.context.json") == body["codec_context"]
        assert read_json(directory / "usage.json")["cached_input_tokens"] is not None


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_reverse_dictionary_order_does_not_change_layout(n):
    def reverse(v):
        if isinstance(v, dict): return {k: reverse(x) for k, x in reversed(list(v.items()))}
        if isinstance(v, list): return [reverse(x) for x in v]
        return v
    inputs = inputs_for(n)
    assert v2.encode_input(inputs, n)[0].text == v2.encode_input(reverse(inputs), n)[0].text


def test_provider_schema_size_and_chunk_independence(project):
    provider = OfflineV2(project)
    body = provider.body(PROMPTS[3], inputs_for(3), response_schema(3, inputs_for(3)), 3)
    inputs = inputs_for(3)
    inputs["SOURCE_BLOCKS"].extend({"id": f"B{i}", "kind": "paragraph", "text": "Larger chunk."} for i in range(2, 100))
    bigger = provider.body(PROMPTS[3], inputs, response_schema(3, inputs), 3)
    assert encode_value(bigger["output_schema"]) == encode_value(body["output_schema"])
    assert len(encode_value(v2.TRANSPORT_SCHEMA).encode()) == 1113


def test_unknown_codec_version_and_tampered_map_cannot_recover(project):
    from bookpipe.engine import _decode_transport_result
    inputs = inputs_for(3)
    _, ctx = v2.encode_input(inputs, 3)
    wire = v2.encode_output(RESULTS[3], 3, ctx)
    saved = ctx.as_dict()
    saved["map_version"] = 999
    with pytest.raises(PipelineError, match="version"):
        _decode_transport_result(wire, "cache-v2", inputs, saved, expected_pass=3)
    saved = ctx.as_dict()
    saved["block_ids"] = ["B999"]
    with pytest.raises(PipelineError, match="differs"):
        _decode_transport_result(wire, "cache-v2", inputs, saved, expected_pass=3)


@pytest.mark.parametrize("method", ["codec_context", "validation"])
def test_codec_evidence_failure_does_not_trigger_more_inference(project, monkeypatch, method):
    store = Store(project)
    provider = OfflineV2(project)
    monkeypatch.setattr(AttemptRecorder, method, lambda *a: (_ for _ in ()).throw(EvidenceError("disk")))
    try:
        with pytest.raises(EvidenceError):
            Runner(store, provider, {**SETTINGS, "json_retries": 2}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(provider.calls) == (0 if method == "codec_context" else 1)
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


def test_original_identifiers_in_quote_error_remain_local(project):
    from test_codex_cache import SENTENCES
    provider = OfflineV2(project, invalid=lambda v: {**v, "i": [{"s": 0, "x": "invented words", "k": 1, "m": "meaning", "c": "constraint", "q": 1}]})
    store = Store(project)
    try:
        Runner(store, provider, {**SETTINGS, "json_retries": 1}, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        retry = json.loads(provider.calls[1]["input"])
        assert "S1" not in retry["VALIDATION_ERROR"] and "S1" not in retry["RETRY_INSTRUCTION"]
        assert retry["SOURCE_SENTENCES"][0][2] == SENTENCES[0]["text"]
        error = next((project / "artifacts").rglob("validation_error.txt")).read_text()
        assert "S1" in error
    finally: store.close()


def test_local_map_failure_after_completion_is_not_a_model_retry(project, monkeypatch):
    provider = OfflineV2(project)
    original = provider.generate
    def corrupt(body, directory, recorder):
        result = original(body, directory, recorder)
        body["codec_context"]["block_ids"] = ["corrupted"]
        return result
    monkeypatch.setattr(provider, "generate", corrupt)
    store = Store(project)
    try:
        with pytest.raises(EvidenceError, match="differs"):
            Runner(store, provider, {**SETTINGS, "json_retries": 2}, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert len(provider.calls) == 1
        assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally: store.close()


def test_initial_validation_and_conservative_repair_are_separate(project):
    provider = OfflineV2(project, invalid=lambda v: {**v, "i": [{"s": 0, "x": "Żuraw … waits", "k": 1, "m": "meaning", "c": "constraint", "q": 1}]})
    store = Store(project)
    try:
        result, _, _ = Runner(store, provider, SETTINGS, Display(True)).run(2, "pass2/chunk1", inputs_for(2))
        directory = next((project / "artifacts").rglob("decoded.canonical.json")).parent
        assert read_json(directory / "decoded.canonical.json")["issues"][0]["source_span"] == "Żuraw … waits"
        assert result["issues"][0]["source_span"] == "Żuraw waits"
        validation = read_json(directory / "validation.json")
        assert validation["initial_validation_error"] is not None
        assert validation["repairs"] and validation["final_validation"] == "passed"
        inputs = inputs_for(3)
        inputs["SEMANTIC_AUDIT"] = result
        layout, ctx = v2.encode_input(inputs, 3)
        assert v2.decode_input(layout, ctx)["SEMANTIC_AUDIT"] == result
    finally: store.close()


def test_p1_all_existing_paths_unchanged_by_translation_format(project):
    for p1 in ("canonical", "compact-v1", "cache-v1"):
        old = client(project, translation_wire_format="cache-v1", p1_wire_format=p1)
        new = client(project, translation_wire_format="cache-v2", p1_wire_format=p1)
        args = (PROMPTS[1], inputs_for(1), response_schema(1, inputs_for(1)), 1)
        assert old.body(*args) == new.body(*args)
    (project / "prompts" / "pass1.txt").unlink()
    provider = client(project, translation_wire_format="cache-v2", p1_wire_format="compact-v1")
    assert provider.body(PROMPTS[2], inputs_for(2), response_schema(2, inputs_for(2)), 2)["wire_format"] == "cache-v2"


def test_separate_namespaces_even_when_identifier_strings_collide(rich_inputs):
    tasks, results = rich_inputs
    inputs = tasks[4]
    inputs["APPROVED_LEXICON"][0]["id"] = "block/z"
    inputs["SOURCE_BLOCKS"][0]["scene_id"] = "block/z"
    inputs["SOURCE_SENTENCES"][0]["id"] = "block/z"
    inputs["SEMANTIC_AUDIT"]["checks"][0]["sid"] = "block/z"
    inputs["SEMANTIC_AUDIT"]["issues"][0]["sid"] = "block/z"
    layout, ctx = v2.encode_input(inputs, 4)
    assert v2.decode_input(layout, ctx) == inputs
    assert ctx.block_ids == ("block/z", "block/a")
    assert ctx.term_ids == ("block/z",)
    assert ctx.sentence_ids == ("block/z", "sentence/a")


def test_completed_recovery_map_failure_does_not_spend_another_call(project):
    from bookpipe.util import atomic_json
    store = Store(project)
    first = OfflineV2(project)
    try:
        Runner(store, first, SETTINGS, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        with store.db:
            store.db.execute("DELETE FROM jobs")
            store.db.execute("DELETE FROM kv")
        path = next((project / "artifacts").rglob("codec.context.json"))
        context = read_json(path)
        context["block_ids"] = ["bad map"]
        atomic_json(path, context)
        new = OfflineV2(project, "canonical")
        with pytest.raises(EvidenceError, match="differs"):
            Runner(store, new, SETTINGS, Display(True)).run(3, "pass3/chunk1", inputs_for(3))
        assert new.calls == []
    finally: store.close()


def test_custom_output_directive_is_rejected_but_literal_braces_rule_preserved():
    with pytest.raises(PipelineError, match="custom output contract"):
        v2.compact_prompt(PROMPTS[3] + "\nReturn only a string.", 3)
    rule = "\nPreserve literal braces { and } when they occur in the source.\n"
    assert rule in v2.compact_prompt(PROMPTS[3] + rule, 3)
