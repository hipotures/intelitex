from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from experiments import codex_session_cache_probe as probe
from bookpipe.util import PipelineError


def fake_install(tmp_path, monkeypatch):
    """A real JSONL subprocess exercises one persistent connection, not a model."""
    executable = tmp_path / "codex-fake"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json,sys
from pathlib import Path
def send(value):
    print(json.dumps(value),flush=True)
if '--version' in sys.argv:
    print('codex-cli fake-test')
    sys.exit()
if 'generate-json-schema' in sys.argv:
    p=Path(sys.argv[sys.argv.index('--out')+1])/'v2'
    p.mkdir(parents=True)
    fields={
      'ThreadStartParams':['model','cwd','sandbox','approvalPolicy','ephemeral','baseInstructions','developerInstructions','personality','environments','dynamicTools','selectedCapabilityRoots','runtimeWorkspaceRoots','config'],
      'TurnStartParams':['threadId','input','model','effort','cwd','environments','runtimeWorkspaceRoots','outputSchema'],
      'ThreadForkParams':['threadId','lastTurnId','excludeTurns','ephemeral','model','config','cwd','baseInstructions','developerInstructions','sandbox','approvalPolicy','runtimeWorkspaceRoots'],
      'ThreadTurnsListParams':['threadId','itemsView','limit','sortDirection'],
    }
    for name,keys in fields.items():
        (p/(name+'.json')).write_text(json.dumps({'type':'object','properties':{k:{} for k in keys},'additionalProperties':False}))
    sys.exit()
roots=0
threads={}
for raw in sys.stdin:
    request=json.loads(raw)
    method=request.get('method')
    params=request.get('params',{})
    result={}
    events=[]
    if method=='initialize':
        result={'userAgent':'fake'}
    elif method=='skills/list':
        result={'data':[{'skills':[],'errors':[]}]}
    elif method in ('thread/start','thread/fork'):
        if method=='thread/start':
            roots+=1
            tid='root-'+str(roots)
            sid='session-'+str(roots)
            turns=[]
            forked=None
        else:
            tid='fork-A1'
            sid=threads[params['threadId']]['sessionId']
            turns=[{'id':params['lastTurnId'],'status':'completed'}]
            forked=params['threadId']
        thread={'id':tid,'sessionId':sid,'model':params['model'],'reasoningEffort':'high',
                'path':None,'ephemeral':params['ephemeral'],'forkedFromId':forked,'turns':turns,'environments':[]}
        threads[tid]=thread
        result={'thread':thread,'model':params['model'],'reasoningEffort':'high',
                'sandbox':{'type':'readOnly'},'approvalPolicy':'never','runtimeWorkspaceRoots':[]}
    elif method=='thread/turns/list':
        result={'data':threads[params['threadId']]['turns'],'nextCursor':None}
    elif method=='turn/start':
        tid=params['threadId']
        thread=threads[tid]
        number=len(thread['turns'])+1
        turnid=tid+'-turn-'+str(number)
        thread['turns'].append({'id':turnid,'status':'completed'})
        cached=29000 if number>1 else 0
        usage={'inputTokens':30000,'cachedInputTokens':cached,'cacheWriteInputTokens':0,
               'outputTokens':7,'reasoningOutputTokens':0,'totalTokens':30007}
        total={k:v*number for k,v in usage.items()}
        result={'turn':{'id':turnid,'status':'inProgress','items':[]}}
        events=[
          {'method':'item/completed','params':{'threadId':tid,'turnId':turnid,'item':{'type':'agentMessage','phase':'final_answer','text':'{"ok":"OK"}'}}},
          {'method':'thread/tokenUsage/updated','params':{'threadId':tid,'turnId':turnid,'tokenUsage':{'last':usage,'total':total}}},
          {'method':'turn/completed','params':{'threadId':tid,'turn':{'id':turnid,'status':'completed','items':[]}}},
        ]
    if 'id' in request:
        send({'id':request['id'],'result':result})
    for event in events:
        send(event)
''', encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(probe.shutil, "which", lambda name: str(executable))
    user_home = tmp_path / "authorized-home"
    (user_home / ".codex").mkdir(parents=True)
    (user_home / ".codex" / "auth.json").write_text('{"test_only":true}')
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    project = tmp_path / "project"
    project.mkdir()
    (project / "source.xhtml").write_text('<p>Test fixture.</p>')
    chapter = {"id": "ch0034", "title": "Test chapter", "source_file": "source.xhtml",
               "blocks": [{"text": "A prose fixture for offline protocol testing. " * 2200}],
               "source_tokens": 99000, "source_tokens_tokenizer": {"method": "utf8_byte_upper_bound"}}
    (project / "book.json").write_text(json.dumps({"source_root": str(project), "chapters": [chapter]}))
    return project


def test_persistent_process_roots_followup_fork_and_complete_evidence(tmp_path, monkeypatch):
    project = fake_install(tmp_path, monkeypatch)
    scratch = tmp_path / "probe"
    result = probe.run(project, scratch, "ch0034", 10, True)
    assert all(result["checks"].values())
    assert result["classification"]["pattern"] == 4
    assert result["fork"]["status"] == "completed"
    rows = result["rows"]
    assert [r["scenario"] for r in rows] == ["A1", "B1", "A2", "D1"]
    # A2's raw thread total is 60000; row input must use the provider's last.
    assert [r["input_tokens"] for r in rows] == [30000] * 4
    assert [r["cached_input_tokens"] for r in rows] == [0, 0, 29000, 29000]
    assert rows[3]["session_id"] == rows[0]["session_id"]
    assert rows[3]["thread_id"] != rows[0]["thread_id"]
    assert rows[3]["anchor_turn_id"] == rows[0]["turn_id"]
    assert rows[2]["previous_relevant_scenario"] == "A1"
    assert rows[2]["previous_scenario"] == "B1"
    assert rows[2]["previous_relevant_start_to_start_seconds"] > rows[2]["previous_start_to_start_seconds"]
    requests = [json.loads(line) for line in (scratch / "rpc.jsonl").read_text().splitlines()]
    outbound = [r["message"] for r in requests if r["direction"] == "outbound"]
    turns = [r["params"] for r in outbound if r.get("method") == "turn/start"]
    assert turns[0]["input"] == turns[1]["input"]
    assert turns[2]["input"] == turns[3]["input"] == [{"type": "text", "text": probe.FOLLOWUP}]
    assert len([r for r in outbound if r.get("method") == "thread/start"]) == 2
    fork = next(r["params"] for r in outbound if r.get("method") == "thread/fork")
    assert fork["lastTurnId"] == rows[0]["turn_id"] and fork["ephemeral"] is True and fork["excludeTurns"] is True
    for row in rows:
        directory = scratch / row["scenario"]
        assert json.loads((directory / "turn.start.params.json").read_text()) == turns[rows.index(row)]
        assert json.loads((directory / "usage.events.json").read_text())[0]["params"]["turnId"] == row["turn_id"]
        assert (directory / "rpc.jsonl").is_file()
        assert row["completed_at"] and row["elapsed_seconds"] > 0
    assert not (scratch / "runtime/home/auth.json").exists()
    assert (project / "book.json").is_file()
    assert set(project.iterdir()) == {project / "source.xhtml", project / "book.json"}


def test_changed_frozen_bytes_fail_before_b1_submission(tmp_path, monkeypatch):
    project = fake_install(tmp_path, monkeypatch)
    scratch = tmp_path / "probe"
    original = probe.Probe.start_root
    def changed(self, scenario):
        result = original(self, scenario)
        if scenario == "B1":
            frozen = self.scratch / "source.utf8.txt"
            frozen.chmod(0o600)
            frozen.write_bytes(frozen.read_bytes() + b"changed")
        return result
    monkeypatch.setattr(probe.Probe, "start_root", changed)
    with pytest.raises(PipelineError, match="Frozen source bytes changed"):
        probe.run(project, scratch, "ch0034", 10, False)
    sent = [json.loads(line)["message"] for line in (scratch / "rpc.jsonl").read_text().splitlines()
            if json.loads(line)["direction"] == "outbound"]
    assert len([m for m in sent if m.get("method") == "turn/start"]) == 1
    assert not (scratch / "runtime/home/auth.json").exists()


@pytest.mark.parametrize("cache,pattern", [([0,0,29000],1), ([0,29000,29000],2), ([0,0,0],3), ([0,0,100],None)])
def test_classification_requires_observed_cache_magnitude(cache, pattern):
    rows = [{"scenario": n, "input_tokens": 30000, "cached_input_tokens": c, "status": "completed"}
            for n,c in zip(("A1","B1","A2"),cache)]
    assert probe.classify(rows)["pattern"] == pattern


def test_missing_usage_is_inconclusive_and_thread_total_never_used():
    assert probe.classify([])["pattern"] is None
    events = [{"last": {"inputTokens": 20000, "cachedInputTokens": 0},
               "total": {"inputTokens": 40000, "cachedInputTokens": 20000}}]
    assert probe.usage_values(events)["cached_input_tokens"] == 0
    assert probe.usage_values(events)["input_tokens"] == 20000
    assert probe.usage_values(events)["cache_write_input_tokens"] is None


def test_reported_model_effort_and_external_instructions_fail_closed():
    valid = {"thread": {"id":"a", "sessionId":"a"}, "model":probe.MODEL,
             "reasoningEffort":"high", "approvalPolicy":"never", "sandbox":{"type":"readOnly"}}
    assert probe.validate_thread(valid)["reported_model"] == probe.MODEL
    for changes in ({"model":"other"}, {"reasoningEffort":"low"}, {"instructionSources":["/AGENTS.md"]},
                    {"approvalPolicy":"on-request"}, {"sandbox":{"type":"workspaceWrite"}}):
        value = {**copy.deepcopy(valid), **changes}
        with pytest.raises(PipelineError):
            probe.validate_thread(value)


def test_scratch_inside_production_or_repository_is_rejected(tmp_path):
    for project,scratch in ((tmp_path, tmp_path/"inside"), (tmp_path/"project",tmp_path),
                            (tmp_path/"project",Path(probe.__file__).resolve().parents[1]/"bad-probe")):
        with pytest.raises(PipelineError, match="outside both repository and production"):
            probe.run(project,scratch,None,10,False)
        assert not scratch.exists() or scratch == tmp_path


def test_foreign_thread_events_cannot_pollute_usage_or_completion(tmp_path):
    rpc = object.__new__(probe.ProbeRpc)
    rpc.recorder = probe.Recorder(tmp_path)
    rpc.recorder.expected_thread = "B"
    rpc.state = {"usage_events": []}
    rpc.notifications = []
    rpc.dispatch({"method":"thread/tokenUsage/updated", "params":{"threadId":"A","tokenUsage":{"last":{"cachedInputTokens":123}}}})
    rpc.dispatch({"method":"turn/completed", "params":{"threadId":"A","turn":{"status":"completed"}}})
    assert rpc.state == {"usage_events": []} and rpc.notifications == []


@pytest.mark.parametrize("different_text", [False, True])
def test_rollout_audit_compares_role_content_and_preserves_dynamic_metadata(tmp_path, different_text):
    scratch = tmp_path / "probe"
    runtime = scratch / "runtime/home"
    runtime.mkdir(parents=True)
    (scratch / "source.utf8.txt").write_bytes(b"Frozen prose fixture")
    rows=[]
    for scenario in ("A1", "B1"):
        path=runtime/(scenario+'.jsonl')
        developer=probe.DEVELOPER + (' Changed' if scenario=='B1' and different_text else '')
        events=[
            {"type":"session_meta","payload":{"session_id":scenario,"runtime_workspace_roots":[],
                "base_instructions":{"text":probe.BASE,"provenance":{"type":"custom"}}}},
            {"type":"response_item","payload":{"id":scenario,"type":"message","role":"developer",
                "content":[{"text":developer}],"internal_chat_message_metadata_passthrough":{"turn_id":scenario}}},
            {"type":"response_item","payload":{"type":"message","role":"user","content":[{"text":"Frozen prose fixture"}]}},
            {"type":"turn_context","payload":{"turn_id":scenario,"model":probe.MODEL,"effort":probe.EFFORT,
                "approval_policy":"never","sandbox_policy":{"type":"read-only"}}},
        ]
        path.write_text('\n'.join(json.dumps(e) for e in events))
        rows.append({"scenario":scenario,"path":str(path),"ephemeral":False,"session_id":scenario,"turn_id":scenario})
    result=probe.audit_rollouts(scratch,rows)
    assert result["a1_b1_initial_runtime_prefix_identical"] is not different_text
    assert all(r["all_passed"] for r in result["roots"].values())
    a,b=(result['roots'][n] for n in ('A1','B1'))
    assert a['initial_runtime_message_records_sha256'] != b['initial_runtime_message_records_sha256']


def test_fork_local_environment_requires_private_cwd_and_turn_override():
    result={"thread":{"id":"fork","sessionId":"fork","environments":[
        {"environmentId":"local","cwd":"/private/work","runtimeWorkspaceRoots":[]}]},
        "model":probe.MODEL,"reasoningEffort":probe.EFFORT,"sandbox":{"type":"readOnly"},
        "approvalPolicy":"never","cwd":"/private/work"}
    with pytest.raises(PipelineError):
        probe.validate_thread(result)
    thread=probe.validate_thread(result,allow_local_fork_environment=True)
    params=probe.turn_params(Path('/private/work'),thread['thread_id'],probe.FOLLOWUP)
    assert params['environments']==[] and params['runtimeWorkspaceRoots']==[]
    result['thread']['environments'][0]['cwd']='/external/work'
    with pytest.raises(PipelineError):
        probe.validate_thread(result,allow_local_fork_environment=True)
