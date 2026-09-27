"""Real nested sources, folder declarations, global ordering and HTTP navigation."""
from urllib.parse import quote
from zipfile import ZipFile

import pytest

from bookpipe.application.imports import ImportQueries
from bookpipe.application.library_groups import group_metadata
from test_server_api import api
from test_runtime import request


def epub(path, title='Example', author='Writer', language='en'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
        z.writestr('EPUB/book.opf', f'<package><metadata><title>{title}</title><creator>{author}</creator><language>{language}</language></metadata>'
                   '<manifest><item id="one" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/></spine></package>')
        z.writestr('EPUB/chapter.xhtml', '<h1>Chapter</h1><p>The reader walks through the town and remembers the story.</p>')


def test_discovery_keeps_package_roots_and_skips_containers_and_symlinks(tmp_path):
    root = tmp_path / 'books'
    epub(root / 'Author/Series/03.Book.epub')
    packed = root / 'Author/Series/03.Book.epub'
    extracted = root / 'Author/Series/02.Book'
    with ZipFile(packed) as z:
        z.extractall(extracted)
    loose = root / 'Notes/Novel'
    loose.mkdir(parents=True)
    (loose / 'chapter.html').write_text('<p>Story.</p>')
    (root / 'Empty').mkdir()
    (root / 'cycle').symlink_to(root)
    (root / 'outside').symlink_to(tmp_path)
    (root / 'broken.epub').write_text('Not a ZIP')
    assert ImportQueries(root).sources() == [
        {'source_id': 'Author/Series/02.Book'}, {'source_id': 'Author/Series/03.Book.epub'},
        {'source_id': 'Notes/Novel'},
    ]


def test_optional_yaml_is_explicit_and_invalid_yaml_keeps_group_visible(tmp_path):
    folder = tmp_path / 'Author'
    folder.mkdir()
    assert group_metadata(tmp_path, 'Author') == {'group_id': 'Author', 'name': 'Author', 'kind': None}
    declaration = folder / 'library.yaml'
    declaration.write_text('version: 1\nkind: author\nname: "An author"\n')
    assert group_metadata(tmp_path, 'Author')['name'] == 'An author'
    assert group_metadata(tmp_path, 'Author')['kind'] == 'author'
    for invalid in ['version: 2', 'version: 1\nkind: []', 'version: 1\nname: [x]', '!!python/object:builtins.object {}']:
        declaration.write_text(invalid)
        result = group_metadata(tmp_path, 'Author')
        assert result['kind'] is None and result['name'] == 'Author'
        assert result['warning'].startswith('Invalid Author/library.yaml:')


@pytest.mark.parametrize('sort', ['author', 'title', 'language'])
def test_global_sort_keeps_nested_groups_together_across_pages(api, sort):
    _, _, service, server = api
    root = service.imports.root
    epub(root / 'Hamilton/Salvation/01.First.epub', 'First', 'Hamilton', 'en')
    epub(root / 'Hamilton/Salvation/02.Last.epub', 'Last', 'Hamilton, Peter F.', 'pl')
    epub(root / 'Hamilton/Other.epub', 'Other', 'Hamilton', 'en')
    epub(root / 'Root.epub', 'A title', 'Z Author', 'de')
    (root / 'Hamilton/library.yaml').write_text('version: 1\nkind: author\nname: Peter F. Hamilton\n')
    (root / 'Hamilton/Salvation/library.yaml').write_text('version: 1\nkind: series\n')
    cursor, all_sources = None, []
    for _ in range(10):
        code, page = request(server, 'GET', f'/api/library?limit=2&links=false&sort={sort}' + (f'&after={quote(cursor, safe="")}' if cursor else ''))
        assert code == 200
        all_sources.extend(page['sources'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert cursor is None
    ids = [s['source_id'] for s in all_sources]
    assert len(ids) == len(set(ids)) == 5
    expected = {
        'author': ['Hamilton/Other.epub', 'Hamilton/Salvation/01.First.epub', 'Hamilton/Salvation/02.Last.epub', 'Root.epub', 'book'],
        'title': ['Root.epub', 'book', 'Hamilton/Other.epub', 'Hamilton/Salvation/01.First.epub', 'Hamilton/Salvation/02.Last.epub'],
        'language': ['Root.epub', 'Hamilton/Other.epub', 'Hamilton/Salvation/01.First.epub', 'Hamilton/Salvation/02.Last.epub', 'book'],
    }
    assert ids == expected[sort]
    first = next(s for s in all_sources if s['source_id'].endswith('01.First.epub'))
    assert first['groups'] == [
        {'group_id': 'Hamilton', 'name': 'Peter F. Hamilton', 'kind': 'author'},
        {'group_id': 'Hamilton/Salvation', 'name': 'Salvation', 'kind': 'series'},
    ]
    # Nested IDs round-trip through both HTTP adapters, including an encoded slash.
    code, info = request(server, 'GET', '/api/library/sources/' + quote(first['source_id'], safe='') + '/preflight')
    assert code == 200 and info['title'] == 'First'
    code, reader = request(server, 'GET', '/api/library/sources/' + quote(first['source_id'], safe='') + '/reader')
    assert code == 200 and reader['title'] == 'First'


def test_refresh_detects_metadata_yaml_and_location_changes_without_touching_workspace(api, monkeypatch):
    _, workspace, service, _ = api
    root = service.imports.root
    source = root / 'Author/Book.epub'
    epub(source)
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    calls = []
    from bookpipe.application import catalog
    original = catalog.packed_epub_metadata
    monkeypatch.setattr(catalog, 'packed_epub_metadata', lambda p: (calls.append(p), original(p))[1])
    for _ in range(2):
        page = service.library_page({'sort': 'author'})
        assert next(s for s in page['sources'] if s['source_id'] == 'Author/Book.epub')['title'] == 'Example'
    assert calls == [source]
    epub(source, title='Changed title')
    (source.parent / 'library.yaml').write_text('version: 1\nkind: category\nname: Science fiction\n')
    changed = next(s for s in service.library_page({'sort': 'title'})['sources'] if s['source_id'] == 'Author/Book.epub')
    assert changed['title'] == 'Changed title' and changed['groups'][0]['name'] == 'Science fiction'
    source.rename(root / 'Moved.epub')
    ids = [s['source_id'] for s in service.library_page({'sort': 'title'})['sources']]
    assert 'Moved.epub' in ids and 'Author/Book.epub' not in ids
    assert before == {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}


def test_library_rejects_invalid_sort_and_nested_cursor(api):
    _, _, _, server = api
    for query in ['sort=wrong', 'sort=title&after=..%2Fsecret', 'after=%2Fabsolute', 'after=x%2F%2Fy', 'sort=title&after=removed.epub']:
        assert request(server, 'GET', '/api/library?' + query)[0] == 400
