"""Production web boundaries use disposable projects and offline providers only."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from test_server_api import api, analyze, approve, translate
from bookpipe.server.asgi import create_app
from bookpipe.application.web import LifecycleConflict, WorkspaceArchived
from bookpipe.processing import AnalysisMembershipLocked, ConfigConflict, effective_book
from bookpipe.util import read_json, PipelineError
from bookpipe.application.commands import TranslateCommand


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
    assert listed['progress']['percent'] == 0
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
