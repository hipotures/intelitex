"""Confined import inputs shared by delivery and worker validation."""
from pathlib import Path, PurePosixPath
import re
import zipfile


class RequestConflict(Exception):
    pass


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
        result = []

        def visit(folder):
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                return
            for entry in entries:
                try:
                    # Do not follow directory aliases/cycles during discovery.
                    if entry.is_symlink():
                        continue
                    ident = entry.relative_to(self.root).as_posix()
                    path = confined_source(self.root, ident)
                    if path.is_file() and path.suffix.lower() == '.epub' and zipfile.is_zipfile(path):
                        result.append({'source_id': ident})
                    elif path.is_dir():
                        children = list(path.iterdir())
                        package = (path / 'META-INF' / 'container.xml').is_file()
                        loose_book = any(p.is_file() and p.suffix.lower() in {'.opf', '.html', '.htm', '.xhtml'} for p in children)
                        # A declared group remains a group, even with a README.html.
                        if package or (loose_book and not (path / 'library.yaml').exists()):
                            result.append({'source_id': ident})
                        else:
                            visit(path)
                except (ValueError, OSError):
                    continue

        visit(self.root)
        return sorted(result, key=lambda item: item['source_id'])

    def sources_page(self, after: str | None, limit: int) -> tuple[list[dict], str | None]:
        """Discover book roots without reading prose or descending into packages."""
        result = [item for item in self.sources() if after is None or item['source_id'] > after]
        next_cursor = result[limit - 1]['source_id'] if len(result) > limit else None
        return result[:limit], next_cursor
