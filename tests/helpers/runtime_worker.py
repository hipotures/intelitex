"""Offline subprocess fixture using the production execution/progress protocol."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bookpipe.progress import ProgressEvent
from bookpipe.runtime.models import JobSpec
from bookpipe.runtime.protocol import JsonlProgressSink
from bookpipe.runtime.worker import execute
from bookpipe.util import project_lock

mode = sys.argv[1]
if mode in {"stubborn", "stubborn_child"}:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
spec = JobSpec(**json.loads(sys.stdin.readline()))
sink = JsonlProgressSink(sys.stdout)
if mode == "malformed":
    print('{"type":"failure","error":[]}', flush=True)
    time.sleep(60)



def operation(command):
    with project_lock(command.project):
        if mode == "stubborn_child":
            child = subprocess.Popen([sys.executable, "-u", "-c",
                "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"],
                stdout=subprocess.PIPE, text=True, start_new_session=True)
            assert child.stdout.readline().strip() == "ready"
            child.stdout.close()
            (command.project / "child.pid").write_text(str(child.pid))
        sink.emit(ProgressEvent(kind="provider_waiting", current=1, total=3, values={
            "pass_no": 2, "task_key": "P2:c1", "chapter_id": "ch1", "chunk_id": "c1",
            "unit_id": "c1", "provider": "codex", "profile": "fixture", "requested_model": "fixture-model",
            "input_value": 4321, "input_unit": "utf8_bytes", "input_quality": "exact",
        }))
        sink.emit(ProgressEvent(kind="provider_usage_update", values={"input_tokens": 41, "output_tokens": 7}))
        if mode == "fail":
            raise RuntimeError("secret-provider-body sk-private-secret")
        if mode in {"hold", "stubborn", "stubborn_child"}:
            while True:
                time.sleep(0.05)
        sink.emit(ProgressEvent(kind="publication_completed", values={"target_language": "pl"}))


app = SimpleNamespace(pipeline=SimpleNamespace(analyze=operation, translate=operation),
                      publishing=SimpleNamespace(publish=operation))
raise SystemExit(execute(spec, sink, lambda progress: app))
