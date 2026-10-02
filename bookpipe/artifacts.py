"""Logical task paths: chapter-first writes and non-destructive legacy discovery."""
from pathlib import Path
import re

from .util import PipelineError


def task_parts(key):
    match = re.fullmatch(r"pass(\d+)/([A-Za-z0-9_.-]+)", key)
    if not match:
        raise PipelineError(f"Invalid artifact task key: {key!r}")
    return int(match[1]), match[2]


def task_roots(root: Path, key: str):
    number, unit = task_parts(key)
    legacy = root / "artifacts" / f"pass{number}" / unit
    return [legacy, *sorted((root / "artifacts").glob(f"*/pass{number}/{unit}"))]


def task_root(root, key, chapter=None):
    number, unit = task_parts(key)
    if chapter is None:
        return root / "artifacts" / f"pass{number}" / unit
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", chapter) or chapter in (".", ".."):
        raise PipelineError("Unsafe chapter artifact identity.")
    return root / "artifacts" / chapter / f"pass{number}" / unit


def analysis_directories(root):
    return [root / "artifacts" / "pass1", *sorted((root / "artifacts").glob("*/pass1"))]


def attempt_manifests(root):
    """Physical accounting survives explicit P1 archive/reset, exactly once."""
    return sorted(set(root.glob("artifacts/**/attempt_*/attempt.json")) |
                  set(root.glob("history/p1_resets/*/artifacts/**/attempt_*/attempt.json")))
