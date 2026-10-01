from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from bookpipe import codex_cache_v2 as v2
from bookpipe.util import PipelineError, read_json
from experiments import codex_cache_v3_session as probe
from experiments.codex_session_cache_probe import Recorder, USAGE_FIELDS


@pytest.fixture
def data():
    source = [{"id": "block/z", "kind": "paragraph", "text": '“She waits.”\n'},
              {"id": "block/a", "kind": "paragraph", "text": 'The next “line”.'}]
    sentences = [{"id": "sentence/z", "block_id": "block/z", "text": '“She waits.”'},
                 {"id": "sentence/a", "block_id": "block/a", "text": 'The next “line”.'}]
    p2 = {"CHUNK_ID": "test", "SOURCE_BLOCKS": source, "SOURCE_SENTENCES": sentences,
          "APPROVED_LEXICON": [], "OBSERVATIONS": [], "PREVIOUS_CONTEXT": {}}
    audit = {"checks": [{"sid": s["id"], "risk": "low"} for s in sentences], "issues": []}
    draft = {"translations": [{"id": b["id"], "text": "Polski tekst „tak”."} for b in source]}
    ledger = {"checks": [{"sid": s["id"], "status": "ok"} for s in sentences], "corrections": []}
    return p2, {2: audit, 3: draft, 4: ledger, 5: draft}


class FakeRpc:
    def __init__(self, recorder, outputs, ctx, fail_pass=None, bad_quote=False):
        self.recorder, self.outputs, self.ctx = recorder, outputs, ctx
        self.requests, self.notifications, self.state = [], [], {}
        self.fail_pass = fail_pass
        self.turns = 0
        self.per_pass = {}
        self.bad_quote = bad_quote
        self.summed = 0

    def request(self, method, params, deadline):
        self.requests.append((method, copy.deepcopy(params)))
        if method == "thread/start":
            return {"model": probe.MODEL, "reasoningEffort": probe.EFFORT, "approvalPolicy": "never",
                    "sandbox": {"type": "readOnly"}, "thread": {"id": "same-thread", "sessionId": "same-session"}}
        assert method == "turn/start"
        self.pass_no = json.loads(params["input"][0]["text"])["ACTIVE_PASS"]
        self.per_pass[self.pass_no] = self.per_pass.get(self.pass_no, 0) + 1
        self.turns += 1
        return {"turn": {"id": f"turn-{self.turns}"}}

    def reset_turn(self, thread_id):
        self.state = {"terminal_error": None, "terminal": None, "context_altered": False,
                      "usage_events": [], "final_messages": [], "fallback_messages": [], "turn_id": None}
        self.notifications = []
        self.recorder.completion = None

    def drain_until_terminal(self, deadline):
        output = v2.encode_output(self.outputs[self.pass_no], self.pass_no, self.ctx)
        if self.pass_no == self.fail_pass and self.per_pass[self.pass_no] == 1:
            output["t"] = []
        if self.bad_quote and self.pass_no == 2:
            output["i"][0]["x"] = "She… waits"
        self.state["final_messages"] = [v2.encode_value(output)]
        self.state["terminal"] = {"status": "completed", "id": f"turn-{self.turns}"}
        self.recorder.completion = (probe.utc_now(), time.monotonic())
        last = {k: 10 for k in USAGE_FIELDS.values()}
        last.update(inputTokens=1000, cachedInputTokens=0 if self.turns == 1 else 900, outputTokens=100,
                    totalTokens=1100, reasoningOutputTokens=20, cacheWriteInputTokens=0)
        self.summed += 1100
        total = {**last, "totalTokens": self.summed, "inputTokens": 1000*self.turns}
        self.state["usage_events"] = [{"last": {**last, "inputTokens": 900}, "total": total},
                                      {"last": last, "total": total, "modelContextWindow": 258400}]
        self.notifications = [{"method": "thread/tokenUsage/updated", "params": {"tokenUsage": u}} for u in self.state["usage_events"]]

    def wait_late_usage(self, seconds):
        pass


def harness(tmp_path, monkeypatch, data, fail_pass=None, bad_quote=False):
    p2, outputs = data
    layout, ctx = v2.encode_input(p2, 2)
    frozen = {"p2_inputs": p2, "p2_wire": layout.text, "codec_context": ctx.as_dict(), "schema": v2.TRANSPORT_SCHEMA,
              "max_attempts": 2, "base": "Fixed base", "developer": "Fixed developer", "prompts": {str(n): "test" for n in range(2, 6)},
              "pricing": {"provider": "codex", "rate": {"input": 2, "cached_input": .1, "cache_write_input": 2.5, "output": 10}}}
    recorder = Recorder(tmp_path)
    session = probe.SessionExperiment(tmp_path, frozen, recorder)
    rpc = FakeRpc(recorder, outputs, ctx, fail_pass, bad_quote)
    monkeypatch.setattr(probe, "validate_params", lambda *args: None)
    session.execute(rpc, 1234)
    return session, rpc


def test_one_thread_four_turns_minimal_controls_and_canonical_inputs(tmp_path, monkeypatch, data):
    session, rpc = harness(tmp_path, monkeypatch, data)
    assert [m for m, _ in rpc.requests] == ["thread/start"] + ["turn/start"] * 4
    turns = [p for m, p in rpc.requests if m == "turn/start"]
    assert {p["threadId"] for p in turns} == {"same-thread"}
    assert all(p["model"] == probe.MODEL and p["effort"] == probe.EFFORT for p in turns)
    assert all(p["outputSchema"] == v2.TRANSPORT_SCHEMA for p in turns)
    assert json.loads(turns[0]["input"][0]["text"])["SOURCE_BLOCKS"]
    for n, params in zip(range(3, 6), turns[1:]):
        control = json.loads(params["input"][0]["text"])
        assert control == {"CACHE_V3_SESSION": 1, "ACCEPTED_PREVIOUS_PASS": n-1, "ACTIVE_PASS": n}
        assert len(params["input"][0]["text"].encode()) < 100
    p2, outputs = data
    assert session.accepted == outputs
    for n in range(2, 6):
        path = tmp_path / f"P{n}_attempt_001"
        actual = read_json(path / "request.semantic.json")["input_payload"]
        assert actual == probe.canonical_inputs(p2, outputs, n)
        assert v2.context_for(actual, n) == session.ctx
        assert read_json(path / "codec.context.json") == session.ctx.as_dict()
        assert read_json(path / "validation.json")["final_validation"] == "passed"
    p5 = read_json(tmp_path / "P5_attempt_001/request.semantic.json")["input_payload"]
    assert "SEMANTIC_AUDIT" not in p5 and "SOURCE_SENTENCES" not in p5


def test_usage_last_not_cumulative_or_snapshot_sum(tmp_path, monkeypatch, data):
    session, _ = harness(tmp_path, monkeypatch, data)
    rows = session.rows
    assert [r["input_tokens"] for r in rows] == [1000]*4
    assert [r["total_tokens"] for r in rows] == [1100]*4
    assert [r["thread_total"]["totalTokens"] for r in rows] == [1100,2200,3300,4400]
    assert probe.aggregate(rows)["input_tokens"] == 4000
    assert probe.aggregate(rows)["total_tokens"] == 4400
    assert probe.aggregate(rows)["cached_input_tokens"] == 2700
    assert rows[-1]["uncached_input_tokens"] == 100
    assert all(r["model_context_window"] == 258400 for r in rows)


def test_same_thread_retry_keeps_invalid_history_and_records_cost(tmp_path, monkeypatch, data):
    session, rpc = harness(tmp_path, monkeypatch, data, fail_pass=3)
    assert len([m for m, _ in rpc.requests if m == "thread/start"]) == 1
    assert len([m for m, _ in rpc.requests if m == "turn/start"]) == 5
    assert len(session.rows) == 5
    assert session.rows[1]["validation"] == "failed"
    assert session.rows[2]["validation"] == "passed"
    text = [p for m, p in rpc.requests if m == "turn/start"][2]["input"][0]["text"]
    assert json.loads(text)["RETRY_OF_TURN_ID"] == "turn-2"
    assert "SOURCE_BLOCKS" not in text
    assert probe.aggregate(session.rows)["total_tokens"] == 5500
    assert all(r["cost_usd"] > 0 for r in session.rows)


def test_quote_repair_acceptance_patch_not_full_artifact(tmp_path, monkeypatch, data):
    p2, output = data
    output[2]["issues"] = [{"sid": "sentence/z", "source_span": "She waits", "type": "style", "meaning": "Wait", "constraint": "Wait", "confidence": "high"}]
    session, rpc = harness(tmp_path, monkeypatch, data, bad_quote=True)
    assert session.rows[0]["deterministic_repairs"] == 1
    control = json.loads([p for m,p in rpc.requests if m == "turn/start"][1]["input"][0]["text"])
    assert control["ACCEPTED_REPAIRS"][0]["array"] == "i"
    assert control["ACCEPTED_REPAIRS"][0]["record"]["x"] == "She waits"
    assert "SEMANTIC_AUDIT" not in control


def test_context_rejects_incomplete_check_domains(data):
    p2, outputs = data
    ctx = v2.context_for(p2, 2)
    inputs = probe.canonical_inputs(p2, outputs, 3)
    inputs["SEMANTIC_AUDIT"]["checks"].pop()
    with pytest.raises(PipelineError, match="maps changed"):
        probe.stable_context(inputs, 3, ctx)


@pytest.mark.parametrize("pass_no", [2,3,4,5])
def test_canonical_dependency_graph_and_immutability(data, pass_no):
    p2, outputs = data
    before = copy.deepcopy((p2,outputs))
    inputs = probe.canonical_inputs(p2,outputs,pass_no)
    assert (p2, outputs) == before
    assert set(inputs)-set(probe.COMMON_FIELDS) == {
        2:{"SOURCE_SENTENCES"},3:{"SEMANTIC_AUDIT"},4:{"SOURCE_SENTENCES","POLISH_DRAFT","SEMANTIC_AUDIT"},5:{"POLISH_DRAFT","CORRECTION_LEDGER"}}[pass_no]


def test_production_path_not_modified_by_offline_harness(tmp_path, monkeypatch, data):
    repository = Path(probe.__file__).resolve().parents[1]
    protected = [repository / "bookpipe/application/pipeline.py", repository / "bookpipe/codex_transport.py",
                 repository / "bookpipe/codex_cache_v2.py", repository / "bookpipe/engine.py", repository / "bookpipe/profiles.py"]
    before = {p: p.read_bytes() for p in protected}
    harness(tmp_path, monkeypatch, data)
    assert all(p.read_bytes() == raw for p,raw in before.items())


def test_evidence_failure_never_retries_model(tmp_path, monkeypatch, data):
    p2, outputs = data
    layout, ctx = v2.encode_input(p2, 2)
    recorder = Recorder(tmp_path)
    frozen = {"p2_inputs": p2, "p2_wire": layout.text, "codec_context": ctx.as_dict(), "schema": v2.TRANSPORT_SCHEMA,
              "max_attempts": 2, "base": "Fixed", "developer": "Fixed", "prompts": {str(n):"test" for n in range(2,6)},
              "pricing": {"provider":"codex", "rate":{"input":2,"cached_input":.1,"cache_write_input":2.5,"output":10}}}
    session = probe.SessionExperiment(tmp_path, frozen, recorder)
    rpc = FakeRpc(recorder, outputs,ctx)
    monkeypatch.setattr(probe,"validate_params",lambda *args:None)
    monkeypatch.setattr(probe,"validate_answer",lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError,match="disk full"):
        session.execute(rpc,1234)
    assert rpc.turns == 1


def test_scratch_guard_rejects_project_destination(tmp_path):
    with pytest.raises(PipelineError,match="outside"):
        probe.prepare(tmp_path,tmp_path/'experiment')


def test_reject_duplicate_keys_and_wrong_pass(data,tmp_path):
    p2, outputs = data
    ctx=v2.context_for(p2,2)
    for answer in ['{"p":2,"p":2}',v2.encode_value(v2.encode_output(outputs[3],3,ctx))]:
        with pytest.raises(PipelineError):
            probe.validate_answer(answer,2,p2,ctx,tmp_path)


def test_missing_usage_stays_unknown():
    assert probe.aggregate([{"input_tokens":None}])["input_tokens"] is None
    assert probe.aggregate([{"input_tokens":None}])["cost_usd"] is None
