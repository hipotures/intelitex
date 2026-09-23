"""The installed CLI and legacy script use the same project environment."""

import http.client
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")


def test_canonical_entrypoint_and_legacy_shim_help():
    assert UV is not None
    for command in ("intelitex", "translate.py"):
        result = subprocess.run(
            [UV, "run", "--locked", command, "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
        )
        assert "serve" in result.stdout
        assert "translate" in result.stdout
    assert "# /// script" not in (ROOT / "translate.py").read_text()


def test_canonical_serve_starts_http_server(tmp_path):
    assert UV is not None
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")}
    process = subprocess.Popen(
        [UV, "run", "--locked", "intelitex", "serve", "--workspace-root", str(workspaces),
         "--bind", "127.0.0.1", "--port", "0"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        readable, _, _ = select.select([process.stdout], [], [], 15)
        assert readable, "Server did not announce its listening address"
        line = process.stdout.readline()
        match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        assert match, line
        with urllib.request.urlopen(f"http://127.0.0.1:{match.group(1)}/api/health", timeout=5) as response:
            assert response.status == 200
            assert json.load(response) == {"status": "ok"}
    finally:
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, f"{stdout}\n{stderr}"


def test_ctrl_c_closes_sse_without_traceback(tmp_path):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    process = subprocess.Popen(
        [UV, "run", "--locked", "intelitex", "serve", "--workspace-root", str(workspaces),
         "--bind", "127.0.0.1", "--port", "0"],
        cwd=ROOT, env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    connection = None
    try:
        readable, _, _ = select.select([process.stdout], [], [], 15)
        assert readable, "Server did not announce its listening address"
        line = process.stdout.readline()
        match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        assert match, line
        port = int(match.group(1))
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError("HTTP server did not become ready")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        assert response.status == 200
        assert response.readline() == b"event: snapshot\n"
        assert response.readline().startswith(b"data: ")
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode in {0, 130}
        assert stdout.count("Intelitex stopped.") <= 1
        assert not stderr, stderr
    finally:
        if connection:
            connection.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_ctrl_c_drains_inflight_http_request_without_cancelling_it(tmp_path):
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    entered = tmp_path / "request-entered"
    script = textwrap.dedent("""
        import sys, time
        from pathlib import Path
        import bookpipe.server as server_module
        from bookpipe.server.service import ServerService

        original = ServerService.capabilities
        def slow_capabilities(self):
            Path(sys.argv[2]).write_text('entered')
            time.sleep(3)
            return original(self)
        ServerService.capabilities = slow_capabilities
        server_module.serve(Path(sys.argv[1]), port=0)
    """)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(workspaces), str(entered)],
        cwd=ROOT, env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    connection = stream = None
    result = []
    try:
        readable, _, _ = select.select([process.stdout], [], [], 15)
        assert readable, "Server did not start"
        match = re.search(r"http://127\.0\.0\.1:(\d+)", process.stdout.readline())
        assert match
        port = int(match.group(1))
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        stream.request("GET", "/api/events")
        assert stream.getresponse().readline() == b"event: snapshot\n"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        connection.request("GET", "/api/capabilities")

        def read_response():
            try:
                response = connection.getresponse()
                result.append((response.status, response.read()))
            except BaseException as exc:
                result.append(exc)

        reader = threading.Thread(target=read_response)
        reader.start()
        for _ in range(100):
            if entered.is_file():
                break
            time.sleep(.02)
        else:
            raise AssertionError("HTTP request did not enter the application")
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        reader.join(timeout=3)
        assert process.returncode == 0, stderr
        assert stdout.count("Intelitex stopped.") == 1
        assert not stderr, stderr
        assert result and result[0][0] == 200, result
    finally:
        if connection:
            connection.close()
        if stream:
            stream.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_ctrl_c_cancels_owned_worker_before_registry_closes(tmp_path):
    workspaces = tmp_path / "workspaces"
    (workspaces / "running").mkdir(parents=True)
    helper = ROOT / "tests" / "helpers" / "runtime_worker.py"
    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        import bookpipe.server as server_module
        from bookpipe.runtime.models import JobSpec
        from bookpipe.runtime.supervisor import JobSupervisor

        root, helper = Path(sys.argv[1]), sys.argv[2]
        class FixtureSupervisor(JobSupervisor):
            def __init__(self, registry, workspace_root):
                super().__init__(registry, workspace_root,
                    command_factory=lambda spec: [sys.executable, '-u', helper, 'hold'],
                    interrupt_grace=.3, terminate_grace=.2)
                self.start(JobSpec(str(root), 'running', str(root / 'running'), 'translate'))

        server_module.JobSupervisor = FixtureSupervisor
        server_module.serve(root, port=0)
    """)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(workspaces), str(helper)],
        cwd=ROOT, env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        readable, _, _ = select.select([process.stdout], [], [], 15)
        assert readable, "Server did not start"
        assert "Intelitex serving" in process.stdout.readline()
        registry = tmp_path / "state" / "intelitex" / "jobs.sqlite3"
        for _ in range(50):
            with sqlite3.connect(registry) as db:
                row = db.execute("SELECT data FROM jobs").fetchone()
                events = db.execute("SELECT data FROM events").fetchall()
            if (row and json.loads(row[0])["state"] == "running" and
                    any(json.loads(event[0])["event"]["kind"] == "provider_waiting" for event in events)):
                break
            time.sleep(.1)
        else:
            raise AssertionError("Fixture worker did not start")
        worker_pid = json.loads(row[0])["pid"]
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        assert stdout.count("Intelitex stopped.") == 1
        assert not stderr, stderr
        with sqlite3.connect(registry) as db:
            final = json.loads(db.execute("SELECT data FROM jobs").fetchone()[0])
        assert final["state"] == "cancelled"
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("Owned worker was not reaped")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
