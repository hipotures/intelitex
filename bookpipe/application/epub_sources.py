"""Bounded metadata reads and safe extraction for packed EPUB sources."""

from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from ..util import PipelineError


def _member_name(name: str) -> str:
    if not name or '\\' in name or ':' in name or name.startswith('/'):
        raise PipelineError('Unsafe EPUB member path.')
    parts = name.rstrip('/').split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise PipelineError('Unsafe EPUB member path.')
    return PurePosixPath(*parts).as_posix()


def _small_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    member = archive.getinfo(name)
    if member.file_size > limit:
        raise PipelineError('EPUB metadata is too large.')
    with archive.open(member) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise PipelineError('EPUB metadata is too large.')
    return raw


def packed_epub_metadata(source: Path) -> dict:
    """Read presentation metadata without unpacking prose or the entire archive."""
    result = {'title': source.stem, 'creators': [], 'language': None, 'word_count': None}
    try:
        with zipfile.ZipFile(source) as archive:
            container = ET.fromstring(_small_member(archive, 'META-INF/container.xml', 256 * 1024))
            rootfile = container.find('.//{*}rootfile')
            if rootfile is None:
                return result
            opf_name = _member_name(rootfile.get('full-path', ''))
            opf = ET.fromstring(_small_member(archive, opf_name, 2 * 1024 * 1024))
            metadata = opf.find('.//{*}metadata')
            if metadata is None:
                return result
            title = metadata.find('{*}title')
            language = metadata.find('{*}language')
            creators = metadata.findall('{*}creator')
            result['title'] = (title.text or '').strip()[:500] or source.stem if title is not None else source.stem
            result['creators'] = [(node.text or '').strip()[:500] for node in creators if (node.text or '').strip()]
            result['language'] = (language.text or '').strip()[:50] or None if language is not None else None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, DefusedXmlException, PipelineError):
        pass
    return result


def unpack_epub(source: Path, project: Path) -> Path:
    """Copy a packed source into a durable project snapshot, rejecting traversal and bombs."""
    destination = project / 'source-package'
    destination.mkdir(exist_ok=False)
    total = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > 100_000:
                raise PipelineError('EPUB has too many members.')
            for member in members:
                name = _member_name(member.filename)
                if name in seen:
                    raise PipelineError('EPUB contains duplicate members.')
                seen.add(name)
                kind = (member.external_attr >> 16) & 0o170000
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind != 0 and (kind == stat.S_IFDIR) != member.is_dir()):
                    raise PipelineError('EPUB contains an unsupported member type.')
                total += member.file_size
                if total > 2 * 1024 * 1024 * 1024:
                    raise PipelineError('EPUB is too large to prepare.')
                target = destination / name
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as stream, target.open('xb') as output:
                    shutil.copyfileobj(stream, output)
        return destination
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise PipelineError('Unable to read EPUB package.') from exc
