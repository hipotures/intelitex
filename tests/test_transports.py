from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bookpipe.catalog import import_catalog
from bookpipe.codex_transport import CodexAppServerClient, _RpcSession
from bookpipe.contracts import preflight_display, preflight_measurement, preflight_metadata
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.engine import response_schema
from bookpipe.openai_transport import OpenAIResponsesClient
from bookpipe.profiles import migrate_settings_file, resolve_profile, validate_profiles, with_profiles
from bookpipe.schemas import SCHEMAS
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, read_json


@pytest.fixture
def quiet_ui():
    with Display(True) as ui:
        yield ui


@pytest.fixture
def openai_server():
    received: list[tuple[str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            received.append((self.path, body))
            if self.path == "/v1/responses/input_tokens":
                if body.get("instructions") == "UNSUPPORTED_COUNT":
                    raw = b'{"error":"unsupported"}'
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                raw = json.dumps({"object": "response.input_tokens", "input_tokens": 41}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if body.get("instructions") == "MALFORMED":
                wire = "data: {not-json\n\n"
                raw = wire.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            usage = {"input_tokens": 41, "input_tokens_details": {"cached_tokens": 7, "cache_write_tokens": 3},
                     "output_tokens": 19, "output_tokens_details": {"reasoning_tokens": 5}, "total_tokens": 60}
            if body.get("instructions") in {"INCOMPLETE", "REFUSAL"}:
                kind = "response.incomplete" if body["instructions"] == "INCOMPLETE" else "response.completed"
                status = "incomplete" if kind == "response.incomplete" else "completed"
                output = [] if kind == "response.incomplete" else [{"type":"message","content":[{"type":"refusal","refusal":"declined"}]}]
                event = {"type": kind, "response": {"id":"resp-fail","status":status,"model":"test-openai",
                         "output":output,"incomplete_details":{"reason":"max_output_tokens"} if status == "incomplete" else None,
                         "usage":usage}}
                wire = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
                raw = wire.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            answer = '{"translations":[{"id":"B1","text":"Przekład."}]}'
            events = [
                {"type": "response.created", "response": {"id": "resp-test", "status": "in_progress", "model": "test-openai"}},
                {"type": "response.output_text.delta", "delta": answer[:20]},
                {"type": "response.output_text.delta", "delta": answer[20:]},
                {"type": "response.completed", "response": {
                    "id": "resp-test", "status": "completed", "model": "test-openai", "output": [],
                    "usage": usage,
                }},
            ]
            wire = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
            raw = wire.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("x-request-id", "req-test")
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield received, server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def test_openai_native_responses_and_full_evidence(tmp_path, openai_server, quiet_ui, monkeypatch):
    received, port = openai_server
    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-proj-this-must-never-be-recorded")
    profile = {
        "profile_name": "remote", "resolved_profile": {"provider": "openai", "credential_env": "TEST_OPENAI_KEY"},
        "provider": "openai", "model": "test-openai", "endpoint": f"http://127.0.0.1:{port}/v1",
        "credential_env": "TEST_OPENAI_KEY", "context_size": 4096, "planning_output_reserve": 100,
        "max_output_tokens": 200, "request_timeout": 5, "reasoning_effort": "low", "options": {},
    }
    client = OpenAIResponsesClient(profile, quiet_ui)
    attempt = tmp_path / "attempt_001"
    recorder = AttemptRecorder(attempt, {"provider": "openai", "requested_model": "test-openai"})
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "text": "Text with ordinary token wording."}]}
    schema = SCHEMAS[5]
    body = client.body("Trusted instructions", inputs, schema, 5)
    try:
        assert client.preflight(body, recorder) == 41
        answer, meta = client.generate(body, attempt, recorder)
    finally:
        client.close()
    assert json.loads(answer)["translations"][0]["id"] == "B1"
    assert meta["reported_model"] == "test-openai"
    assert body["store"] is False and body["truncation"] == "disabled" and body["tools"] == []
    assert "previous_response_id" not in body and body["text"]["format"]["strict"] is True
    assert [path for path, _ in received] == ["/v1/responses/input_tokens", "/v1/responses"]
    usage = read_json(attempt / "usage.json")
    assert usage["cached_input_tokens"] == 7
    assert usage["cache_write_input_tokens"] == 3
    assert usage["reasoning_output_tokens"] == 5
    retained = "\n".join(path.read_text(encoding="utf-8") for path in attempt.iterdir() if path.is_file())
    assert "sk-proj-this-must-never-be-recorded" not in retained
    assert "ordinary token wording" in retained


@pytest.mark.parametrize("instructions,match", [
    ("INCOMPLETE", "terminal status"),
    ("REFUSAL", "refused"),
    ("MALFORMED", "Malformed"),
])
def test_openai_terminal_failures_are_not_success(tmp_path, openai_server, quiet_ui, monkeypatch, instructions, match):
    _, port = openai_server
    monkeypatch.setenv("TEST_OPENAI_KEY", "fake")
    profile = {
        "profile_name":"remote", "resolved_profile":{}, "provider":"openai", "model":"test-openai",
        "endpoint":f"http://127.0.0.1:{port}/v1", "credential_env":"TEST_OPENAI_KEY",
        "context_size":4096, "planning_output_reserve":100, "max_output_tokens":200,
        "request_timeout":5, "reasoning_effort":None, "options":{},
    }
    client = OpenAIResponsesClient(profile, quiet_ui)
    attempt = tmp_path / instructions
    recorder = AttemptRecorder(attempt, {"provider":"openai"})
    body = client.body(instructions, {"x":1}, {"type":"object"}, 1)
    client.preflight(body, recorder)
    with pytest.raises(PipelineError, match=match):
        client.generate(body, attempt, recorder)
    if instructions != "MALFORMED":
        assert read_json(attempt / "usage.json")["total_tokens"] == 60
    assert not (attempt / "answer.txt").exists()
    client.close()


def test_openai_unsupported_token_count_is_not_zero(tmp_path, openai_server, quiet_ui, monkeypatch):
    _, port = openai_server
    monkeypatch.setenv("TEST_OPENAI_KEY", "fake")
    profile = {"profile_name":"remote","resolved_profile":{},"provider":"openai","model":"test-openai",
               "endpoint":f"http://127.0.0.1:{port}/v1","credential_env":"TEST_OPENAI_KEY","context_size":4096,
               "planning_output_reserve":100,"max_output_tokens":None,"request_timeout":5,"reasoning_effort":None,"options":{}}
    client = OpenAIResponsesClient(profile, quiet_ui)
    recorder = AttemptRecorder(tmp_path / "count", {"provider":"openai"})
    with pytest.raises(PipelineError, match="unavailable"):
        client.preflight(client.body("UNSUPPORTED_COUNT", {"x":1}, {"type":"object"}, 1), recorder)
    assert not (tmp_path / "count" / "usage.json").exists()
    client.close()


def _write_fake_codex(path: Path) -> None:
    script = r'''#!/usr/bin/env python3
import json
import os
import pathlib
import sys

skills_disabled = False
thread_path = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions" / "rollout.jsonl"

def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()

for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send({"method":"unknown/early","params":{"value":1}})
        send({"id":request_id,"result":{"userAgent":"fake-codex/1"}})
    elif method == "initialized":
        continue
    elif method == "skills/list":
        skills = [{"name":"bundled","path":"/fake/SKILL.md","enabled":not skills_disabled}]
        send({"id":request_id,"result":{"data":[{"cwd":message["params"]["cwds"][0],"skills":skills,"errors":[]}]}})
    elif method == "skills/config/write":
        skills_disabled = True
        send({"id":request_id,"result":{"effectiveEnabled":False}})
    elif method == "thread/start":
        assert message["params"]["ephemeral"] is False
        assert message["params"]["developerInstructions"] == "Trusted pass instructions"
        assert message["params"]["dynamicTools"] == []
        send({"id":request_id,"result":{"thread":{"id":"thread-1","sessionId":"session-1","path":str(thread_path)}}})
    elif method == "turn/start":
        assert message["params"]["input"][0]["text"] == '{"SOURCE":"payload"}'
        send({"method":"item/agentMessage/delta","params":{"threadId":"thread-1","turnId":"turn-1","itemId":"msg-1","delta":"partial"}})
        send({"id":request_id,"result":{"turn":{"id":"turn-1","model":"gpt-5.6-luna","effort":"low"}}})
        send({"method":"item/completed","params":{"threadId":"thread-1","turnId":"turn-1","item":{"id":"comment","type":"agentMessage","phase":"commentary","text":"not final"}}})
        send({"id":900,"method":"unsupported/request","params":{"token":"secret"}})
    elif request_id == 900 and "error" in message:
        send({"method":"item/completed","params":{"threadId":"thread-1","turnId":"turn-1","item":{"id":"final","type":"agentMessage","phase":"final_answer","text":json.dumps({"ok":True}, separators=(",", ":"))}}})
        send({"method":"thread/tokenUsage/updated","params":{"threadId":"thread-1","turnId":"turn-1","tokenUsage":{"last":{"inputTokens":10,"cachedInputTokens":2,"cacheWriteInputTokens":1,"outputTokens":4,"reasoningOutputTokens":3,"totalTokens":14},"total":{"inputTokens":10,"cachedInputTokens":2,"cacheWriteInputTokens":1,"outputTokens":4,"reasoningOutputTokens":3,"totalTokens":14},"modelContextWindow":1000}}})
        send({"method":"turn/completed","params":{"threadId":"thread-1","turnId":"turn-1","turn":{"id":"turn-1","status":"completed","model":"gpt-5.6-luna","effort":"low"}}})
        send({"method":"thread/tokenUsage/updated","params":{"threadId":"thread-1","turnId":"turn-1","tokenUsage":{"last":{"inputTokens":11,"cachedInputTokens":3,"cacheWriteInputTokens":2,"outputTokens":5,"reasoningOutputTokens":4,"totalTokens":16},"total":{"inputTokens":11,"cachedInputTokens":3,"cacheWriteInputTokens":2,"outputTokens":5,"reasoningOutputTokens":4,"totalTokens":16},"modelContextWindow":1000}}})

thread_path.parent.mkdir(parents=True, exist_ok=True)
thread_path.write_text('{"session_meta":{"payload":{"base_instructions":"thin"}}}\n', encoding="utf-8")
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_codex_interleaving_isolation_usage_and_rollout(tmp_path, quiet_ui):
    executable = tmp_path / "fake-codex"
    _write_fake_codex(executable)
    project = tmp_path / "project"
    project.mkdir()
    profile = {
        "profile_name": "codex-low", "resolved_profile": {"provider": "codex", "reasoning_effort": "low"},
        "provider": "codex", "model": "gpt-5.6-luna", "context_size": 10000,
        "planning_output_reserve": 100, "request_timeout": 5, "reasoning_effort": "low",
        "executable": str(executable), "options": {"late_usage_wait": 0.2, "p1_wire_format": "canonical"},
        "project_root": str(project),
    }
    client = CodexAppServerClient(profile, quiet_ui)
    attempt = project / "artifacts" / "attempt_001"
    recorder = AttemptRecorder(attempt, {"provider": "codex", "requested_model": "gpt-5.6-luna"})
    body = client.body("Trusted pass instructions", {"SOURCE": "payload"}, {"type": "object"}, 1)
    count = client.preflight(body, recorder)
    assert count > 0
    measurement = preflight_measurement(client, count)
    assert measurement["unit"] == "utf8_bytes"
    assert preflight_display(measurement).endswith("UTF-8 bytes (not tokens)")
    assert preflight_metadata(measurement) == {"input_preflight": measurement}
    answer, meta = client.generate(body, attempt, recorder)
    assert json.loads(answer) == {"ok": True}
    assert meta["thread_id"] == "thread-1" and meta["session_id"] == "session-1"
    assert meta["requested_model"] == meta["reported_model"] == "gpt-5.6-luna"
    assert meta["requested_effort"] == meta["reported_effort"] == "low"
    assert (attempt / meta["rollout_copy"]).is_file()
    usage = read_json(attempt / "usage.json")
    assert usage["input_tokens"] == 11 and usage["total_tokens"] == 16
    trace = (attempt / "transport.jsonl").read_text(encoding="utf-8")
    assert "unknown/early" in trace
    assert "unsupported_server_request_reply" in trace
    assert "not final" in trace
    assert not list((project / ".runtime").rglob("SKILL.md"))


def test_recorder_redacts_credentials_but_preserves_source(tmp_path):
    recorder = AttemptRecorder(tmp_path / "attempt", {"provider": "test"})
    source = "A character says the word token in ordinary prose."
    recorder.semantic({"source": source}, {"type": "object"})
    recorder.event("outbound", "headers", {"Authorization": "Bearer secret-value", "source": source})
    retained = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "attempt").iterdir())
    assert "secret-value" not in retained
    assert source in retained


def test_catalog_import_failure_preserves_active_catalog(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    valid = tmp_path / "valid.json"
    atomic_json(valid, {"schema_version": 1, "models": [{"provider": "codex", "id": "example", "context_tokens": 1000,
                                                          "efforts": ["low"], "pricing": None, "capabilities": {}}]})
    import_catalog(valid, project)
    before = (project / "catalog" / "models.json").read_bytes()
    invalid = tmp_path / "invalid.json"
    atomic_json(invalid, {"schema_version": 1, "models": [{"provider": "bad", "id": "x"}]})
    with pytest.raises(PipelineError):
        import_catalog(invalid, project)
    assert (project / "catalog" / "models.json").read_bytes() == before


def test_legacy_profile_migration_creates_sqlite_backup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    store = Store(project)
    with store.db:
        store.set("approved", True)
    store.close()
    legacy = {
        "format_version": 1, "host": "127.0.0.1", "port": 8080, "model": "local-model",
        "context_size": 4096, "thinking": "off", "request_timeout": 30, "seed": 42,
        "request_extra": {}, "passes": {str(i): {"temperature": 0.1, "max_tokens": 100} for i in range(1, 6)},
    }
    atomic_json(project / "settings.json", legacy)
    migrated, backup = migrate_settings_file(project, legacy)
    assert backup and backup.is_file()
    assert migrated["profiles"]["local"]["model"] == "local-model"
    reopened = Store(project)
    assert reopened.get("approved") is True
    reopened.close()


def test_all_real_pass_schemas_compile_for_cloud_transports(tmp_path, quiet_ui, monkeypatch):
    monkeypatch.setenv("TEST_OPENAI_KEY", "not-a-real-key")
    openai = OpenAIResponsesClient({
        "profile_name": "o", "resolved_profile": {}, "provider": "openai", "model": "opaque/model:id",
        "endpoint": "http://127.0.0.1:9/v1", "credential_env": "TEST_OPENAI_KEY", "context_size": 100000,
        "planning_output_reserve": 1000, "max_output_tokens": None, "request_timeout": 1,
        "reasoning_effort": None, "options": {},
    }, quiet_ui)
    codex = CodexAppServerClient({
        "profile_name": "c", "resolved_profile": {}, "provider": "codex", "model": "opaque/model:id",
        "context_size": 100000, "planning_output_reserve": 1000, "request_timeout": 1,
        "reasoning_effort": "low", "executable": "codex", "options": {"p1_wire_format": "canonical"},
        "project_root": str(tmp_path),
    }, quiet_ui)
    try:
        for pass_no in range(1, 6):
            schema = response_schema(pass_no, {"SOURCE_BLOCKS": [{"id": "B1", "text": "x"}]})
            assert openai.body("pass", {"SOURCE_BLOCKS": []}, schema, pass_no)["text"]["format"]["schema"] == schema
            assert codex.body("pass", {"SOURCE_BLOCKS": []}, schema, pass_no)["output_schema"] == schema
    finally:
        openai.close()


def test_profile_precedence_and_codex_capability_validation(tmp_path):
    base = {
        "passes": {str(i): {"temperature": 0.1, "max_tokens": 100} for i in range(1, 6)},
        "request_timeout": 30,
        "profiles": {
            "default": {"provider":"llamacpp","enabled":True,"model":None,"endpoint":"127.0.0.1",
                        "context_size":1000,"planning_output_reserve":100,"options":{}},
            "saved": {"provider":"llamacpp","enabled":True,"model":None,"endpoint":"127.0.0.1",
                      "context_size":1000,"planning_output_reserve":100,"options":{}},
            "command": {"provider":"llamacpp","enabled":True,"model":None,"endpoint":"127.0.0.1",
                        "context_size":1000,"planning_output_reserve":100,"options":{}},
        },
        "default_profile":"default", "pass_profiles":{"3":"saved"},
    }
    assert resolve_profile(base, 3, project=tmp_path)[0] == "saved"
    assert resolve_profile(base, 3, command_profile="command", project=tmp_path)[0] == "command"
    assert resolve_profile(base, 3, command_profile="command", command_pass_profiles={3:"default"}, project=tmp_path)[0] == "default"
    bad = json.loads(json.dumps(base))
    bad["profiles"]["command"] = {"provider":"codex","enabled":True,"model":"opaque","context_size":1000,
                                      "planning_output_reserve":100,"max_output_tokens":10,"options":{}}
    with pytest.raises(PipelineError, match="no verified hard output-token cap"):
        validate_profiles(bad, tmp_path)


def test_evidence_creation_failure_happens_before_provider_contact(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("bookpipe.evidence.atomic_json", fail)
    with pytest.raises(EvidenceError, match="Cannot write evidence"):
        AttemptRecorder(tmp_path / "attempt", {"provider":"test"})


def test_midstream_evidence_failure_is_visible(tmp_path, monkeypatch):
    recorder = AttemptRecorder(tmp_path / "attempt", {"provider":"test"})
    original = recorder._open_append
    def fail(name):
        if name == "transport.jsonl":
            raise OSError("permission denied")
        return original(name)
    monkeypatch.setattr(recorder, "_open_append", fail)
    with pytest.raises(EvidenceError, match="Cannot append transport evidence"):
        recorder.event("inbound", "delta", {"text":"partial"})


def test_codex_retry_system_error_and_compaction_are_terminally_distinct():
    rpc = object.__new__(_RpcSession)
    rpc.notifications = []
    rpc.state = {"thread_id":None,"turn_id":None,"usage_events":[],"terminal":None,
                 "terminal_error":None,"context_altered":False,"final_messages":[],"fallback_messages":[]}
    rpc.dispatch({"method":"error","params":{"willRetry":True,"message":"temporary"}})
    assert rpc.state["terminal_error"] is None
    rpc.dispatch({"method":"error","params":{"willRetry":False,"message":"final"}})
    assert rpc.state["terminal_error"]["message"] == "final"
    rpc.state["terminal_error"] = None
    rpc.dispatch({"method":"thread/status/changed","params":{"status":{"type":"systemError"}}})
    assert rpc.state["terminal_error"] is not None
    rpc.dispatch({"method":"thread/compacted","params":{"threadId":"t","turnId":"u"}})
    assert rpc.state["context_altered"] is True


def test_codex_cleanup_reaps_stalled_process_group():
    proc = subprocess.Popen(["sh", "-c", "sleep 60"], start_new_session=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    CodexAppServerClient._cleanup_process(proc, graceful=False)
    assert proc.poll() is not None
