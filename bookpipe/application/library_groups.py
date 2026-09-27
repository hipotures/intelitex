"""Folder hierarchy and optional presentation metadata; never workspace identity."""
from pathlib import Path, PurePosixPath
import re
import unicodedata

import yaml
from ..languages import language_code


def group_metadata(root: Path, relative: str) -> dict:
    folder = root / relative
    result = {'group_id': relative, 'name': folder.name, 'kind': None}
    path = folder / 'library.yaml'
    if not path.exists():
        return result
    try:
        if path.is_symlink() or path.stat().st_size > 16_384:
            raise ValueError('library.yaml must be a regular file of at most 16 KiB.')
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) - {'version', 'kind', 'name'}:
            raise ValueError('Expected version, optional kind and optional name.')
        if type(value.get('version')) is not int or value['version'] != 1:
            raise ValueError('Expected version: 1.')
        if value.get('kind') not in {None, 'author', 'series', 'category'}:
            raise ValueError('kind must be author, series or category.')
        name = value.get('name', folder.name)
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise ValueError('name must be a non-empty single line of at most 200 characters.')
        result.update(name=name.strip(), kind=value.get('kind'))
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
        # Keep the real folder visible, and explain why its declaration was ignored.
        result['warning'] = f'Invalid {relative}/library.yaml: {exc}'
    return result


def ancestors(root: Path, source_id: str, cache: dict) -> list[dict]:
    parts = PurePosixPath(source_id).parts[:-1]
    result = []
    for i in range(1, len(parts) + 1):
        relative = '/'.join(parts[:i])
        if relative not in cache:
            cache[relative] = group_metadata(root, relative)
        result.append(cache[relative])
    return result


def natural(value: str) -> tuple:
    value = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.split(r'(\d+)', value))


def ordered_sources(sources: list[dict], sort: str) -> list[dict]:
    """Apply one ordering at each hierarchy level; keep every subtree contiguous."""
    def author(item):
        return next((g['name'] for g in reversed(item['groups']) if g['kind'] == 'author'), '') or ', '.join(item['creators'])

    def language(item):
        return language_code(item.get('language')) or ''

    def key(name, value, ident):
        return (not bool(value), natural(value), natural(name), ident)

    def book_key(item):
        name = PurePosixPath(item['source_id']).name if sort == 'author' else item['title']
        value = author(item) if sort == 'author' else language(item) if sort == 'language' else item['title']
        return key(name, value, item['source_id'])

    def walk(items, depth):
        leaves, groups = [], {}
        for item in items:
            if len(item['groups']) == depth:
                leaves.append((book_key(item), [item]))
            else:
                group = item['groups'][depth]
                groups.setdefault(group['group_id'], []).append(item)
        for ident, members in groups.items():
            group = members[0]['groups'][depth]
            if sort == 'author':
                values = [author(item) for item in members if author(item)]
                value = group['name'] if group['kind'] == 'author' else min(values, key=natural, default='')
            elif sort == 'language':
                value = min((language(item) for item in members if language(item)), key=natural, default='')
            else:
                value = group['name']
            leaves.append((key(group['name'], value, ident), walk(members, depth + 1)))
        return [item for _, chunk in sorted(leaves, key=lambda pair: pair[0]) for item in chunk]

    return walk(sources, 0)
