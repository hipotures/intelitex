"""Explicit, verified source relocation; no inference from filenames or titles."""
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import shutil
import zipfile

from .. import book_metadata
from ..util import PipelineError, atomic_json, digest, plan_fingerprint, project_lock, read_json
from .epub_sources import unpack_epub


def relink_epub(root: Path, source: Path) -> dict:
    root, source = root.resolve(), source.resolve(strict=True)
    with project_lock(root):
        book = read_json(root / 'book.json')
        if plan_fingerprint(book) != book['content_fingerprint']:
            raise PipelineError('Frozen source/chunk manifest was modified.')
        with zipfile.ZipFile(source) as archive:
            for item in book['files']:
                if digest(archive.read(item['file'])) != item['sha256']:
                    raise PipelineError('Library source differs from the frozen source manifest.')
        original = dict(book)
        if not Path(book['source_root']).is_dir():
            destination = root / 'source-package'
            if destination.exists():
                raise PipelineError('Source snapshot already exists; inspect it before relinking.')
            with TemporaryDirectory(prefix='.source-relink-', dir=root) as staging:
                unpacked = unpack_epub(source, Path(staging))
                for item in book['files']:
                    if digest((unpacked / item['file']).read_bytes()) != item['sha256']:
                        raise PipelineError('Extracted source differs from the frozen manifest.')
                unpacked.rename(destination)
            book['source_root'] = str(destination)
        book['source_archive'] = str(source)
        if book != original:
            backup = root / 'history' / 'source_relinks' / uuid4().hex
            backup.mkdir(parents=True)
            shutil.copyfile(root / 'book.json', backup / 'book.json')
            atomic_json(backup / 'relocation.json', {'source_archive': str(source), 'sha256': digest(source.read_bytes()),
                                                   'source_fingerprint': book['source_fingerprint']})
            atomic_json(root / 'book.json', book)
        return book_metadata.register(root, book, source)
