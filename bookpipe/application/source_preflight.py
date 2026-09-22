"""Read-only, bounded source inspection for Library setup. No project or provider."""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .epub_sources import _member_name, _small_member, packed_epub_metadata
from .imports import confined_source, validate_source_tree
from ..util import PipelineError

LANGUAGES = {
    'en': {'the', 'and', 'that', 'with', 'from', 'this', 'was', 'were', 'for', 'not'},
    'fr': {'le', 'la', 'les', 'des', 'une', 'avec', 'dans', 'pour', 'que', 'est'},
    'de': {'der', 'die', 'das', 'und', 'mit', 'den', 'ein', 'eine', 'nicht', 'ist'},
    'es': {'los', 'las', 'una', 'con', 'para', 'que', 'del', 'por', 'está', 'como'},
    'it': {'gli', 'che', 'una', 'con', 'per', 'della', 'non', 'sono', 'nel', 'come'},
    'pl': {'się', 'nie', 'jest', 'jak', 'dla', 'przez', 'który', 'oraz', 'był', 'jego'},
}


def source_signature(root: Path, source_id: str) -> str:
    """Fingerprint selected bytes, never every book during Library discovery."""
    source = confined_source(root, source_id)
    h = hashlib.sha256()
    paths = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file())
    if source.is_dir():
        validate_source_tree(source)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe source file.')
        h.update(path.relative_to(source.parent if source.is_file() else source).as_posix().encode())
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                h.update(chunk)
    return h.hexdigest()


def _visible(raw: bytes) -> tuple[str, str | None]:
    soup = BeautifulSoup(raw, 'html.parser')
    for node in soup(['script', 'style', 'nav']):
        node.decompose()
    heading = soup.find(['h1', 'h2', 'h3'])
    title = heading.get_text(' ', strip=True)[:160] if heading else None
    return soup.get_text(' ', strip=True), title or None


def _sample_indices(total: int) -> list[int]:
    count = min(5, total)
    return [0] if count == 1 else sorted({round(i * (total - 1) / (count - 1)) for i in range(count)})


def _epub_samples(source: Path) -> tuple[list[tuple[str, str | None]], list[str]]:
    with zipfile.ZipFile(source) as archive:
        container = ET.fromstring(_small_member(archive, 'META-INF/container.xml', 256 * 1024))
        rootfile = container.find('.//{*}rootfile')
        if rootfile is None:
            raise PipelineError('EPUB package document is missing.')
        opf_name = _member_name(rootfile.get('full-path', ''))
        opf = ET.fromstring(_small_member(archive, opf_name, 2 * 1024 * 1024))
        base = Path(opf_name).parent
        manifest = {node.get('id'): node.get('href') for node in opf.findall('.//{*}manifest/{*}item')}
        names = []
        for item in opf.findall('.//{*}spine/{*}itemref'):
            href = manifest.get(item.get('idref'))
            if href:
                name = _member_name((base / href.split('#')[0]).as_posix())
                if name.lower().endswith(('.html', '.xhtml', '.htm')):
                    names.append(name)
        if not names:
            raise PipelineError('EPUB has no readable spine documents.')
        samples = []
        for index in _sample_indices(len(names)):
            with archive.open(names[index]) as stream:
                samples.append(_visible(stream.read(512 * 1024)))
        return samples, names


def _folder_samples(source: Path) -> tuple[list[tuple[str, str | None]], list[str]]:
    validate_source_tree(source)
    names = sorted(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in {'.html', '.htm', '.xhtml'})
    if not names:
        raise PipelineError('Source has no readable HTML documents.')
    samples = []
    for index in _sample_indices(len(names)):
        with names[index].open('rb') as stream:
            samples.append(_visible(stream.read(512 * 1024)))
    return samples, [p.relative_to(source).as_posix() for p in names]


def _folder_metadata(source: Path) -> dict:
    result = {'title': source.name, 'creators': [], 'language': None}
    opf = next((path for path in sorted(source.rglob('*.opf')) if path.is_file() and not path.is_symlink()), None)
    if opf is not None:
        try:
            with opf.open('rb') as stream:
                root = ET.fromstring(stream.read(2 * 1024 * 1024))
        except (OSError, ET.ParseError, DefusedXmlException, ValueError):
            return result
        metadata = root.find('.//{*}metadata')
        if metadata is not None:
            title = metadata.find('{*}title')
            language = metadata.find('{*}language')
            creators = metadata.findall('{*}creator')
            result['title'] = (title.text or '').strip()[:500] or source.name if title is not None else source.name
            result['creators'] = [(node.text or '').strip()[:500] for node in creators if (node.text or '').strip()]
            result['language'] = (language.text or '').strip()[:50] or None if language is not None else None
    if result['language'] is None:
        first = next((path for path in sorted(source.rglob('*')) if path.is_file() and not path.is_symlink() and path.suffix.lower() in {'.html', '.htm', '.xhtml'}), None)
        if first is not None:
            with first.open('rb') as stream:
                opening = stream.read(4096).decode('utf-8', errors='replace')
            match = re.search(r'<html\b[^>]*\b(?:xml:)?lang=["\']([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)', opening, re.I)
            if match:
                result['language'] = match.group(1)
    return result


def detect_language(samples: list[str]) -> tuple[str | None, float]:
    # Give each sampled document a comparable vote, including long spine files.
    per_document = max(1, 6000 // max(len(samples), 1))
    words = [word for sample in samples for word in re.findall(r"[^\W\d_]+", sample.casefold())[:per_document]]
    if len(words) < 40:
        return None, 0.0
    scores = sorted(((sum(word in stops for word in words), lang) for lang, stops in LANGUAGES.items()), reverse=True)
    top, language = scores[0]
    second = scores[1][0]
    confidence = round((top - second) / max(top, 1), 3)
    return (language, confidence) if top >= 8 and confidence >= .35 else (None, confidence)


def inspect_source(root: Path, source_id: str, *, detailed: bool = False) -> dict:
    source = confined_source(root, source_id)
    if source.is_file():
        if source.suffix.lower() != '.epub' or not zipfile.is_zipfile(source):
            raise ValueError('Unsupported source.')
        metadata = packed_epub_metadata(source)
        try:
            samples, names = _epub_samples(source)
        except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, ET.ParseError, DefusedXmlException) as exc:
            raise PipelineError('Source inspection unavailable.') from exc
    elif source.is_dir():
        metadata = _folder_metadata(source)
        samples, names = _folder_samples(source)
    else:
        raise ValueError('Unsupported source.')
    prose = [text for text, _ in samples]
    detected, confidence = detect_language(prose)
    declared = metadata.get('language')
    language = detected or (declared if isinstance(declared, str) and re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', declared) else None)
    result = {'source_id': source_id, 'title': metadata['title'], 'creators': metadata.get('creators', []),
              'declared_language': declared, 'detected_language': detected, 'detection_confidence': confidence,
              'source_language': language, 'source_fingerprint': source_signature(root, source_id),
              'language_warning': 'Metadata and prose disagree; verify source language.' if detected and declared and detected != declared.lower().split('-')[0] else None}
    if detailed:
        result['sample_word_count'] = sum(len(re.findall(r"[^\W\d_]+", sample)) for sample in prose)
        result['sampled_documents'] = len(samples)
        result['document_count'] = len(names)
        result['sample_previews'] = [
            {'position': position + 1, 'heading': heading, 'excerpt': text[:320]}
            for position, (text, heading) in zip(_sample_indices(len(names)), samples) if text
        ]
    return result
