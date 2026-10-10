"""Hand-entered title of a workspace's translation, shown by the Reader instead of the source title.

Kept in its own small file so that it works for every workspace (workspaces created before
`workspace.json` have none) and never touches the frozen source or any checkpoint.
"""
from __future__ import annotations

from pathlib import Path

from ..util import atomic_json, read_json

FILE = 'translated_title.json'
MAX_LENGTH = 200


def clean_translated_title(value) -> str | None:
    """Return the normalized title, None for "none", or raise ValueError for an invalid one."""
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError('Invalid translated title.')
    value = ' '.join(value.split())
    if len(value) > MAX_LENGTH:
        raise ValueError('Invalid translated title.')
    return value or None


def read_translated_title(root: Path) -> str | None:
    path = root / FILE
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = read_json(path)
        return clean_translated_title(value.get('title')) if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_translated_title(root: Path, title: str | None) -> str | None:
    title = clean_translated_title(title)
    path = root / FILE
    if title is None:
        path.unlink(missing_ok=True)
    else:
        atomic_json(path, {'format_version': 1, 'title': title})
    return title
