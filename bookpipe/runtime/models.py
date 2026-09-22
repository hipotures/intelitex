"""Validated runtime inputs and detached supervision snapshots."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from .protocol import public_envelope

ACTIVE = frozenset({"starting", "running", "stopping"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "abandoned"})


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobSpec:
    workspace_root: str
    workspace_id: str
    project: str
    operation: str
    profile: str | None = None
    chunk_limit: int = 0
    target_language: str = "pl"

    def __post_init__(self):
        if self.operation not in {"analyze", "translate", "publish"}:
            raise ValueError("Unsupported operation; use analyze, translate or publish.")
        if self.profile is not None and (not isinstance(self.profile, str) or not re.fullmatch(r"[\w.-]{1,128}", self.profile)):
            raise ValueError("Invalid profile name.")
        if type(self.chunk_limit) is not int or self.chunk_limit < 0:
            raise ValueError("chunk_limit must be a nonnegative integer (0 means all).")
        if not isinstance(self.target_language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", self.target_language):
            raise ValueError("Invalid target_language.")
        root = Path(self.workspace_root).resolve()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.workspace_id) or ".." in self.workspace_id:
            raise ValueError("Invalid workspace ID.")
        project = (root / self.workspace_id).resolve()
        if project == root or not project.is_relative_to(root) or project != Path(self.project) or not project.is_dir():
            raise ValueError("Workspace must resolve beneath workspace root.")
        object.__setattr__(self, "workspace_root", str(root))


@dataclass(frozen=True)
class Job:
    job_id: str
    workspace_root: str
    workspace_id: str
    project: str
    operation: str
    state: str = "starting"
    pid: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    sequence: int = 0
    last_event: dict | None = None
    error: dict | None = None

    def public(self) -> dict:
        value = asdict(self)
        value.pop("project")
        value.pop("workspace_root")
        if value["last_event"] is not None:
            value["last_event"] = public_envelope(value["last_event"])
        return value
