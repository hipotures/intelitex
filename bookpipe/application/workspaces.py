"""Root-confined workspace discovery and project queries for local clients."""
from dataclasses import dataclass
from pathlib import Path
import re

from .commands import StatusCommand
from .results import StatusResult


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    status: StatusResult


class WorkspaceQueries:
    def __init__(self, projects, root: Path):
        self.projects = projects
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace root must be a directory.")

    def resolve(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workspace_id) or ".." in workspace_id:
            raise ValueError("Invalid workspace ID.")
        path = (self.root / workspace_id).resolve()
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError("Workspace escapes configured root.")
        if not path.is_dir() or not (path / "book.json").is_file():
            raise KeyError(workspace_id)
        return path

    def list(self) -> list[str]:
        result = []
        for candidate in sorted(self.root.iterdir()):
            try:
                self.resolve(candidate.name)
                result.append(candidate.name)
            except (ValueError, KeyError, OSError):
                continue
        return result

    def status(self, workspace_id: str) -> Workspace:
        return Workspace(workspace_id, self.projects.status(StatusCommand(self.resolve(workspace_id))))
