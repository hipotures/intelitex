from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bookpipe.codex_effort import resolve_effort_plan, runtime_model_metadata, rollout_effort_evidence
from bookpipe.codex_cache import encode_value
from bookpipe import codex_cache_shared as shared
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.engine import response_schema
from bookpipe.profiles import resolve_profile
from bookpipe.util import PipelineError, digest, read_json
from test_codex_cache import PROMPTS, SETTINGS, client, inputs_for, project


def model_metadata(*, support=True, default="medium", slug="model-a"):
    return {"slug": slug, "supports_reasoning_effort_updates": support, "default_reasoning_level": default,
            "supported_reasoning_levels": [{"effort": e, "description": e} for e in ("low", "medium", "high", "ultra")]}


def picker(metadata):
    return {"id": metadata["slug"], "model": metadata["slug"],
            "defaultReasoningEffort": metadata["default_reasoning_level"],
            "supportedReasoningEfforts": [{"reasoningEffort": e["effort"], "description": e["description"]}
                                          for e in metadata["supported_reasoning_levels"]]}


def save_catalog(home, metadata, **overrides):
    value = {"client_version": "0.159.3", "models": [metadata], **overrides}
    (home / "models_cache.json").write_text(json.dumps(value))


def test_supported_model_preserves_selected_effort_and_uses_one_model_default():
    plans = [resolve_effort_plan(e, model_metadata(), feature_available=True) for e in ("low", "medium", "low", "medium")]
    assert [p.requested for p in plans] == ["low", "medium", "low", "medium"]
    assert {p.baseline for p in plans} == {"medium"}
    for p in plans:
        assert p.supported and p.mode == "configuration_update"
        assert p.thread_config() == {"model_reasoning_effort": "medium", "features.reasoning_effort_override": True}
        assert p.metadata()["effective_effort_requested"] == p.requested
        assert p.metadata()["provider_request_effort"] is None
        assert p.metadata()["configuration_update_observed"] is None


@pytest.mark.parametrize("support", [False, None, 1, "true"])
@pytest.mark.parametrize("selected", ["low", "medium", "high", None])
def test_unsupported_missing_or_nonboolean_capability_keeps_request_effort(support, selected):
    plan = resolve_effort_plan(selected, model_metadata(support=support), feature_available=True)
    assert not plan.supported and plan.mode == "request_level"
    assert plan.baseline == plan.requested == selected
    assert plan.thread_config()["features.reasoning_effort_override"] is False


def test_old_cli_no_feature_and_unknown_catalog_keep_previous_behavior():
    for metadata, feature in [(model_metadata(), False), (None, False), (None, True)]:
        plan = resolve_effort_plan("high", metadata, feature_available=feature)
        assert plan.mode == "request_level" and plan.baseline == "high"
        if not feature:
            assert plan.thread_config() == {"model_reasoning_effort": "high"}


@pytest.mark.parametrize("default", [None, "unknown", "ultra", "low"])
def test_no_invented_default_when_metadata_is_incomplete_or_requires_alias_resolution(default):
    metadata = model_metadata(default=default)
    if default == "low": metadata["supported_reasoning_levels"] = []
    p = resolve_effort_plan("high", metadata, feature_available=True)
    assert p.mode == "request_level" and p.baseline == p.requested == "high"


def test_alias_custom_and_invalid_effort_go_to_ordinary_provider_validation_without_clamping():
    for selected in ("ultra", "custom-effort", "typo", True):
        p = resolve_effort_plan(selected, model_metadata(), feature_available=True)
        assert p.mode == "request_level" and p.baseline == p.requested == selected
        assert p.thread_config()["features.reasoning_effort_override"] is False


def test_same_effort_and_unspecified_effort_use_model_default_without_appending_user_items():
    for selected in ("medium", None):
        p = resolve_effort_plan(selected, model_metadata(), feature_available=True)
        assert p.mode == "configuration_update" and p.baseline == "medium" and p.requested == selected
        assert "input" not in p.thread_config()


def test_capability_is_versioned_selected_private_catalog_not_model_name(tmp_path):
    m = model_metadata(slug="any-future-model")
    save_catalog(tmp_path, m)
    actual, evidence = runtime_model_metadata(tmp_path, m["slug"], "codex-cli 0.159.3", picker(m))
    assert actual == m and evidence["trusted"]
    assert evidence["catalog_sha256"] == digest((tmp_path / "models_cache.json").read_bytes())
    for version in (None, "codex-cli 0.159.2"):
        assert runtime_model_metadata(tmp_path, m["slug"], version, picker(m))[0] is None
    for listed in (None, picker(model_metadata(slug="other")), dict(picker(m), defaultReasoningEffort="low"),
                   dict(picker(m), supportedReasoningEfforts=[])):
        assert runtime_model_metadata(tmp_path, m["slug"], "codex-cli 0.159.3", listed)[0] is None
    save_catalog(tmp_path, m, models=[m, m])
    assert runtime_model_metadata(tmp_path, m["slug"], "codex-cli 0.159.3", picker(m))[0] is None


def test_catalog_path_escape_and_malformed_data_are_safe_optimization_misses(tmp_path):
    home = tmp_path / "private"
    home.mkdir()
    save_catalog(tmp_path, model_metadata())
    (home / "models_cache.json").symlink_to(tmp_path / "models_cache.json")
    assert runtime_model_metadata(home, "model-a", "codex-cli 0.159.3", picker(model_metadata()))[0] is None
    (home / "models_cache.json").unlink()
    for bad in ('{', '[]', '{"client_version":"0.159.3","models":null}'):
        (home / "models_cache.json").write_text(bad)
        assert runtime_model_metadata(home, "model-a", "codex-cli 0.159.3", picker(model_metadata()))[0] is None


def write_fake_codex(path, **options):
    """Protocol double; does not call any provider or simulate measured cache hits."""
    script = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, uuid
opts = OPTIONS
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.159.3"); sys.exit(0)
if sys.argv[1:] == ["features", "list"]:
    if opts.get("feature", True): print("reasoning_effort_override under development false")
    sys.exit(0)
home = pathlib.Path(os.environ["CODEX_HOME"])
thread_id, turn_id = str(uuid.uuid4()), str(uuid.uuid4())
rollout = home / "sessions" / "rollout.jsonl"
def send(v):
    print(json.dumps(v), flush=True)
def event(kind, payload):
    with rollout.open('a') as f: f.write(json.dumps({'type':kind, 'payload':payload})+'\n')
for line in sys.stdin:
    msg = json.loads(line); method, params = msg.get('method'), msg.get('params', {}); rid = msg.get('id')
    if method == 'initialize': send({'id':rid,'result':{}})
    elif method == 'initialized': continue
    elif method == 'skills/list': send({'id':rid,'result':{'data':[]}})
    elif method == 'model/list':
        model = opts.get('model', 'gpt-6-astra'); default = opts.get('default', 'medium')
        presets = [{'effort':e,'description':e} for e in ['low','medium','high','ultra']]
        metadata = {'slug':model,'default_reasoning_level':default,'supported_reasoning_levels':presets,
                    'supports_reasoning_effort_updates':opts.get('support', True)}
        if not opts.get('missing_metadata'):
            (home/'models_cache.json').write_text(json.dumps({'client_version':'0.159.3','models':[metadata]}))
        send({'id':rid,'result':{'data':[{'id':model,'model':model,'defaultReasoningEffort':default,
              'supportedReasoningEfforts':[{'reasoningEffort':p['effort'],'description':p['description']} for p in presets]}]}})
    elif method == 'thread/start':
        assert params['ephemeral'] is False and params['sandbox']=='read-only' and params['approvalPolicy']=='never'
        assert params['dynamicTools']==[] and params['environments']==[] and params['runtimeWorkspaceRoots']==[]
        model, config = params['model'], params['config']
        baseline = config['model_reasoning_effort']
        rollout.parent.mkdir(parents=True)
        event('session_meta', {'session_id':thread_id})
        send({'id':rid,'result':{'thread':{'id':thread_id,'sessionId':thread_id,'path':str(rollout)},
                               'model':model,'reasoningEffort':'high' if opts.get('wrong_baseline') else baseline}})
    elif method == 'turn/start':
        assert params['threadId']==thread_id and len(params['input'])==1
        selected = params['effort'] or baseline
        if not opts.get('no_observation'):
            event('turn_context', {'turn_id':turn_id,'model':model,'effort':'high' if opts.get('wrong_effort') else selected})
            event('response_item', {'type':'message','role':'user','content':params['input']})
            if config.get('features.reasoning_effort_override') and selected!=baseline:
                event('response_item', {'type':'configuration_update','reasoning':{'effort':'high' if opts.get('wrong_update') else selected}})
        send({'id':rid,'result':{'turn':{'id':turn_id}}})
        send({'method':'item/completed','params':{'threadId':thread_id,'turnId':turn_id,
              'item':{'type':'agentMessage','phase':'final_answer','text':'{"ok":true}'}}})
        send({'method':'thread/tokenUsage/updated','params':{'threadId':thread_id,'turnId':turn_id,
              'tokenUsage':{'last':{'inputTokens':100,'cachedInputTokens':0,'outputTokens':3,
                                   'reasoningOutputTokens':1,'totalTokens':103},
                            'total':{'inputTokens':100,'outputTokens':3,'totalTokens':103}}}})
        send({'method':'turn/completed','params':{'threadId':thread_id,'turnId':turn_id,'turn':{'id':turn_id,'status':'completed'}}})
    else: raise AssertionError(method)
'''
    path.write_text(script.replace('OPTIONS', repr(options)))
    path.chmod(0o700)


def inference(root, n, effort, executable, *, model="gpt-6-astra", wire="cache-shared-v1"):
    instance = client(root, p1_wire_format=wire, translation_wire_format=wire if wire != "compact-v1" else "canonical")
    instance.executable = str(executable)
    instance.model = model
    instance.settings.update(reasoning_effort=effort, runtime_root=str(root / "runtime"))
    instance.settings["options"]["late_usage_wait"] = 0.01
    inputs = inputs_for(n)
    body = instance.body(PROMPTS[n], inputs, response_schema(n, inputs), n)
    directory = root / f"p{n}-{effort}-{executable.name}"
    recorder = AttemptRecorder(directory, {"pass_no": n})
    instance.preflight(body, recorder)
    answer, metadata = instance.generate(body, directory, recorder)
    return body, metadata, read_json(directory / "request.transport.json"), directory


def test_all_five_calls_are_independent_complete_requests_with_per_turn_efforts(project):
    executable = project / "fake"
    write_fake_codex(executable)
    rows = [inference(project, n, e, executable) for n, e in zip(range(1, 6), ("high", "low", "medium", "low", "medium"))]
    assert len({m["thread_id"] for _, m, _, _ in rows}) == 5
    assert len({m["session_id"] for _, m, _, _ in rows}) == 5
    assert len({p["environment"]["CODEX_HOME"] for _, _, p, _ in rows}) == 5
    for n, (body, meta, plan, directory) in enumerate(rows, 1):
        assert plan["thread"]["config"] == {"model_reasoning_effort":"medium", "features.reasoning_effort_override":True}
        assert plan["turn"]["effort"] == meta["requested_effort"] == meta["reported_effort"] == body["effort"]
        assert meta["request_effort_baseline"] == "medium" and meta["reasoning_effort_update_supported"]
        assert meta["reasoning_effort_update_mode"] == "configuration_update"
        assert meta["requested_model"] == meta["reported_model"] == "gpt-6-astra"
        assert plan["requested_model"] == plan["reported_model"] == "gpt-6-astra"
        assert plan["reported_effort"] is None  # Not yet observed when the request is sent.
        assert meta["provider_request_effort"] is None
        assert meta["configuration_update_observed"] == (body["effort"] != "medium")
        assert plan["turn"]["input"] == [{"type":"text","text":body["input"]}]
        assert plan["turn"]["outputSchema"] == body["output_schema"]
        messages = [json.loads(line)["payload"] for line in (directory / "transport.jsonl").read_text().splitlines()]
        sent = [m for m in messages if isinstance(m, dict) and m.get("method")]
        assert [m["method"] for m in sent].count("thread/start") == 1
        assert [m["method"] for m in sent].count("turn/start") == 1
        assert not {"thread/fork","thread/resume","turn/steer"} & {m["method"] for m in sent}
        assert next(m["params"] for m in sent if m["method"] == "thread/start") == plan["thread"]
        assert next(m["params"] for m in sent if m["method"] == "turn/start") == plan["turn"]
        assert "configuration_update" not in body["input"]
        usage = read_json(directory / "usage.json")
        assert usage["input_tokens"] == 100 and usage["output_tokens"] == 3 and usage["reasoning_output_tokens"] == 1
        assert usage["total_tokens"] == 103 and usage["cached_input_tokens"] == 0
        assert usage["cache_write_input_tokens"] is None
        assert not Path(plan["environment"]["CODEX_HOME"]).exists()
    assert len({body["developer_instructions"] for body, _, _, _ in rows}) == 1
    assert len({meta["source_prefix_sha256"] for _, meta, _, _ in rows}) == 1
    assert len({meta["translation_common_prefix_sha256"] for _, meta, _, _ in rows[1:]}) == 1
    assert rows[3][1]["draft_prefix_sha256"] == rows[4][1]["draft_prefix_sha256"]


@pytest.mark.parametrize("options", [{"support":False}, {"missing_metadata":True}, {"feature":False}])
def test_real_construction_fallback_keeps_requested_level_without_override(project, options):
    executable = project / "fallback"
    write_fake_codex(executable, **options)
    _, meta, plan, _ = inference(project, 2, "high", executable)
    assert meta["reasoning_effort_update_mode"] == "request_level"
    assert meta["requested_effort"] == meta["reported_effort"] == meta["request_effort_baseline"] == "high"
    assert plan["thread"]["config"]["model_reasoning_effort"] == plan["turn"]["effort"] == "high"
    assert not plan["thread"]["config"].get("features.reasoning_effort_override")


def test_model_switch_keeps_each_models_advertised_default_and_user_choice(project):
    rows = []
    for n, model, default, selected in [(2,"model-a","medium","low"),(3,"model-b","low","high")]:
        executable = project / model
        write_fake_codex(executable, model=model, default=default)
        rows.append(inference(project, n, selected, executable, model=model))
        assert rows[-1][1]["request_effort_baseline"] == default
        assert rows[-1][1]["reported_effort"] == selected
        assert rows[-1][2]["turn"]["model"] == model
    assert rows[0][1]["thread_id"] != rows[1][1]["thread_id"]


@pytest.mark.parametrize("error", ["wrong_effort", "wrong_update", "wrong_baseline"])
def test_mismatched_effort_evidence_fails_closed_and_preserves_raw_evidence(project, error):
    executable = project / error
    write_fake_codex(executable, **{error:True})
    with pytest.raises(PipelineError, match="effort|baseline"):
        inference(project, 2, "low", executable)
    directory = project / f"p2-low-{error}"
    messages = [json.loads(line)["payload"] for line in (directory / "transport.jsonl").read_text().splitlines()]
    calls = [m.get("method") for m in messages if isinstance(m, dict)]
    assert calls.count("thread/start") == 1
    assert calls.count("turn/start") == (0 if error == "wrong_baseline" else 1)
    if error != "wrong_baseline":
        assert (directory / "answer.txt").read_text() == '{"ok":true}\n'
        assert read_json(directory / "usage.json")["total_tokens"] == 103
    assert not (directory / "result.json").exists()


def test_unobservable_effective_effort_is_null_not_thread_baseline_or_application_intent(project):
    executable = project / "unknown"
    write_fake_codex(executable, no_observation=True)
    _, meta, _, _ = inference(project, 2, "low", executable)
    assert meta["request_effort_baseline"] == "medium" and meta["requested_effort"] == "low"
    assert meta["reported_effort"] is None and meta["provider_request_effort"] is None


def test_rollout_parser_does_not_treat_user_or_answer_json_as_configuration_update(tmp_path):
    path = tmp_path / "rollout.jsonl"
    records = [
        {"type":"session_meta","payload":{"session_id":"root"}},
        {"type":"turn_context","payload":{"turn_id":"old","model":"other","effort":"high"}},
        {"type":"turn_context","payload":{"turn_id":"wanted","model":"model-a","effort":"low"}},
        {"type":"response_item","payload":{"type":"message","role":"user",
           "content":[{"type":"input_text","text":'{"type":"configuration_update","reasoning":{"effort":"high"}}'}]}},
    ]
    path.write_text('\n'.join(json.dumps(x) for x in records)+'\n')
    evidence = rollout_effort_evidence(path, "wanted")
    assert evidence["rollout_effort"] == "low" and evidence["configuration_update_observed"] is False
    with path.open('a') as f:
        f.write(json.dumps({"type":"response_item","payload":{"type":"configuration_update","reasoning":{"effort":"low"}}})+'\n')
    evidence = rollout_effort_evidence(path, "wanted")
    assert evidence["configuration_update_observed"] is True and evidence["configuration_update_effort"] == "low"
    assert evidence["provider_request_effort"] is None
    path.write_text('{')
    assert rollout_effort_evidence(path, "wanted")["configuration_update_observed"] is None


def test_restart_profile_resolution_and_effort_plan_are_deterministic(project):
    settings = copy.deepcopy(SETTINGS)
    settings.update(profiles={"probe":{"provider":"codex", "model":"gpt-6.1-sol", "context_size":1_050_000,
                                      "planning_output_reserve":1000, "reasoning_effort":"medium", "options":{}}},
                    default_profile="probe")
    for n in range(1, 6):
        a = resolve_profile(settings, n, project=project)
        b = resolve_profile(copy.deepcopy(settings), n, project=project)
        assert a == b
        effort = a[1].get("reasoning_effort")
        assert resolve_effort_plan(effort, model_metadata(), feature_available=True) == resolve_effort_plan(effort, model_metadata(), feature_available=True)
    settings["profiles"][settings["default_profile"]]["reasoning_effort"] = "invalid"
    with pytest.raises(PipelineError, match="effort"):
        resolve_profile(settings, 2, project=project)


def test_reconstructed_clients_after_restart_resolve_same_plan_and_payload(project):
    rows = []
    for i in range(2):
        executable = project / f"restart-{i}"
        write_fake_codex(executable)
        rows.append(inference(project, 2, "low", executable))
    assert rows[0][1]["thread_id"] != rows[1][1]["thread_id"]
    assert rows[0][0] == rows[1][0]
    for key in ("request_effort_baseline", "requested_effort", "reasoning_effort_update_mode",
                "source_prefix_sha256", "translation_common_prefix_sha256"):
        assert rows[0][1][key] == rows[1][1][key]


def test_effort_plan_evidence_failure_aborts_before_a_model_turn(project, monkeypatch):
    executable = project / "evidence-error"
    write_fake_codex(executable)
    original = AttemptRecorder.transport_request
    def fail_after_capability(self, request, schema):
        if request.get("reasoning_effort_update_mode") == "configuration_update":
            raise EvidenceError("effort plan could not be recorded")
        return original(self, request, schema)
    monkeypatch.setattr(AttemptRecorder, "transport_request", fail_after_capability)
    with pytest.raises(EvidenceError): inference(project, 2, "low", executable)
    directory = project / "p2-low-evidence-error"
    events = [json.loads(line)["payload"] for line in (directory / "transport.jsonl").read_text().splitlines()]
    assert not any(isinstance(e, dict) and e.get("method") in ("thread/start", "turn/start") for e in events)


def test_cache_shared_golden_bytes_from_step1_head_are_unchanged():
    golden = read_json(Path(__file__).parent / "fixtures/codex_effort/cache_shared_c9e3178.json")
    assert digest(shared.developer_contract(PROMPTS)) == golden["developer"]
    assert digest(encode_value(shared.TRANSPORT_SCHEMA)) == golden["schema"]
    for n in range(1, 6):
        inputs = inputs_for(n) if n > 1 else {
            "SECTION_ID":"chapter", "SOURCE_BLOCKS":inputs_for(2)["SOURCE_BLOCKS"],
            "EXISTING_MEMORY":{"catalogue":[],"matched":[],"catalogue_incomplete":False}}
        layout, _ = shared.encode_input(inputs, n)
        actual = {k:digest(v) if v is not None else None for k,v in {
            "input":layout.text,"source":layout.source_prefix,"common":layout.translation_common_prefix,"draft":layout.draft_prefix}.items()}
        assert actual == golden["passes"][str(n)]


@pytest.mark.parametrize("wire,n", [("compact-v1",1),("cache-v1",2),("cache-v2",3),("canonical",4)])
def test_effort_path_does_not_reencode_other_wire_formats(project, wire, n):
    executable = project / wire
    write_fake_codex(executable)
    body, meta, plan, _ = inference(project, n, "low", executable, wire=wire)
    assert meta["wire_format"] == wire
    assert plan["turn"]["input"][0]["text"] == body["input"]
    assert plan["thread"]["developerInstructions"] == body["developer_instructions"]
    assert plan["turn"]["outputSchema"] == body["output_schema"]
