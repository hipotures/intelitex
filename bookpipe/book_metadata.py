"""Shared book identity and original/current bibliographic metadata in durable JSON.

Identity uses the frozen source fingerprint, never title, author or location.
Corrections replace one current value set; the original is retained unchanged.
"""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re

from .languages import language_code
from .util import PipelineError, atomic_json, digest, file_lock, read_json


class MetadataConflict(PipelineError):
    pass


def book_id(book: dict) -> str:
    return 'book-' + digest({'source_fingerprint': book['source_fingerprint']})


def catalog_path(workspace_root: Path) -> Path:
    return workspace_root / '.book-metadata.json'


def _stamp(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=8)
def _cached_catalog(path, stamp):
    value = read_json(path)
    if not isinstance(value, dict) or value.get('format_version') != 1 or not isinstance(value.get('books'), dict) or not isinstance(value.get('sources'), dict):
        raise PipelineError('Invalid book metadata catalog.')
    return value


def _read(workspace_root):
    path = catalog_path(workspace_root)
    if path.is_symlink():
        raise PipelineError('Unsafe book metadata catalog.')
    if not path.exists():
        return {'format_version': 1, 'books': {}, 'sources': {}}
    return deepcopy(_cached_catalog(path, _stamp(path)))


def _original(book):
    metadata = book.get('metadata', {})
    return {'title': metadata.get('title') or Path(book['source_root']).name,
            'creators': list(metadata.get('creators', [])), 'language': metadata.get('language')}


def _record(book, source=None):
    original = _original(book)
    # Older manifests omitted author/language. Capture these original fields
    # from the explicitly linked source once, without replacing the saved title.
    missing = {'creators', 'language'} - set(book.get('metadata', {}))
    if source is not None and missing:
        from .application.epub_sources import packed_epub_metadata
        from .application.source_preflight import _folder_metadata
        source_values = packed_epub_metadata(source) if source.is_file() else _folder_metadata(source)
        for key in missing:
            original[key] = source_values.get(key, original[key])
    return {'book_id': book_id(book), 'source_fingerprint': book['source_fingerprint'],
            'original': original, 'corrections': {}, 'updated_at': None}


def _snapshot(record):
    original = record['original']
    corrections = record['corrections']
    return {'book_id': record['book_id'], 'source_fingerprint': record['source_fingerprint'],
            'original': deepcopy(original), 'corrections': deepcopy(corrections),
            'effective': {**original, **corrections}, 'updated_at': record.get('updated_at'),
            'revision': digest({'book_id': record['book_id'], 'original': original, 'corrections': corrections})}


def snapshot(root: Path, book: dict) -> dict:
    record = _read(root.parent)['books'].get(book_id(book)) or _record(book)
    return _snapshot(record)


def effective(root: Path, book: dict) -> dict:
    return snapshot(root, book)['effective']


def register(root: Path, book: dict, source: Path | None = None) -> dict:
    """Record original values and an explicit location without modifying the source."""
    with file_lock(root.parent, '.book-metadata.lock', 'Book metadata busy.'):
        catalog = _read(root.parent)
        ident = book_id(book)
        if ident not in catalog['books']:
            catalog['books'][ident] = _record(book, source)
        if source is not None:
            source = source.resolve()
            location = {'book_id': ident}
            if source.is_file():
                location['sha256'] = digest(source.read_bytes())
            catalog['sources'][str(source)] = location
        atomic_json(catalog_path(root.parent), catalog)
        return _snapshot(catalog['books'][ident])


def update(root: Path, book: dict, revision: str, corrections: dict) -> dict:
    if not isinstance(corrections, dict) or set(corrections) - {'title', 'creators', 'language'}:
        raise ValueError('Expected title, creators and/or language corrections.')
    with file_lock(root.parent, '.book-metadata.lock', 'Book metadata busy.'):
        catalog = _read(root.parent)
        ident = book_id(book)
        record = catalog['books'].setdefault(ident, _record(book))
        if revision != _snapshot(record)['revision']:
            raise MetadataConflict('Book metadata changed. Reload before saving.')
        for key, value in corrections.items():
            if value is None:
                record['corrections'].pop(key, None)
                continue
            if key == 'creators':
                if not isinstance(value, list) or len(value) > 32 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 or any(ord(c) < 32 for c in v) for v in value):
                    raise ValueError('Authors must be non-empty names, one per entry.')
                value = [v.strip() for v in value]
            elif key == 'title':
                if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(c) < 32 for c in value):
                    raise ValueError('Title must be a non-empty single line of at most 500 characters.')
                value = value.strip()
            else:
                if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*', value.strip()):
                    raise ValueError('Expected a language code such as en or pl.')
                value = language_code(value)
                if value is None:
                    raise ValueError('Choose a known language or restore the original value.')
            original = record['original'].get(key)
            same = value == (language_code(original) if key == 'language' else original)
            if same:
                record['corrections'].pop(key, None)
            else:
                record['corrections'][key] = value
        if revision != _snapshot(record)['revision']:
            record['updated_at'] = datetime.now(timezone.utc).isoformat()
        atomic_json(catalog_path(root.parent), catalog)
        return _snapshot(record)


@lru_cache(maxsize=256)
def _file_hash(path, stamp):
    return digest(path.read_bytes())


def source_metadata(workspace_root: Path, source: Path) -> dict | None:
    """Resolve an explicitly bound Library source; refuse a replaced file."""
    catalog = _read(workspace_root)
    location = catalog['sources'].get(str(source.resolve()))
    if location is None:
        return None
    if 'sha256' in location and _file_hash(source, _stamp(source)) != location['sha256']:
        return None
    record = catalog['books'].get(location['book_id'])
    if record is None:
        raise PipelineError('Library source references missing book metadata.')
    return _snapshot(record)
