"""Real process/HTTP lifecycle tests, with no live provider calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import http.client
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from bookpipe.application.commands import ImportBookCommand, PublicationStatusCommand, StatusCommand, ReviewSessionCommand
from bookpipe.application.workspaces import WorkspaceQueries
from bookpipe.bootstrap import create_application
from bookpipe.cli import parser
from bookpipe.infrastructure.read_store import ReadStore
from bookpipe.progress import ProgressEvent
from bookpipe.runtime.models import ACTIVE, Job, JobSpec
from bookpipe.runtime.protocol import JsonlProgressSink, decode
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.supervisor import JobConflict, JobSupervisor
from bookpipe.runtime.worker import execute
from bookpipe.server.http import IntelitexHTTPServer, BODY_LIMIT
from bookpipe.server.service import ServerService
from bookpipe.store import Store
from bookpipe.util import PipelineError, project_lock
from test_application import LocalImportPool
from test_pipeline import server

HELPER = Path(__file__).parent / "helpers" / "runtime_worker.py"


def wait_for(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for worker state")


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    for ident in ("a", "b", "c", "success", "fail", "stubborn"):
        (root / ident).mkdir()
        (root / ident / "book.json").write_text("{}")
    registry = JobRegistry(tmp_path / "runtime" / "jobs.sqlite3", event_limit=30)
    def command(spec):
        mode = spec.workspace_id if spec.workspace_id in {"success", "fail", "stubborn"} else "hold"
        return [sys.executable, "-u", str(HELPER), mode]
    supervisor = JobSupervisor(registry, root, command_factory=command, interrupt_grace=0.3, terminate_grace=0.2)
    try:
        yield root, registry, supervisor
    finally:
        supervisor.shutdown()
        registry.close()


def spec(root, ident="a", operation="translate"):
    return JobSpec(str(root), ident, str(root / ident), operation)


def running(supervisor, job):
    def ready():
        return any(event["event"]["kind"] == "provider_usage_update" for event in
                   supervisor.registry.events(0, workspace_root=supervisor.workspace_root, job_id=job.job_id))
    wait_for(ready)
    assert supervisor.get(job.job_id).state == "running"


def terminal(supervisor, job):
    return wait_for(lambda: (current if (current := supervisor.get(job.job_id)).state not in ACTIVE else None))


def test_concurrency_conflict_and_isolated_cancellation(runtime):
    root, registry, supervisor = runtime
    jobs = [supervisor.start(spec(root, ident, operation))
            for ident, operation in (("a", "analyze"), ("b", "translate"), ("c", "translate"))]
    for job in jobs:
        running(supervisor, job)
    assert len({job.pid for job in jobs}) == 3
    with pytest.raises(JobConflict):
        supervisor.start(spec(root, "a"))
    # Aliases to the same resolved workspace cannot bypass mutual exclusion.
    (root / "alias").symlink_to(root / "a", target_is_directory=True)
    with pytest.raises(JobConflict):
        supervisor.start(JobSpec(str(root), "alias", str(root / "a"), "translate"))
    supervisor.stop(jobs[0].job_id)
    stopped = terminal(supervisor, jobs[0])
    assert stopped.state == "cancelled" and stopped.exit_code == 130
    assert all(supervisor.get(job.job_id).state == "running" for job in jobs[1:])
    with project_lock(root / "a"):
        pass
    # A terminal registry state can precede detached-child cleanup; ownership is
    # intentionally retained until the supervisor finishes that cleanup.
    wait_for(lambda: not supervisor.owns_project(root / "a"))
    resumed = supervisor.start(spec(root, "a"))
    running(supervisor, resumed)


def test_atomic_conflict_under_simultaneous_starts(runtime):
    root, _, supervisor = runtime
    def start():
        try:
            return supervisor.start(spec(root))
        except JobConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: start(), range(8)))
    assert sum(value is not None for value in results) == 1


def test_reload_waits_for_owned_worker_checkpoint_and_preserves_resume_spec(runtime):
    root, _, supervisor = runtime
    supervisor.command_factory = lambda spec: [sys.executable, "-u", str(HELPER), "reloadable"]
    job = supervisor.start(spec(root, "a"))
    running(supervisor, job)
    supervisor.request_reload()
    assert supervisor.closing
    with pytest.raises(JobConflict):
        supervisor.start(spec(root, "b"))
    assert terminal(supervisor, job).state == "cancelled"
    wait_for(supervisor.reload_drained)
    assert supervisor.reload_specs == [spec(root, "a")]


def test_reload_signal_waits_until_worker_has_initialized(runtime):
    root, _, supervisor = runtime
    supervisor.command_factory = lambda spec: [sys.executable, "-u", str(HELPER), "late_ready"]
    job = supervisor.start(spec(root, "a"))
    supervisor.request_reload()
    with supervisor.lock:
        owned = supervisor.owned[job.job_id]
        assert owned.reload_requested and not owned.reload_signalled
    assert terminal(supervisor, job).state == "cancelled"
    wait_for(supervisor.reload_drained)
    assert supervisor.reload_specs == [spec(root, "a")]


def test_success_failure_events_and_failure_isolation(runtime):
    root, registry, supervisor = runtime
    background = supervisor.start(spec(root, "b"))
    running(supervisor, background)
    successful = terminal(supervisor, supervisor.start(spec(root, "success")))
    failed = terminal(supervisor, supervisor.start(spec(root, "fail")))
    assert successful.state == "succeeded" and successful.exit_code == 0
    assert failed.state == "failed" and failed.error["type"] == "RuntimeError"
    assert supervisor.get(background.job_id).state == "running"
    events = registry.events(0, workspace_root=str(root), job_id=successful.job_id)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)
    waiting = next(event["event"] for event in events if event["event"]["kind"] == "provider_waiting")
    assert waiting["current"] == 1 and waiting["total"] == 3
    assert waiting["values"]["input_unit"] == "utf8_bytes" and waiting["values"]["input_value"] == 4321
    assert waiting["values"]["task_key"] == "P2:c1" and waiting["values"]["requested_model"] == "fixture-model"
    assert any(event["event"]["kind"] == "publication_completed" for event in events)
    assert "sk-private-secret" not in json.dumps([asdict(j) for j in registry.list()])


def test_shutdown_stops_all_owned_workers_and_escalates(runtime):
    root, registry, supervisor = runtime
    jobs = [supervisor.start(spec(root, ident)) for ident in ("a", "b", "stubborn")]
    for job in jobs:
        running(supervisor, job)
    owned = dict(supervisor.owned)
    event_counts = {job.job_id: len(registry.events(0, workspace_root=str(root), job_id=job.job_id))
                    for job in jobs}
    supervisor.begin_shutdown()
    assert supervisor.closing and supervisor.broker.closed
    with pytest.raises(JobConflict):
        supervisor.start(spec(root, "c"))
    supervisor.shutdown()
    assert not supervisor.owned
    for job in jobs:
        assert supervisor.get(job.job_id).state == "cancelled"
        assert owned[job.job_id].process.poll() is not None
        assert len(registry.events(0, workspace_root=str(root), job_id=job.job_id)) > event_counts[job.job_id]
    assert supervisor.get(jobs[-1].job_id).exit_code == -signal.SIGKILL
    with pytest.raises(JobConflict):
        supervisor.start(spec(root, "c"))


def test_stale_jobs_abandoned_registry_exclusive_and_events_bounded(tmp_path):
    path = tmp_path / "registry.sqlite3"
    registry = JobRegistry(path, event_limit=3)
    with pytest.raises(PipelineError, match="Another Intelitex server"):
        JobRegistry(path)
    job = Job("old", str(tmp_path), "a", str(tmp_path / "a"), "translate", state="running", pid=999999)
    registry.save(job)
    for i in range(7):
        registry.append("old", {"kind": "usage", "values": {"i": i}})
    events = registry.events(0, workspace_root=str(tmp_path))
    assert [e["sequence"] for e in events] == [5, 6, 7]
    cursor = registry.cursor()
    registry.close()
    registry = JobRegistry(path)
    try:
        job = registry.get("old")
        assert job.state == "abandoned" and job.finished_at and job.error["type"] == "ServerRestart"
        assert registry.cursor() == cursor and job.sequence == 7
    finally:
        registry.close()


def imported_project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "chapter.html").write_text("<h1>One</h1><p>A deterministic fixture paragraph.</p>")
    root = tmp_path / "imported"
    app = create_application(provider_factory=LocalImportPool)
    app.projects.import_book(ImportBookCommand(root, source))
    return app, root


def test_queries_work_while_other_process_holds_writer_lock(tmp_path):
    app, root = imported_project(tmp_path)
    command = [sys.executable, "-u", "-c", "from pathlib import Path; import sys,time; from bookpipe.util import project_lock;\nwith project_lock(Path(sys.argv[1])):\n print('locked',flush=True); time.sleep(60)", str(root)]
    worker = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    try:
        assert worker.stdout.readline().strip() == "locked"
        with pytest.raises(PipelineError, match="Another process"):
            with project_lock(root):
                pass
        status = app.projects.status(StatusCommand(root))
        publication = app.publishing.status(PublicationStatusCommand(root))
        assert not status.analysis_complete and publication.state == "not_ready"
        from bookpipe.application.commands import UsageByUnitCommand
        assert app.operations.usage_by_unit(UsageByUnitCommand(root)).units == ()
    finally:
        worker.terminate()
        worker.wait(timeout=5)
        worker.stdout.close()


def test_read_snapshot_is_read_only_and_stable_during_commit(tmp_path):
    app, root = imported_project(tmp_path)
    reader = ReadStore(root)
    writer = Store(root)
    try:
        assert not reader.get("analysis_done")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.db.execute("INSERT INTO kv VALUES ('unexpected','true')")
        writer.finish_analysis()
        assert not reader.get("analysis_done")
    finally:
        reader.close()
        writer.close()
    assert app.projects.status(StatusCommand(root)).analysis_complete
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(sqlite3.OperationalError):
        ReadStore(missing)
    assert not (missing / "state.sqlite3").exists()


def test_review_browser_session_does_not_own_writer_lock(tmp_path):
    app, root = imported_project(tmp_path)
    store = Store(root)
    store.finish_analysis()
    store.close()
    with app.review.open_session(ReviewSessionCommand(root)) as session:
        with project_lock(root):
            with pytest.raises(PipelineError, match="Another process"):
                session.set_confirmed(True)
        assert session.load()["terms"] == []


@pytest.fixture
def http_server(runtime):
    root, _, supervisor = runtime
    app = create_application()
    server = IntelitexHTTPServer(("127.0.0.1", 0), ServerService(app, WorkspaceQueries(app.projects, root), supervisor))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        yield server, supervisor, root
    finally:
        supervisor.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    options = {"Content-Type": "application/json", **(headers or {})}
    connection.request(method, path, json.dumps(body) if body is not None else None, options)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def read_sse(response):
    frame = {}
    while True:
        line = response.readline().decode().rstrip('\r\n')
        if not line:
            return frame
        if ': ' in line:
            key, value = line.split(': ', 1)
            frame[key] = json.loads(value) if key == "data" else value


def test_http_start_sse_disconnect_and_reconnect(http_server):
    server, supervisor, root = http_server
    assert request(server, "GET", "/api/health")[0] == 200
    before = time.monotonic()
    code, job = request(server, "POST", "/api/workspaces/a/jobs", {"operation": "translate"})
    assert code == 202 and time.monotonic() - before < 2
    running(supervisor, supervisor.get(job["job_id"]))
    assert request(server, "POST", "/api/workspaces/a/jobs", {"operation": "translate"})[0] == 409
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request("GET", "/api/events?job_id=" + job["job_id"], headers={"Last-Event-ID": "0"})
    response = connection.getresponse()
    assert response.status == 200 and response.getheader("Content-Type") == "text/event-stream"
    snapshot = read_sse(response)
    assert snapshot["event"] == "snapshot" and snapshot["data"]["jobs"][0]["job_id"] == job["job_id"]
    first = read_sse(response)
    assert first["event"] == "progress" and first["data"]["sequence"] == 1
    response.close()
    connection.close()
    assert supervisor.get(job["job_id"]).state == "running"
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request("GET", "/api/events?job_id=" + job["job_id"], headers={"Last-Event-ID": first["id"]})
    response = connection.getresponse()
    assert read_sse(response)["event"] == "snapshot"
    replay = read_sse(response)
    assert int(replay["id"]) > int(first["id"])
    assert replay["data"]["sequence"] == 2
    response.close()
    connection.close()
    assert request(server, "GET", "/api/jobs/" + job["job_id"])[1]["state"] == "running"
    assert len(request(server, "GET", "/api/jobs")[1]["jobs"]) == 1
    assert request(server, "POST", "/api/jobs/" + job["job_id"] + "/stop", {})[0] == 202
    assert terminal(supervisor, supervisor.get(job["job_id"])).state == "cancelled"


@pytest.mark.parametrize("ident", ["..", "%2e%2e", "%2Fetc", "a%2F..%2F..", "a%5C..", "outside"])
def test_http_workspace_traversal_rejected(http_server, tmp_path, ident):
    server, _, root = http_server
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    assert request(server, "POST", f"/api/workspaces/{ident}/jobs", {"operation": "analyze"})[0] == 400


def test_http_origin_host_body_and_operation_validation(http_server):
    server, _, _ = http_server
    path = "/api/workspaces/a/jobs"
    assert request(server, "POST", path, {"operation": "translate"}, {"Origin": "http://evil.example"})[0] == 403
    assert request(server, "GET", "/api/health", headers={"Host": "evil.example"})[0] == 403
    assert request(server, "POST", path, {"operation": "translate"}, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert request(server, "POST", path, {"operation": "translate"}, {"Content-Type": "text/plain"})[0] == 400
    assert request(server, "POST", path, {"operation": "translate", "extra": "x" * BODY_LIMIT})[0] == 413
    for payload in ({}, {"operation": "shell"}, {"operation": "translate", "project": "/etc"},
                    {"operation": "translate", "chunk_limit": True}, {"operation": "publish", "profile": "x"}):
        assert request(server, "POST", path, payload)[0] == 400


def test_production_worker_invalid_project_becomes_structured_failure(runtime):
    root, _, _ = runtime
    process = subprocess.run([sys.executable, "-m", "bookpipe.runtime.worker"],
                             input=json.dumps(asdict(spec(root))), capture_output=True, text=True, timeout=10)
    assert process.returncode == 1
    frame = decode(process.stdout)
    assert frame["type"] == "failure" and "traceback" not in process.stdout.lower()


def test_worker_invoke_retains_automatic_publish_in_same_use_case(runtime):
    root, _, _ = runtime
    calls = []
    def translate(command):
        calls.append(command)
    stream = io.StringIO()
    app = SimpleNamespace(pipeline=SimpleNamespace(translate=translate))
    assert execute(spec(root), JsonlProgressSink(stream), lambda sink: app) == 0
    assert len(calls) == 1 and calls[0].chunk_limit == 0
    assert decode(stream.getvalue())["type"] == "result"
    # No separate publication use case is invoked by the worker.


def test_serve_cli_does_not_require_project(tmp_path):
    args = parser().parse_args(["serve", "--workspace-root", str(tmp_path)])
    assert args.bind == "127.0.0.1" and args.port == 8780
    assert not hasattr(args, "project")


def test_http_status_usage_and_live_sse_while_worker_active(http_server, tmp_path):
    server, supervisor, root = http_server
    source = tmp_path / "actual-source"
    source.mkdir()
    (source / "chapter.html").write_text("<h1>One</h1><p>Offline test text.</p>")
    (root / "a" / "book.json").unlink()
    create_application(provider_factory=LocalImportPool).projects.import_book(ImportBookCommand(root / "a", source))
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request("GET", "/api/events?workspace_id=a")
    response = connection.getresponse()
    assert read_sse(response)["event"] == "snapshot"
    code, job = request(server, "POST", "/api/workspaces/a/jobs", {"operation": "analyze"})
    assert code == 202
    while True:
        frame = read_sse(response)
        if frame.get("data", {}).get("event", {}).get("kind") == "provider_waiting":
            break
    running(supervisor, supervisor.get(job["job_id"]))
    code, workspace = request(server, "GET", "/api/workspaces/a")
    assert code == 200 and workspace["active_job"]["job_id"] == job["job_id"]
    assert workspace["status"]["analysis_complete"] is False
    assert workspace["status"]["publication"]["state"] == "not_ready"
    assert request(server, "GET", "/api/workspaces/a/usage")[0] == 200
    response.close()
    connection.close()
    assert supervisor.get(job["job_id"]).state == "running"


def test_immediate_stop_and_unexpected_exit(runtime):
    root, _, supervisor = runtime
    job = supervisor.start(spec(root))
    supervisor.stop(job.job_id)
    assert terminal(supervisor, job).state == "cancelled"
    job = supervisor.start(spec(root, "b"))
    running(supervisor, job)
    supervisor.owned[job.job_id].process.kill()
    failed = terminal(supervisor, job)
    assert failed.state == "failed" and failed.error["type"] == "WorkerExit"


def test_explicit_publish_uses_real_worker_and_application(tmp_path):
    app, root = imported_project(tmp_path)
    value = JobSpec(str(root.parent), root.name, str(root), "publish")
    process = subprocess.run([sys.executable, "-m", "bookpipe.runtime.worker"],
                             input=json.dumps(asdict(value)), capture_output=True, text=True, timeout=10)
    frames = [decode(line + '\n') for line in process.stdout.splitlines()]
    assert process.returncode == 1  # P1 has not completed; the real publish gate refused.
    assert frames[0]["event"]["kind"] == "publication_failed"
    assert frames[-1]["type"] == "failure" and frames[-1]["error"]["type"] == "PipelineError"
    assert (root / "publication.json").is_file()


def test_broker_after_shutdown_does_not_touch_closed_registry(tmp_path):
    from bookpipe.runtime.events import EventBroker
    registry = JobRegistry(tmp_path / "jobs.sqlite3")
    broker = EventBroker(registry)
    broker.close()
    registry.close()
    assert broker.read(0, workspace_root=str(tmp_path)) == []


def test_progress_protocol_preserves_metadata_and_excludes_private_payloads():
    stream = io.StringIO()
    event = ProgressEvent("provider_waiting", current=3, total=7, message="private response", values={
        "pass_no": 2, "input_value": 1234, "input_unit": "utf8_bytes", "task_key": "P2:c1",
        "prompt": "secret prompt", "response": "secret response", "authorization": "secret auth",
        "error": "provider error with credentials",
    })
    JsonlProgressSink(stream).emit(event)
    wire = stream.getvalue()
    assert "private response" not in wire and "secret" not in wire and "credentials" not in wire
    decoded = decode(wire)["event"]
    assert decoded["values"]["input_unit"] == "utf8_bytes"
    assert decoded["current"] == 3 and decoded["total"] == 7


def test_public_job_state_and_safe_worker_failure_code(tmp_path):
    import httpx
    from bookpipe.runtime.protocol import public_envelope
    state = public_envelope({"id": 1, "job_id": "job", "workspace_id": "w", "sequence": 1,
                             "timestamp": "2026-01-01T00:00:00Z",
                             "event": {"kind": "job_state", "values": {"state": "failed", "private": "secret"}}})
    assert state["event"]["values"] == {"state": "failed"}
    frame = decode('{"type":"failure","error":{"type":"PipelineError","code":"local_model_unavailable","message":"secret"}}\n')
    assert frame["error"]["code"] == "local_model_unavailable"
    assert "secret" not in json.dumps(frame)
    public = Job("j", str(tmp_path), "w", str(tmp_path), "translate", state="failed",
                 error=frame["error"]).public()
    assert "local model server is unavailable" in public["error"]["message"]
    project = tmp_path / 'w'
    project.mkdir()
    frames = []

    def disconnected(_sink):
        try:
            raise httpx.ConnectError('private host or token')
        except httpx.ConnectError as exc:
            raise PipelineError('Cannot discover local model at private host') from exc

    assert execute(JobSpec(str(tmp_path), 'w', str(project), 'translate'),
                   SimpleNamespace(send=frames.append), disconnected) == 1
    assert frames[0]['error']['code'] == 'local_model_unavailable'
    assert 'private host' not in json.dumps(frames)

    frames.clear()

    def invalid_draft(_sink):
        raise PipelineError('P4 failed validation. Artifacts: /private/path\n'
                            'draft_span is not found in the specified draft block.')

    assert execute(JobSpec(str(tmp_path), 'w', str(project), 'translate'),
                   SimpleNamespace(send=frames.append), invalid_draft) == 1
    safe_frame = decode(json.dumps(frames[0]) + '\n')
    assert safe_frame['error']['code'] == 'draft_span_mismatch'
    assert '/private/path' not in json.dumps(safe_frame)
    public = Job('j', str(tmp_path), 'w', str(project), 'translate', state='failed',
                 error=safe_frame['error']).public()
    assert 'P4 failed validation' in public['error']['message']

    frames.clear()

    def missing_blocks(_sink):
        raise PipelineError("P3 failed validation. Artifacts: /private/path\n"
                            "ID coverage mismatch: missing=['B0000113'], unexpected=[], duplicates=0")

    assert execute(JobSpec(str(tmp_path), 'w', str(project), 'translate'),
                   SimpleNamespace(send=frames.append), missing_blocks) == 1
    safe_frame = decode(json.dumps(frames[0]) + '\n')
    assert safe_frame['error']['code'] == 'p3_id_coverage'
    assert 'B0000113' not in json.dumps(safe_frame)
    public = Job('j', str(tmp_path), 'w', str(project), 'translate', state='failed',
                 error=safe_frame['error']).public()
    assert 'did not translate every source block' in public['error']['message']


def test_worker_real_pipeline_resumes_checkpoints_and_auto_publishes(tmp_path, server):
    from test_pipeline import make_epub_source
    from bookpipe.cli import main
    from bookpipe.util import atomic_json, read_json
    state, port = server
    source = make_epub_source(tmp_path)
    root = tmp_path / "book"
    args = ["--project", str(root), "--quiet"]
    assert main(["import", str(source), *args, "--host", "127.0.0.1", "--port", str(port)]) == 0
    registry = JobRegistry(tmp_path / "runtime" / "jobs.sqlite3")
    supervisor = JobSupervisor(registry, tmp_path)
    try:
        def start(operation, limit=0):
            job = supervisor.start(JobSpec(str(tmp_path), "book", str(root), operation, chunk_limit=limit))
            result = terminal(supervisor, job)
            assert result.state == "succeeded", result
            return result
        start("analyze")
        review = read_json(root / "terms.review.json")
        review["confirmed"] = True
        review["terms"][0]["select"] = 1
        atomic_json(root / "terms.review.json", review)
        assert main(["approve", *args]) == 0
        start("translate", 1)
        first = state.calls.copy()
        job = start("translate")
        assert state.calls[1] == first[1]
        for stage in (2, 3, 4, 5):
            assert state.calls[stage] == first[stage] + 1
        status = create_application().projects.status(StatusCommand(root))
        assert status.translation_complete and status.publication.current
        assert status.publication.output_path.is_file()
        events = registry.events(0, workspace_root=str(tmp_path), job_id=job.job_id)
        assert any(e["event"]["kind"] == "publication_completed" for e in events)
        completed = state.calls.copy()
        start("translate")
        assert state.calls == completed
    finally:
        supervisor.shutdown()
        registry.close()


def test_worker_reload_finishes_current_pass_then_reuses_its_checkpoint(tmp_path, server, monkeypatch):
    from test_pipeline import make_epub_source
    from bookpipe.cli import main
    from bookpipe.engine import Runner
    from bookpipe.util import atomic_json, read_json

    state, port = server
    root = tmp_path / "book"
    args = ["--project", str(root), "--quiet"]
    assert main(["import", str(make_epub_source(tmp_path)), *args,
                 "--host", "127.0.0.1", "--port", str(port)]) == 0
    assert main(["analyze", *args]) == 0
    review = read_json(root / "terms.review.json")
    review["confirmed"] = True
    review["terms"][0]["select"] = 1
    atomic_json(root / "terms.review.json", review)
    assert main(["approve", *args]) == 0

    first_stream = io.StringIO()
    first_sink = JsonlProgressSink(first_stream)
    original_run = Runner.run

    def request_after_p2(self, pass_no, *args, **kwargs):
        result = original_run(self, pass_no, *args, **kwargs)
        if pass_no == 2:
            first_sink.reload_requested = True
        return result

    job_spec = JobSpec(str(tmp_path), "book", str(root), "translate")
    with monkeypatch.context() as patch:
        patch.setattr(Runner, "run", request_after_p2)
        assert execute(job_spec, first_sink) == 0
    assert decode(first_stream.getvalue().splitlines()[-1] + "\n") == {
        "type": "reload", "completed_units": 0,
    }
    assert state.calls[2] == 1 and state.calls[3] == 0

    resumed_stream = io.StringIO()
    assert execute(job_spec, JsonlProgressSink(resumed_stream)) == 0
    assert decode(resumed_stream.getvalue().splitlines()[-1] + "\n")["type"] == "result"
    assert state.calls[2] == len(read_json(root / "book.json")["chunks"])
    assert create_application().projects.status(StatusCommand(root)).publication.current


def test_reload_protocol_requires_nonnegative_checkpoint_count():
    assert decode('{"type":"reload","completed_units":2}\n') == {
        "type": "reload", "completed_units": 2,
    }
    for invalid in (-1, 1.5, True, None):
        with pytest.raises(ValueError):
            decode(json.dumps({"type": "reload", "completed_units": invalid}) + "\n")


def test_reload_handoff_preserves_finite_remaining_chunk_limit(tmp_path, monkeypatch):
    from bookpipe.server.reload import HANDOFF_ENV, read_handoff, write_handoff

    root = tmp_path / "workspaces"
    (root / "book").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    intended = JobSpec(str(root), "book", str(root / "book"), "translate", chunk_limit=3)
    handoff = write_handoff(root, [intended])
    monkeypatch.setenv(HANDOFF_ENV, str(handoff))
    assert read_handoff(root) == [intended]
    assert not handoff.exists() and HANDOFF_ENV not in os.environ


def test_malformed_protocol_and_launch_failure_do_not_leave_active_jobs(runtime):
    root, _, supervisor = runtime
    supervisor.command_factory = lambda spec: [sys.executable, "-u", str(HELPER), "malformed"]
    failed = terminal(supervisor, supervisor.start(spec(root)))
    assert failed.state == "failed" and failed.error["type"] == "WorkerProtocolError"
    # Terminal state is visible before supervised process/descendant cleanup can
    # release ownership. A new mutating job must wait for that release.
    if supervisor.owns_project(root / "a"):
        with pytest.raises(JobConflict):
            supervisor.start(spec(root))
    for owned in list(supervisor.owned.values()):
        if owned.monitor:
            owned.monitor.join(timeout=5)
    assert not supervisor.owns_project(root / "a")
    supervisor.command_factory = lambda spec: [str(root / "nonexistent-executable")]
    failed = supervisor.start(spec(root))
    assert failed.state == "failed" and failed.error["type"] == "WorkerLaunchError"
    assert supervisor.active_for_project(root / "a") is None


@pytest.mark.skipif(sys.platform != "linux", reason="Detached child ownership uses Linux /proc and subreaping")
def test_forced_stop_reaps_detached_provider_child_and_leaves_other_worker(runtime):
    root, _, supervisor = runtime
    survivor = supervisor.start(spec(root, "b"))
    running(supervisor, survivor)
    supervisor.command_factory = lambda spec: [sys.executable, "-u", str(HELPER), "stubborn_child"]
    job = supervisor.start(spec(root, "a"))
    running(supervisor, job)
    child_pid = int((root / "a" / "child.pid").read_text())
    assert Path(f"/proc/{child_pid}").exists()
    owned = supervisor.owned[job.job_id]
    supervisor.stop(job.job_id)
    assert terminal(supervisor, job).state == "cancelled"
    owned.monitor.join(timeout=5)
    assert job.job_id not in supervisor.owned
    assert not Path(f"/proc/{child_pid}").exists()
    assert supervisor.get(survivor.job_id).state == "running"


@pytest.mark.parametrize("kind", ["analysis_completed", "recovery_repaired", "publication_completed"])
def test_progress_paths_are_removed_at_both_protocol_boundaries(kind, tmp_path):
    paths = {"project": str(tmp_path), "review_path": str(tmp_path / "terms.review.json"),
             "recovery_path": str(tmp_path / "artifacts/recovery.json"),
             "output_path": str(tmp_path / "published/book.epub")}
    event = ProgressEvent(kind, current=2, total=4, values={
        **paths, "pass_no": 5, "task_key": "P5:c1", "target_language": "pl",
    })
    stream = io.StringIO()
    JsonlProgressSink(stream).emit(event)
    assert str(tmp_path) not in stream.getvalue()
    # The decoder independently protects against older worker versions too.
    for wire in (stream.getvalue(), json.dumps({"type": "progress", "event": asdict(event)}) + '\n'):
        decoded = decode(wire)["event"]
        assert decoded["values"] == {"pass_no": 5, "task_key": "P5:c1", "target_language": "pl"}
        assert decoded["kind"] == kind and decoded["current"] == 2 and decoded["total"] == 4
    assert all(event.values[key] == value for key, value in paths.items())


def test_legacy_event_paths_are_hidden_in_http_snapshots_and_sse_replay(http_server):
    server, supervisor, root = http_server
    registry = supervisor.registry
    job = Job("legacy", str(root), "a", str(root / "a"), "translate", state="succeeded")
    registry.save(job)
    # Simulate persisted events written before the path fields were removed.
    original = registry.append(job.job_id, {"kind": "publication_completed", "values": {
        "project": str(root / "a"), "output_path": str(root / "a/published/book.epub"),
        "recovery_path": str(root / "a/artifacts/recovery.json"),
        "review_path": str(root / "a/terms.review.json"), "target_language": "pl",
    }})
    for route in ("/api/jobs", "/api/jobs/legacy"):
        status, value = request(server, "GET", route)
        assert status == 200 and str(root) not in json.dumps(value)
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request("GET", "/api/events?job_id=legacy", headers={"Last-Event-ID": "0"})
        response = connection.getresponse()
        assert response.status == 200
        snapshot, replay = read_sse(response), read_sse(response)
        assert str(root) not in json.dumps([snapshot, replay])
        assert snapshot["data"]["jobs"][0]["last_event"]["event"]["values"] == {"target_language": "pl"}
        assert replay["data"]["event"]["values"] == {"target_language": "pl"}
        assert replay["data"]["sequence"] == original["sequence"]
        assert int(replay["id"]) == original["id"]
        response.close()
    finally:
        connection.close()
    # Serialization must not mutate the stored snapshot or renumber history.
    assert registry.get(job.job_id).last_event == original


@pytest.mark.parametrize("workspace_id, expected", [("success", "succeeded"), ("fail", "failed"), ("a", "cancelled")])
def test_completed_workers_are_released_but_registry_history_survives(runtime, workspace_id, expected):
    root, registry, supervisor = runtime
    for _ in range(3):
        job = supervisor.start(spec(root, workspace_id))
        if expected == "cancelled":
            running(supervisor, job)
            supervisor.stop(job.job_id)
        assert terminal(supervisor, job).state == expected
        wait_for(lambda: job.job_id not in supervisor.owned)
        assert registry.get(job.job_id).state == expected
        assert registry.events(0, workspace_root=str(root), job_id=job.job_id)
        assert supervisor.stop(job.job_id).state == expected
    assert not supervisor.owned and len(supervisor.list()) == 3


def test_owned_remains_until_stop_cleanup_finishes_and_shutdown_waits(runtime, monkeypatch):
    import bookpipe.runtime.supervisor as runtime_supervisor
    root, _, supervisor = runtime
    cleanup_entered, release_cleanup, shutdown_done = threading.Event(), threading.Event(), threading.Event()
    def delayed_reap(children):
        cleanup_entered.set()
        assert release_cleanup.wait(5), "Test did not release child cleanup"
    monkeypatch.setattr(runtime_supervisor, "reap_descendants", delayed_reap)
    job = supervisor.start(spec(root, "stubborn"))
    running(supervisor, job)
    owned = supervisor.owned[job.job_id]
    supervisor.stop(job.job_id)
    shutdown = None
    try:
        assert cleanup_entered.wait(5)
        assert terminal(supervisor, job).state == "cancelled"
        assert supervisor.owned[job.job_id] is owned and owned.monitor.is_alive()
        def shut_down():
            supervisor.shutdown()
            shutdown_done.set()
        shutdown = threading.Thread(target=shut_down)
        shutdown.start()
        wait_for(lambda: supervisor.closing)
        assert not shutdown_done.is_set()
    finally:
        release_cleanup.set()
        if shutdown is not None:
            shutdown.join(timeout=5)
    assert shutdown_done.is_set() and not supervisor.owned
    assert not owned.monitor.is_alive() and not owned.stopper.is_alive()
    assert supervisor.get(job.job_id).state == "cancelled"
