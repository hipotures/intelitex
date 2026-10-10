"""Logical task paths: chapter-first writes and non-destructive legacy discovery."""
from pathlib import Path
import os
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
    found = set()
    for base in [root / 'artifacts', *root.glob('history/p1_resets/*/artifacts')]:
        for folder, dirs, files in os.walk(base, followlinks=False):
            # Never inspect credentials/native state, including archived homes.
            dirs[:] = [name for name in dirs if name != 'codex-home' and
                       not (Path(folder) / name).is_symlink()]
            if Path(folder).name.startswith('attempt_') and 'attempt.json' in files:
                path = Path(folder) / 'attempt.json'
                if not path.is_symlink():
                    found.add(path)
    return sorted(found)
