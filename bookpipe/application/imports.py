"""Confined import inputs shared by delivery and worker validation."""
from pathlib import Path, PurePosixPath
import re


class ImportDisabled(ValueError):
    pass


class DestinationConflict(ValueError):
    pass


def workspace_destination(root: Path, identifier: str) -> Path:
    if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', identifier)
            or '..' in identifier):
        raise ValueError('Invalid workspace ID.')
    path = root / identifier
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('Invalid destination.')
    return path


def confined_source(root: Path, identifier: str) -> Path:
    if (not isinstance(identifier, str) or not identifier or '\\' in identifier
            or ':' in identifier or identifier.startswith('/')
            or any(part in {'', '.', '..'} for part in identifier.split('/'))):
        raise ValueError('Invalid source identifier.')
    path = (root / PurePosixPath(identifier)).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Source escapes import root.')
    return path


def validate_source_tree(source: Path) -> None:
    # Import may follow OPF references and write requested sidecars. Reject
    # escaping symlinks anywhere in this selected source, including sidecars.
    for path in source.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(source):
            raise ValueError('Source contains an escaping symlink.')


class ImportQueries:
    def __init__(self, root: Path | None):
        self.root = root.resolve(strict=True) if root is not None else None
        if self.root is not None and not self.root.is_dir():
            raise ValueError('Import root must be a directory.')

    def sources(self) -> list[dict]:
        if self.root is None:
            raise ImportDisabled('Web import is disabled.')
        # Only immediate source folders; no file content or generic navigation.
        result = []
        for entry in sorted(self.root.iterdir()):
            try:
                path = confined_source(self.root, entry.name)
                if path.is_dir():
                    result.append({'source_id': entry.name})
            except (ValueError, OSError):
                continue
        return result
