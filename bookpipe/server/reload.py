"""Private local control files for an operator-requested, checkpoint-safe reload."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import uuid

from ..runtime.models import JobSpec, parse_spec
from ..runtime.registry import default_registry_path
from ..util import atomic_json

HANDOFF_ENV = "INTELITEX_RELOAD_HANDOFF"


def _state_dir() -> Path:
    return default_registry_path().parent


def _process_start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def register_server(root: Path) -> dict:
    record = {"pid": os.getpid(), "start": _process_start(os.getpid()),
              "workspace_root": str(root.resolve()), "token": uuid.uuid4().hex}
    atomic_json(_state_dir() / "serve.json", record)
    return record


def unregister_server(record: dict) -> None:
    path = _state_dir() / "serve.json"
    try:
        if json.loads(path.read_text()) == record:
            path.unlink()
    except (OSError, ValueError):
        pass


def request_reload(root: Path | None = None) -> None:
    try:
        record = json.loads((_state_dir() / "serve.json").read_text())
        pid = record["pid"]
        if type(pid) is not int or pid < 1 or record["start"] != _process_start(pid):
            raise ValueError("No current Intelitex server was found.")
        if root is not None and record["workspace_root"] != str(root.resolve()):
            raise ValueError("The running server uses another workspace root.")
        os.kill(pid, signal.SIGHUP)
    except (FileNotFoundError, ProcessLookupError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("No current Intelitex server was found.") from exc


def write_handoff(root: Path, specs: list[JobSpec]) -> Path:
    path = _state_dir() / ("reload-" + uuid.uuid4().hex + ".json")
    atomic_json(path, {"workspace_root": str(root.resolve()),
                       "jobs": [asdict(spec) for spec in specs]})
    return path


def read_handoff(root: Path) -> list[JobSpec]:
    raw = os.environ.pop(HANDOFF_ENV, None)
    if raw is None:
        return []
    path = Path(raw)
    if path.parent != _state_dir() or not path.name.startswith("reload-"):
        raise ValueError("Invalid reload handoff path.")
    data = json.loads(path.read_text())
    if data["workspace_root"] != str(root.resolve()):
        raise ValueError("Reload handoff belongs to another workspace root.")
    specs = [parse_spec(item) for item in data["jobs"]]
    if any(not isinstance(spec, JobSpec) or spec.operation != "translate" for spec in specs):
        raise ValueError("Invalid reload handoff job.")
    path.unlink()
    return specs
