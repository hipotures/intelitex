"""Durable pre-Prepare workspace identity; absent on legacy projects."""
from pathlib import Path
import re

from ..util import PipelineError, read_json


def read_workspace_setup(root: Path) -> dict:
    path = root / 'workspace.json'
    if path.is_symlink():
        raise PipelineError('Unsafe workspace metadata.')
    if not path.is_file():
        return {}
    value = read_json(path)
    if not isinstance(value, dict) or value.get('format_version') != 1:
        raise PipelineError('Invalid workspace metadata.')
    for key in ('source_id', 'source_fingerprint', 'source_language', 'target_language'):
        if not isinstance(value.get(key), str) or not value[key]:
            raise PipelineError('Incomplete workspace metadata.')
    if not re.fullmatch(r'[0-9a-f]{64}', value['source_fingerprint']):
        raise PipelineError('Invalid source fingerprint.')
    return value


def validate_draft_destination(root: Path, source_id: str) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise PipelineError('Draft directory is unavailable.')
    value = read_workspace_setup(root)
    if not value or value['source_id'] != source_id:
        raise PipelineError('Import destination is not the selected draft.')
    if (root / 'book.json').exists() or (root / 'state.sqlite3').exists():
        raise PipelineError('Draft already has project state.')
    settings = root / 'settings.json'
    if settings.is_symlink() or not settings.is_file():
        raise PipelineError('Draft settings are unavailable.')
    if {entry.name for entry in root.iterdir()} != {'workspace.json', 'settings.json'}:
        raise PipelineError('Draft contains unexpected project files.')
    return value
