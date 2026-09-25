"""Read bounded, unmodified EPUB prose directly from the Library."""
from pathlib import Path
import posixpath
from urllib.parse import unquote
import re
import zipfile

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET

from ..util import PipelineError
from .epub_sources import _member_name, _small_member, packed_epub_metadata
from .imports import confined_source
from .source_preflight import source_signature


def _spine(archive: zipfile.ZipFile) -> list[str]:
    container = ET.fromstring(_small_member(archive, 'META-INF/container.xml', 256 * 1024))
    rootfile = container.find('.//{*}rootfile')
    if rootfile is None:
        raise PipelineError('EPUB has no package document.')
    package_name = _member_name(rootfile.get('full-path', ''))
    package = ET.fromstring(_small_member(archive, package_name, 2 * 1024 * 1024))
    prefix = posixpath.dirname(package_name)
    manifest = {}
    for item in package.findall('.//{*}manifest/{*}item'):
        if item.get('media-type') not in {'application/xhtml+xml', 'text/html'}:
            continue
        href = unquote((item.get('href') or '').split('#', 1)[0])
        manifest[item.get('id')] = _member_name(posixpath.normpath(posixpath.join(prefix, href)))
    spine = [manifest[item.get('idref')] for item in package.findall('.//{*}spine/{*}itemref')
             if item.get('idref') in manifest]
    if not spine or len(spine) > 2000:
        raise PipelineError('EPUB has no supported reading order.')
    return spine


def read_source_epub(root: Path | None, source_id: str, chapter_id: str | None = None) -> dict:
    if root is None:
        raise KeyError(source_id)
    source = confined_source(root, source_id)
    if not source.is_file() or source.suffix.lower() != '.epub' or not zipfile.is_zipfile(source):
        raise KeyError(source_id)
    with zipfile.ZipFile(source) as archive:
        spine = _spine(archive)
        if chapter_id is None:
            return {'title': packed_epub_metadata(source)['title'], 'book_fingerprint': source_signature(root, source_id),
                    'chapters': [{'id': f's{index:04d}', 'title': f'Section {index}'} for index in range(1, len(spine) + 1)]}
        if not re.fullmatch(r's[0-9]{4}', chapter_id) or not 1 <= int(chapter_id[1:]) <= len(spine):
            raise KeyError(chapter_id)
        raw = _small_member(archive, spine[int(chapter_id[1:]) - 1], 2 * 1024 * 1024)
    soup = BeautifulSoup(raw, 'html.parser')
    for ignored in soup.find_all(['script', 'style', 'svg', 'math']):
        ignored.decompose()
    body = soup.body or soup
    tags = {'p', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
    blocks = []
    for element in body.find_all(list(tags)):
        if any(parent.name in tags for parent in element.parents):
            continue
        value = element.get_text(' ', strip=True)
        if value:
            blocks.append({'id': f'b{len(blocks) + 1:04d}', 'kind': element.name, 'text': value})
        if len(blocks) >= 5000:
            raise PipelineError('EPUB section has too many text blocks.')
    if not blocks:
        value = body.get_text(' ', strip=True)
        if value:
            blocks.append({'id': 'b0001', 'kind': 'p', 'text': value})
    title = next((block['text'] for block in blocks if block['kind'] in {'h1', 'h2'}), f'Section {int(chapter_id[1:])}')
    return {'id': chapter_id, 'title': title, 'complete': True, 'stale': False, 'blocks': blocks}
