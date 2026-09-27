"""Corrections follow source identity; publication alone applies bibliographic edits."""
import zipfile

import pytest
from defusedxml import ElementTree as ET

from bookpipe import book_metadata as metadata
from bookpipe.application import PublicationStatusCommand, PublishCommand, StatusCommand
from bookpipe.application.catalog import WebCatalog
from bookpipe.application.imports import ImportQueries
from bookpipe.util import digest, read_json
from test_publishing import _import_project, _complete_translations, CountingOfflinePool
from test_server_api import api
from test_runtime import request


def test_shared_identity_original_restore_conflict_and_source_relocation(tmp_path):
    app, root, source = _import_project(tmp_path)
    book = read_json(root / 'book.json')
    original = metadata.snapshot(root, book)
    second = root.parent / 'another-workspace'
    second.mkdir()
    changed = metadata.update(root, book, original['revision'], {'title': 'Corrected', 'creators': ['A Writer'], 'language': 'eng-US'})
    assert changed['original'] == original['original']
    assert changed['effective'] == {'title': 'Corrected', 'creators': ['A Writer'], 'language': 'en'}
    assert changed['book_id'] == original['book_id']
    assert metadata.snapshot(second, book) == changed
    assert app.web.metadata(root)['title'] == 'Corrected'
    assert app.projects.status(StatusCommand(root)).title == 'Corrected'
    with pytest.raises(metadata.MetadataConflict):
        metadata.update(second, book, original['revision'], {'title': 'Stale edit'})
    moved = source.with_name('moved-source')
    source.rename(moved)
    metadata.register(root, book, moved)
    catalog = WebCatalog(tmp_path, ImportQueries(tmp_path))
    assert catalog.source_metadata('moved-source')['title'] == 'Corrected'
    restored = metadata.update(second, book, changed['revision'], {key: None for key in changed['corrections']})
    assert restored['original'] == restored['effective'] == original['original']
    assert restored['corrections'] == {}
    assert read_json(root / 'book.json') == book


def test_metadata_only_publish_keeps_checkpoints_source_and_old_edition(tmp_path):
    app, root, source = _import_project(tmp_path)
    _complete_translations(root)
    first = app.publishing.publish(PublishCommand(root))
    old_epub = first.status.output_path.read_bytes()
    protected = [root / 'book.json', *[p for p in (root / 'state').rglob('*') if p.is_file()], *[p for p in source.rglob('*') if p.is_file()]]
    hashes = {p: digest(p.read_bytes()) for p in protected}
    book = read_json(root / 'book.json')
    original = metadata.snapshot(root, book)
    metadata.update(root, book, original['revision'], {'title': 'New title & subtitle', 'creators': ['Writer One', 'Writer Two'], 'language': 'fr'})
    status = app.publishing.status(PublicationStatusCommand(root))
    assert not status.current and status.translation_complete
    second = app.publishing.publish(PublishCommand(root))
    assert second.built and second.status.current
    assert second.status.output_path != first.status.output_path
    assert first.status.output_path.read_bytes() == old_epub
    assert second.status.title == 'New title & subtitle'
    assert tuple(second.status.creators) == ('Writer One', 'Writer Two')
    with zipfile.ZipFile(second.status.output_path) as epub:
        package = ET.fromstring(epub.read(book['metadata']['opf']))
        assert package.find('.//{*}metadata/{*}title').text == 'New title & subtitle'
        assert [v.text for v in package.findall('.//{*}metadata/{*}creator')] == ['Writer One', 'Writer Two']
        assert package.find('.//{*}metadata/{*}language').text == 'pl'
        assert 'fr' in ET.tostring(package, encoding='unicode')
    assert not app.publishing.publish(PublishCommand(root)).built
    assert {p: digest(p.read_bytes()) for p in protected} == hashes
    assert CountingOfflinePool.constructions == 0


@pytest.mark.parametrize('corrections', [{'title': ''}, {'creators': 'Name'}, {'language': 'English'}, {'unknown': 'x'}])
def test_rejected_edits_are_atomic(tmp_path, corrections):
    _, root, _ = _import_project(tmp_path)
    book = read_json(root / 'book.json')
    before = metadata.catalog_path(root.parent).read_bytes()
    with pytest.raises(ValueError):
        metadata.update(root, book, metadata.snapshot(root, book)['revision'], corrections)
    assert metadata.catalog_path(root.parent).read_bytes() == before


def test_metadata_api_updates_warm_lists_pipeline_reader_and_library(api):
    app, root, service, server = api
    status, before = request(server, 'GET', '/api/workspaces/book/metadata')
    assert status == 200
    request(server, 'GET', '/api/workspaces')  # Warm summary cache before editing.
    status, changed = request(server, 'PATCH', '/api/workspaces/book/metadata', {
        'revision': before['revision'], 'corrections': {'title': 'A corrected title', 'creators': ['The Author'], 'language': 'eng-US'},
    })
    assert status == 200, changed
    assert changed['book_id'] == before['book_id']
    assert changed['original'] == before['original']
    assert request(server, 'GET', '/api/workspaces')[1]['workspaces'][0]['metadata']['title'] == 'A corrected title'
    assert request(server, 'GET', '/api/workspaces/book/pipeline')[1]['metadata']['title'] == 'A corrected title'
    assert request(server, 'GET', '/api/workspaces/book/reader')[1]['title'] == 'A corrected title'
    assert request(server, 'GET', '/api/library')[1]['sources'][0]['title'] == 'A corrected title'
    status, error = request(server, 'PATCH', '/api/workspaces/book/metadata', {'revision': before['revision'], 'corrections': {'title': 'Lost update'}})
    assert status == 409 and error['error']['code'] == 'metadata_revision_conflict'
    assert request(server, 'PATCH', '/api/workspaces/book/metadata', {'revision': changed['revision'], 'corrections': {'title': ''}})[0] == 400


def test_relink_checks_content_and_restores_source_without_reimport(tmp_path):
    from bookpipe.application.source_relink import relink_epub
    from bookpipe.util import PipelineError
    app, root, source = _import_project(tmp_path)
    packed = tmp_path / 'relocated.epub'
    with zipfile.ZipFile(packed, 'w') as archive:
        for path in source.rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(source))
    source.rename(tmp_path / 'original-source')
    before = read_json(root / 'book.json')
    _complete_translations(root)
    state_hashes = {p: digest(p.read_bytes()) for p in (root / 'state').rglob('*') if p.is_file()}
    result = relink_epub(root, packed)
    after = read_json(root / 'book.json')
    assert {k: v for k, v in after.items() if k not in {'source_root', 'source_archive'}} == {k: v for k, v in before.items() if k not in {'source_root', 'source_archive'}}
    assert list((root / 'history' / 'source_relinks').glob('*/book.json'))
    assert result['book_id'] == metadata.book_id(before)
    assert app.publishing.publish(PublishCommand(root)).built
    assert {p: digest(p.read_bytes()) for p in state_hashes} == state_hashes
    bad = tmp_path / 'different.epub'
    with zipfile.ZipFile(packed) as archive, zipfile.ZipFile(bad, 'w') as changed:
        for name in archive.namelist():
            changed.writestr(name, b'changed' if name == before['files'][0]['file'] else archive.read(name))
    with pytest.raises(PipelineError, match='differs'):
        relink_epub(root, bad)
    assert read_json(root / 'book.json') == after


def test_workspace_link_survives_nested_library_location(api):
    from bookpipe.util import atomic_json
    _, root, service, server = api
    book = read_json(root / 'book.json')
    source = service.imports.root / 'book'
    nested = service.imports.root / 'Author' / 'Series' / 'book'
    nested.parent.mkdir(parents=True)
    source.rename(nested)
    book['source_root'] = str(nested)
    atomic_json(root / 'book.json', book)
    metadata.register(root, book, nested)
    status, listing = request(server, 'GET', '/api/workspaces?workspace_id=book')
    assert status == 200
    assert listing['workspaces'][0]['source_id'] == 'Author/Series/book'
