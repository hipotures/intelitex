"""The installed CLI and legacy script use the same project environment."""

import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
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
