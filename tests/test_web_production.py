"""Production web boundaries use disposable projects and offline providers only."""
import asyncio
import io
import json
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import pytest

from test_server_api import api, analyze, approve, translate
from bookpipe.server.asgi import create_app
from bookpipe.application.web import LifecycleConflict, WorkspaceArchived
from bookpipe.processing import AnalysisMembershipLocked, ConfigConflict, effective_book
from bookpipe.util import read_json, PipelineError, project_lock
from bookpipe.application.commands import TranslateCommand
from bookpipe.application.commands import ImportBookCommand
from bookpipe.application.epub_sources import unpack_epub
from bookpipe.runtime.models import ImportJobSpec
from bookpipe.runtime.worker import execute
from bookpipe.runtime.protocol import JsonlProgressSink
from bookpipe.application.source_preflight import source_signature
from bookpipe.application.imports import RequestConflict, DestinationConflict
from bookpipe.application.source_preflight import detect_language
from test_pipeline import make_epub_source
from test_runtime import request


def test_configured_setup_is_explicit_durable_and_allows_multiple_workspaces(api):
    app, root, service, server = api
    source = service.imports.root / 'setup-book'
    source.mkdir()
    (source / 'chapter.html').write_text('<h1>Chapter</h1><p>The reader walks through the town and remembers the story.</p>')
    code, preflight = request(server, 'GET', '/api/library/sources/setup-book/preflight')
    assert code == 200
    assert preflight['source_fingerprint'] == source_signature(service.imports.root, 'setup-book')
    assert not any(w.get('source_id') == 'setup-book' for w in service.list_workspaces())
    profiles = {str(i): service.profiles()['default_profile'] for i in range(1, 6)}
    setup = {'source_id': 'setup-book', 'source_fingerprint': preflight['source_fingerprint'],
             'source_language': 'en', 'target_language': 'pl', 'label': 'Offline A',
             'pass_profiles': profiles, 'request_key': 'setup-request-0123456789'}
    code, first = request(server, 'POST', '/api/workspaces/setup', setup)
    assert code == 200
    assert request(server, 'POST', '/api/workspaces/setup', setup)[1] == first
    second = service.save_setup({**setup, 'label': 'Offline B', 'request_key': 'setup-request-9876543210'})
    assert first['workspace_id'] != second['workspace_id']
    for value in (first, second):
        destination = root.parent / value['workspace_id']
        assert (destination / 'workspace.json').is_file()
        assert (destination / 'settings.json').is_file()
        assert not (destination / 'book.json').exists()
        assert not (destination / 'state.sqlite3').exists()
        assert service.settings(value['workspace_id'])['assignments'] == profiles
    listed = [w for w in service.list_workspaces() if w.get('source_id') == 'setup-book']
    assert len(listed) == 2 and {w['metadata']['label'] for w in listed} == {'Offline A', 'Offline B'}
    archived = service.archive(second['workspace_id'],
                               {'revision': next(w for w in listed if w['workspace_id'] == second['workspace_id'])['metadata']['lifecycle']['revision']})
    assert archived['archived'] is True
    assert (root.parent / second['workspace_id'] / 'workspace.json').is_file()
    service.archive(second['workspace_id'], {'revision': archived['revision']}, False)
    destination = root.parent / first['workspace_id']
    code, error = request(server, 'POST', '/api/imports', {
        'workspace_id': first['workspace_id'], 'source_id': 'setup-book',
        'profile': service.profiles()['default_profile']})
    assert code == 400 and error['error']['code'] == 'invalid_request'
    spec = ImportJobSpec(workspace_root=str(root.parent), workspace_id=first['workspace_id'],
                         project=str(destination), import_root=str(service.imports.root), source_id='setup-book')
    assert execute(spec, JsonlProgressSink(io.StringIO()), application_factory=lambda _: app) == 0
    assert (destination / 'book.json').is_file()
    assert service.pipeline(first['workspace_id'])['stage'] == 'analysis'
    with pytest.raises(RequestConflict):
        service.save_setup({**setup, 'label': 'Changed'})
    (source / 'chapter.html').write_text('<p>Changed after preflight.</p>')
    with pytest.raises(RequestConflict):
        service.save_setup({**setup, 'request_key': 'setup-request-2222222222'})


def test_prepare_retry_accepts_only_the_empty_lock_left_by_a_failed_draft_import(api):
    _, root, service, _ = api
    source = service.imports.root / 'retry-book'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>Offline retry source.</p>')
    profiles = {str(i): service.profiles()['default_profile'] for i in range(1, 6)}
    draft = service.save_setup({
        'source_id': 'retry-book', 'source_fingerprint': source_signature(service.imports.root, 'retry-book'),
        'source_language': 'en', 'target_language': 'pl', 'label': None,
        'pass_profiles': profiles, 'request_key': 'retry-draft-0123456789',
    })
    destination = root.parent / draft['workspace_id']
    lock = destination / '.lock'
    lock.touch()  # OperationScope leaves this after a failed first Prepare.

    def spec():
        return ImportJobSpec(workspace_root=str(root.parent), workspace_id=draft['workspace_id'],
                             project=str(destination), import_root=str(service.imports.root), source_id='retry-book')

    assert spec().source_id == 'retry-book'
    assert not (destination / 'book.json').exists()
    lock.write_text('unexpected content')
    with pytest.raises(DestinationConflict):
        spec()
    lock.unlink()
    lock.symlink_to(source / 'chapter.html')
    with pytest.raises(DestinationConflict):
        spec()


def test_draft_pass_profile_can_change_with_revision_before_prepare(api):
    _, root, service, server = api
    source = service.imports.root / 'profile-book'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>Offline profile change source.</p>')
    initial = {str(i): 'local' for i in range(1, 6)}
    draft = service.save_setup({
        'source_id': 'profile-book', 'source_fingerprint': source_signature(service.imports.root, 'profile-book'),
        'source_language': 'en', 'target_language': 'pl', 'label': None,
        'pass_profiles': initial, 'request_key': 'profile-draft-0123456789',
    })
    ident = draft['workspace_id']
    destination = root.parent / ident
    code, before = request(server, 'GET', f'/api/workspaces/{ident}/profiles')
    assert code == 200 and before['assignments']['1'] == 'local'
    payload = {'revision': before['revision'], 'pass_profiles': {'1': 'codex-luna-low'},
               'allow_model_change': True}
    code, updated = request(server, 'PATCH', f'/api/workspaces/{ident}/settings', payload)
    assert code == 200 and updated['pass_profiles']['1'] == 'codex-luna-low'
    assert updated['revision'] != before['revision']
    code, after = request(server, 'GET', f'/api/workspaces/{ident}/profiles')
    assert code == 200 and after['revision'] == updated['revision']
    assert after['resolved_passes']['1']['name'] == 'codex-luna-low'
    assert after['resolved_passes']['2']['name'] == 'local'
    assert read_json(destination / 'settings.json')['pass_profiles']['1'] == 'codex-luna-low'
    assert not (destination / 'book.json').exists()
    code, stale = request(server, 'PATCH', f'/api/workspaces/{ident}/settings', payload)
    assert code == 409 and stale['error']['code'] == 'config_revision_conflict'
    code, confirmation = request(server, 'PATCH', f'/api/workspaces/{ident}/settings',
                                 {'revision': after['revision'], 'pass_profiles': {'1': 'local'}})
    assert code == 409 and confirmation['error']['code'] == 'model_change_confirmation_required'
    with project_lock(destination):
        code, busy = request(server, 'PATCH', f'/api/workspaces/{ident}/settings',
                             {'revision': after['revision'], 'pass_profiles': {'1': 'local'},
                              'allow_model_change': True})
        assert code == 409 and busy['error']['code'] == 'workspace_busy'
    assert not (destination / 'state.sqlite3').exists()


def test_web_prepare_estimates_locally_without_provider_and_p1_recounts(tmp_path):
    from bookpipe.bootstrap import create_application
    from bookpipe.engine import analysis_plan
    from bookpipe.store import Store

    source_root, workspace_root = tmp_path / 'sources', tmp_path / 'workspaces'
    source_root.mkdir(); workspace_root.mkdir()
    source = source_root / 'book'
    source.mkdir()
    (source / 'chapter.html').write_text('<h1>One</h1><p>Seven words are here for the source text.</p>')
    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError('Prepare must not construct a model provider.')
    app = create_application(provider_factory=forbidden_provider)
    destination = workspace_root / 'offline'
    spec = ImportJobSpec(workspace_root=str(workspace_root), workspace_id='offline',
                         project=str(destination), import_root=str(source_root), source_id='book')
    assert execute(spec, JsonlProgressSink(io.StringIO()), application_factory=lambda _: app) == 0
    book = read_json(destination / 'book.json')
    assert book['model_identity'] is None
    assert book['tokenizer_identity'] == {'method': 'chars_per_four_estimate', 'chars_per_token': 4}
    chapter = book['chapters'][0]
    text = '\n\n'.join(block['text'] for block in chapter['blocks'])
    assert chapter['source_tokens'] == (len(text) + 3) // 4
    assert chapter['source_tokens_quality'] == 'estimated'
    assert all(chunk['source_tokens_quality'] == 'estimated' for chunk in book['chunks'])

    class ActualP1Counter:
        context = 131072
        tokenizer_identity = book['tokenizer_identity']  # Even a matching identity cannot promote estimates.
        calls = 0
        def count(self, value):
            self.calls += 1
            return len(value)

    counter = ActualP1Counter()
    store = Store(destination)
    try:
        assert analysis_plan(store, book, counter, read_json(destination / 'settings.json'))
    finally:
        store.close()
    assert counter.calls > 0


def test_setup_rejects_unavailable_languages_and_unrelated_destinations(api):
    _, root, service, server = api
    profiles = {str(i): service.profiles()['default_profile'] for i in range(1, 6)}
    code, result = request(server, 'POST', '/api/library/compatibility', {
        'source_language': 'en', 'target_language': 'de', 'pass_profiles': profiles})
    assert code == 200 and result['compatible'] is False and result['target_choices'] == ['pl']
    code, result = request(server, 'POST', '/api/library/compatibility', {
        'source_language': None, 'target_language': 'pl', 'pass_profiles': profiles})
    assert code == 400 and result['error']['code'] == 'invalid_request'
    unrelated = root.parent / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'settings.json').write_text('{}')
    with pytest.raises(DestinationConflict):
        ImportJobSpec(workspace_root=str(root.parent), workspace_id='unrelated', project=str(unrelated),
                      import_root=str(service.imports.root), source_id='book')
    assert detect_language(['The author and the reader were in the town. ' * 20])[0] == 'en'
    assert detect_language(['Ada.'])[0] is None


def test_setup_accepts_builtin_profile_with_undeclared_language_capabilities(api):
    _, root, service, server = api
    source = service.imports.root / 'unknown-profile-language'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>The reader travels through the valley.</p>')
    code, preflight = request(server, 'GET', '/api/library/sources/unknown-profile-language/preflight')
    assert code == 200
    assignments = {str(number): 'codex-luna-low' for number in range(1, 6)}
    code, compatibility = request(server, 'POST', '/api/library/compatibility', {
        'source_language': 'en', 'target_language': 'pl', 'pass_profiles': assignments})
    assert code == 200
    assert compatibility['compatible'] is True
    assert len(compatibility['warnings']) == 2
    code, saved = request(server, 'POST', '/api/workspaces/setup', {
        'source_id': 'unknown-profile-language', 'source_fingerprint': preflight['source_fingerprint'],
        'source_language': 'en', 'target_language': 'pl', 'label': 'Language metadata unknown',
        'pass_profiles': assignments, 'request_key': 'unknown-profile-language-012345'})
    assert code == 200
    destination = root.parent / saved['workspace_id']
    assert (destination / 'settings.json').is_file()
    assert not (destination / 'book.json').exists()
    assert service.settings(saved['workspace_id'])['assignments'] == assignments


def test_source_inspection_reports_bounded_real_excerpts_not_chapter_filenames(api, tmp_path):
    _, root, service, server = api
    source = make_epub_source(tmp_path)
    chapter = source / 'EPUB' / 'text' / 'one.xhtml'
    chapter.write_text(chapter.read_text().replace('</body>',
        '<p>' + 'The reader followed the signal through the valley. ' * 30 + '</p>'
        '<script>window.alert("unsafe")</script></body>'))
    packed = service.imports.root / 'inspect.epub'
    with zipfile.ZipFile(packed, 'w') as archive:
        for path in source.rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    code, result = request(server, 'GET', '/api/library/sources/inspect.epub/inspect')
    assert code == 200
    assert result['document_count'] == result['sampled_documents'] == 2
    assert result['sample_word_count'] > 0
    assert [sample['position'] for sample in result['sample_previews']] == [1, 2]
    assert result['sample_previews'][0]['heading'] == 'Chapter 1'
    assert 'Relay moved steadily' in result['sample_previews'][0]['excerpt']
    assert len(result['sample_previews'][0]['excerpt']) == 320
    assert 'window.alert' not in result['sample_previews'][0]['excerpt']
    assert all('xhtml' not in sample['excerpt'] for sample in result['sample_previews'])
    assert 'section_preview' not in result
    assert not (root.parent / 'inspect.epub' / 'book.json').exists()
    assert detect_language(['się nie jest jak dla przez ' * 1000,
                            *(['the and that with from this ' * 1000] * 4)])[0] == 'en'


def test_setup_concurrent_saves_reuse_only_their_own_request_key(api):
    _, root, service, _ = api
    source = service.imports.root / 'parallel-book'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>The reader and the author were together in the town.</p>')
    fingerprint = source_signature(service.imports.root, 'parallel-book')
    profiles = {str(i): service.profiles()['default_profile'] for i in range(1, 6)}
    setup = {'source_id': 'parallel-book', 'source_fingerprint': fingerprint,
             'source_language': 'en', 'target_language': 'pl', 'label': None,
             'pass_profiles': profiles, 'request_key': 'parallel-save-1234567890'}
    with ThreadPoolExecutor(4) as pool:
        same = list(pool.map(service.save_setup, [setup] * 4))
    assert len({result['workspace_id'] for result in same}) == 1
    other = service.save_setup({**setup, 'request_key': 'parallel-save-0987654321'})
    assert other['workspace_id'] != same[0]['workspace_id']
    assert len([p for p in root.parent.iterdir() if (p / 'workspace.json').is_file()]) == 2
    original = root.parent / same[0]['workspace_id']
    (original / 'partial-import.txt').write_text('unexpected')
    with pytest.raises(DestinationConflict):
        ImportJobSpec(workspace_root=str(root.parent), workspace_id=same[0]['workspace_id'],
                      project=str(original), import_root=str(service.imports.root), source_id='parallel-book')


def test_d05_draft_has_no_project_or_model_and_two_tabs_resolve_once(api):
    app, root, service, _ = api
    source = service.imports.root / 'new-book'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>An offline source.</p>')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: service.create_draft({'source_id': 'new-book'}), range(2)))
    assert results[0] == results[1]
    assert not (root.parent / results[0]['workspace_id']).exists()
    assert any(not w['prepared'] for w in service.list_workspaces())


def test_library_refresh_discovers_packed_epub_and_prepare_uses_durable_snapshot(api, tmp_path):
    app, root, service, _ = api
    before = {item['source_id'] for item in service.library()['sources']}
    source = make_epub_source(tmp_path)
    packed = service.imports.root / 'New Book.epub'
    with zipfile.ZipFile(packed, 'w') as archive:
        for path in source.rglob('*'):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    discovered = {item['source_id']: item for item in service.library()['sources']}
    assert 'New Book.epub' not in before
    assert discovered['New Book.epub']['title'] == 'Relay Book'
    assert discovered['New Book.epub']['creators'] == ['Test Author']
    assert discovered['New Book.epub']['language'] == 'en'
    draft = service.create_draft({'source_id': 'New Book.epub'})
    assert not (root.parent / draft['workspace_id']).exists()
    spec = ImportJobSpec(workspace_root=str(root.parent), workspace_id=draft['workspace_id'],
                         project=str(root.parent / draft['workspace_id']), import_root=str(service.imports.root),
                         source_id='New Book.epub')
    assert spec.source_id == 'New Book.epub'
    destination = root.parent / draft['workspace_id']
    app.projects.import_book(ImportBookCommand(destination, packed))
    book = app.web.book(destination)
    assert book['source_archive'] == str(packed)
    assert (destination / 'source-package' / 'META-INF' / 'container.xml').is_file()
    assert service.pipeline(draft['workspace_id'])['preparation']['source_id'] == 'New Book.epub'
    assert next(item for item in service.library()['sources'] if item['source_id'] == 'New Book.epub')['workspace_id'] == draft['workspace_id']


@pytest.mark.parametrize('member', ['../escape.txt', '/absolute.txt', 'folder/../escape.txt', 'folder\\escape.txt'])
def test_packed_epub_rejects_unsafe_member_paths(tmp_path, member):
    packed = tmp_path / 'unsafe.epub'
    with zipfile.ZipFile(packed, 'w') as archive:
        archive.writestr(member, 'unsafe')
    project = tmp_path / 'workspace'
    project.mkdir()
    with pytest.raises(PipelineError, match='Unsafe EPUB member path'):
        unpack_epub(packed, project)
    assert not (tmp_path / 'escape.txt').exists()


def test_packed_epub_rejects_symlink_and_duplicate_members(tmp_path):
    project = tmp_path / 'workspace'
    project.mkdir()
    packed = tmp_path / 'symlink.epub'
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(packed, 'w') as archive:
        archive.writestr(link, '../outside')
    with pytest.raises(PipelineError, match='unsupported member type'):
        unpack_epub(packed, project)
    packed = tmp_path / 'duplicate.epub'
    with zipfile.ZipFile(packed, 'w') as archive:
        archive.writestr('one.txt', 'first')
        archive.writestr('one.txt', 'second')
    (tmp_path / 'other-workspace').mkdir()
    with pytest.raises(PipelineError, match='duplicate members'):
        unpack_epub(packed, tmp_path / 'other-workspace')


def test_library_page_cursor_loads_only_requested_metadata_and_rejects_bad_queries(api, monkeypatch):
    _, _, service, server = api
    for index in range(26):
        source = service.imports.root / f'new-{index:02d}'
        source.mkdir()
        (source / 'chapter.html').write_text('<p>Offline catalog fixture.</p>')
    calls = []
    original = service.catalog.source_metadata
    def observed(source_id):
        calls.append(source_id)
        return original(source_id)
    monkeypatch.setattr(service.catalog, 'source_metadata', observed)
    collected = []
    cursor = None
    while True:
        path = '/api/library?limit=12' + (f'&after={quote(cursor)}' if cursor else '')
        code, page = request(server, 'GET', path)
        assert code == 200
        assert len(page['sources']) <= 12
        collected.extend(item['source_id'] for item in page['sources'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(collected) == 27
    assert len(set(collected)) == 27
    assert calls == collected
    for query in ('limit=0', 'limit=41', 'limit=oops', 'limit=12&limit=12', 'after=..%2Fetc', 'unexpected=1'):
        assert request(server, 'GET', '/api/library?' + query)[0] == 400


def test_draft_archive_restore_uses_catalog_revision_without_importing(api):
    _, root, service, _ = api
    source = service.imports.root / 'draft-only'
    source.mkdir()
    (source / 'chapter.html').write_text('<p>Offline draft.</p>')
    draft = service.create_draft({'source_id': 'draft-only'})
    ident = draft['workspace_id']
    before = next(w for w in service.list_workspaces() if w['workspace_id'] == ident)
    revision = before['metadata']['lifecycle']['revision']
    archived = service.archive(ident, {'revision': revision})
    assert archived['archived'] is True
    assert not (root.parent / ident).exists()
    from bookpipe.application.web import LifecycleConflict, WorkspaceArchived
    with pytest.raises(LifecycleConflict):
        service.archive(ident, {'revision': revision}, False)
    with pytest.raises(WorkspaceArchived):
        service.prepare(ident, {})
    assert service.create_draft({'source_id': 'draft-only'})['workspace_id'] == ident
    restored = service.archive(ident, {'revision': archived['revision']}, False)
    assert restored['archived'] is False
    assert not (root.parent / ident).exists()


def test_d02_membership_and_revision_preserve_checkpoints(api):
    app, root, service, _ = api
    config = app.web.config(root)
    updated = service.configure('book', {'revision': config['revision'], 'processing': 'translate'}, 'ch0002')
    book = app.web.book(root)
    assert len(effective_book(book, root, phase='analysis')['chapters']) == 1
    assert len(effective_book(book, root)['chunks']) == 2
    with pytest.raises(ConfigConflict):
        service.configure('book', {'revision': config['revision'], 'processing': 'excluded'}, 'ch0002')
    analyze(root)
    with pytest.raises(AnalysisMembershipLocked):
        service.configure('book', {'revision': updated['revision'], 'processing': 'translate'}, 'ch0001')
    before = list((root / 'artifacts').rglob('result.json'))
    updated = service.configure('book', {'revision': updated['revision'], 'processing': 'excluded'}, 'ch0002')
    assert len(effective_book(book, root)['chunks']) == 1
    service.configure('book', {'revision': updated['revision'], 'processing': 'translate'}, 'ch0002')
    assert all(p.exists() for p in before)


def test_d08_edit_blocks_application_before_provider_and_atomic_confirmation(api):
    app, root, service, _ = api
    analyze(root)
    draft = service.review('book', 'prepare', {})

    patched = service.review('book', 'patch', {'revision': draft['_revision'], 'reviewed': True}, 'T000001')
    result = service.approve('book', {'revision': patched['revision']}, confirm_review=True)
    assert result['pipeline']['approved']
    translate(root, 1)
    draft = service.review('book')
    service.review('book', 'patch', {'revision': draft['_revision'], 'custom': 'Adela'}, 'T000001')
    assert not service.pipeline('book')['approved']
    assert service.pipeline('book')['progress']['percent'] == 32
    assert service.pipeline('book')['actions']['translate']['reason'] == 'approval_required'
    with pytest.raises(PipelineError, match='latest terminology'):
        app.pipeline.translate(TranslateCommand(root))
    assert service.pipeline('book')['units'][0]['status'] == 'done'


def test_archive_restore_revision_and_job_gate(api):
    app, root, service, _ = api
    revision = app.web.lifecycle(root)['revision']
    result = service.archive('book', {'revision': revision})
    assert result['archived']
    with pytest.raises(WorkspaceArchived):
        service.start('book', {'operation': 'analyze'})
    with pytest.raises(LifecycleConflict):
        service.archive('book', {'revision': revision}, False)
    assert not service.archive('book', {'revision': result['revision']}, False)['archived']


def test_preview_and_progress_are_application_facts(api):
    app, root, service, _ = api
    preview = app.web.preview(root, 'ch0001')
    assert any('Ada visits' in b['text'] for b in preview['blocks'])
    progress = service.pipeline('book')['progress']
    assert progress['percent'] is None
    assert progress['analysis']['denominator'] == 'required P1 analysis units'
    assert progress['translation']['denominator'] == 'required P5 translation units'
    analyze(root)
    assert service.pipeline('book')['progress']['percent'] == 32
    approve(service)
    translate(root, 1)
    assert service.pipeline('book')['progress']['percent'] == 64


def test_asgi_guards_and_static_route_isolation(api, tmp_path):
    _, _, service, _ = api
    frontend = tmp_path / 'dist'
    frontend.mkdir()
    (frontend / 'index.html').write_text('<html>Intelitex</html>')
    app = create_app(service, {'test:80'}, frontend=frontend)
    async def checks():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test:80', headers={'Host': 'test:80'}) as client:
            assert (await client.get('/work/workspaces/book/review')).status_code == 200
            for path in ['/api/missing', '/assets/missing.js', '/book.json', '/docs']:
                response = await client.get(path)
                assert response.status_code == 404
                assert response.headers['content-type'].startswith('application/json')
            response = await client.get('/api/workspaces/book/pipeline')
            assert response.status_code == 200
            assert response.json()['sections']
            assert "script-src 'self'" in response.headers['content-security-policy']
            for method, path in [('GET', '/api/library'), ('POST', '/api/workspaces'), ('PATCH', '/api/workspaces/book/settings')]:
                response = await client.request(method, path, json={}, headers={'Origin': 'http://evil.test'})
                assert response.status_code == 403
            for value in ['{"source_id":"book","source_id":"book"}', '{"value":NaN}', '[]']:
                response = await client.post('/api/workspaces', content=value, headers={'Content-Type': 'application/json'})
                assert response.status_code == 400
    asyncio.run(checks())


def test_request_receipt_two_tabs_replays_and_conflicting_payload_rejects(api):
    from bookpipe.runtime.supervisor import RequestConflict
    _, _, service, _ = api
    payload = {'operation': 'analyze', 'request_key': 'test-request-0123456789'}
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: service.start('book', payload), range(2)))
    assert results[0]['job_id'] == results[1]['job_id']
    assert len(service.supervisor.list()) == 1
    with pytest.raises(RequestConflict):
        service.start('book', {**payload, 'operation': 'translate'})


def test_draft_resolves_existing_source_and_unconfigured_library_is_empty(api):
    _, root, service, _ = api
    assert service.create_draft({'source_id': 'book'})['workspace_id'] == 'book'
    assert len(service.list_workspaces()) == 1
    service.imports.root = None
    assert service.library() == {'sources': [], 'configured': False}
    assert service.list_workspaces()[0]['workspace_id'] == 'book'


def test_unconfigured_source_root_keeps_persisted_draft_listable(api):
    _, root, service, _ = api
    source = service.imports.root / 'offline-only'
    source.mkdir()
    draft = service.create_draft({'source_id': 'offline-only'})
    service.imports.root = None
    listed = next(w for w in service.list_workspaces() if w['workspace_id'] == draft['workspace_id'])
    assert listed['prepared'] is False
    assert listed['metadata']['title'] == 'offline-only'
    assert listed['progress']['percent'] is None
    assert not (root.parent / draft['workspace_id']).exists()


def test_dormant_reinclude_requires_checked_current_inputs(api):
    app, root, service, _ = api
    revision = app.web.config(root)['revision']
    update = service.configure('book', {'revision': revision, 'processing': 'translate'}, 'ch0002')
    analyze(root); approve(service); translate(root)
    final = (root / 'artifacts' / 'pass5' / 'ch0002_c0001' / 'result.json').read_bytes()
    update = service.configure('book', {'revision': update['revision'], 'processing': 'excluded'}, 'ch0002')
    assert len(service.pipeline('book')['units']) == 1
    service.configure('book', {'revision': update['revision'], 'processing': 'translate'}, 'ch0002')
    # Legacy fixture finals have no persisted inputs: retain but do not claim current reuse.
    assert service.pipeline('book')['units'][1]['status'] == 'stale'
    assert (root / 'artifacts' / 'pass5' / 'ch0002_c0001' / 'result.json').read_bytes() == final


def test_processing_projection_is_idempotent_and_profile_colors_stable(api):
    app, root, service, _ = api
    initial = service.profiles()
    palette = {p['name']: p['stable_palette_index'] for p in initial['profiles']}
    service.catalog.register_profiles(['extra-offline-profile', *reversed(palette)])
    assert {p['name']: p['stable_palette_index'] for p in service.profiles()['profiles']} == palette
    config = app.web.config(root)
    service.configure('book', {'revision': config['revision'], 'processing': 'excluded'}, 'ch0002')
    once = effective_book(app.web.book(root), root)
    twice = effective_book(once, root)
    assert [c['id'] for c in once['chunks']] == [c['id'] for c in twice['chunks']]
    assert len(once['non_narrative_sections']) == len(twice['non_narrative_sections'])


def test_web_review_reads_do_not_take_writer_lock_or_mutate_files(api):
    from bookpipe.util import project_lock
    _, root, service, _ = api
    analyze(root)
    service.review('book', 'prepare', {})
    before = (root / 'terms.review.json').read_bytes()
    with project_lock(root):
        assert service.review('book')['terms']
        assert service.review('book', 'evidence', term_id='T000001')['entries']
    assert (root / 'terms.review.json').read_bytes() == before


def test_draft_request_key_conflicts_without_creating_another_workspace(api):
    from bookpipe.application.imports import RequestConflict
    _, _, service, _ = api
    payload = {'source_id': 'book', 'request_key': 'draft-key-0123456789'}
    one = service.create_draft(payload)
    assert service.create_draft(payload) == one
    extra = service.imports.root / 'other'
    extra.mkdir()
    with pytest.raises(RequestConflict):
        service.create_draft({**payload, 'source_id': 'other'})
    assert len(service.catalog.entries()) == 1


def test_preparation_checks_are_validated_not_inferred_from_prepared(api):
    app, root, service, _ = api
    result = app.web.preparation(root)
    assert result['checks'] == ['Frozen source/chunk manifest verified']
    assert 'not applicable' in result['unavailable']
    book = read_json(root / 'book.json')
    book['chunks'][0]['blocks'][0]['text'] = 'tampered'
    (root / 'book.json').write_text(json.dumps(book))
    with pytest.raises(PipelineError):
        app.web.preparation(root)
