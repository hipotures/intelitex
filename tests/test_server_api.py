"""Single-server workflow, application concurrency, confined import and HTTP policy."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
import sys
import threading

import pytest

from bookpipe.application.commands import ApproveCommand, ImportBookCommand
from bookpipe.application.review import ReviewConflict
from bookpipe.application.workspaces import WorkspaceQueries
from bookpipe.bootstrap import create_application
from bookpipe.runtime.models import ImportJobSpec, JobSpec
from bookpipe.runtime.protocol import JsonlProgressSink
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.supervisor import JobSupervisor
from bookpipe.runtime.worker import execute
from bookpipe.server.http import BODY_LIMIT, IntelitexHTTPServer
from bookpipe.server.service import ServerService
from bookpipe.store import Store
from bookpipe.util import LockConflict, atomic_json, project_lock, reader_lock, read_json
from test_application import LocalImportPool
from test_runtime import HELPER, read_sse, request, running, terminal
from test_pipeline import server as provider_server, make_epub_source


@pytest.fixture(params=['compat', 'production'])
def api(tmp_path, request):
    sources, root = tmp_path / 'sources', tmp_path / 'workspaces'
    sources.mkdir(); root.mkdir()
    source = sources / 'book'
    source.mkdir()
    for index in range(2):
        (source / f'chapter{index}.html').write_text(f'<h1>Chapter {index + 1}</h1><p>Ada visits the town.</p>')
    app = create_application(provider_factory=LocalImportPool)
    app.projects.import_book(ImportBookCommand(root / 'book', source, chapter_mode='file'))
    registry = JobRegistry(tmp_path / 'registry' / 'jobs.sqlite3')
    supervisor = JobSupervisor(registry, root, command_factory=lambda spec: [sys.executable, '-u', str(HELPER), 'hold'],
                               interrupt_grace=.3, terminate_grace=.2)
    service = ServerService(app, WorkspaceQueries(app.projects, root), supervisor, import_root=sources)
    from bookpipe.server.asgi import ASGIServer
    adapter = ASGIServer if request.param == 'production' else IntelitexHTTPServer
    server = adapter(('127.0.0.1', 0), service)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    try:
        yield app, root / 'book', service, server
    finally:
        supervisor.shutdown(); server.shutdown(); server.server_close(); thread.join(); registry.close()


def analyze(root):
    book = read_json(root / 'book.json')
    store = Store(root)
    term = {'source': 'Ada', 'aliases': [], 'category': 'people',
            'meanings': [{'text': 'A visitor', 'confidence': 'high', 'evidence': []}],
            'candidates': [{'text': 'Ada', 'reasons': ['A name'], 'confidence': 'high'}],
            'evidence': [{'chapter_id': book['chapters'][0]['id'], 'block_id': book['chapters'][0]['blocks'][-1]['id'], 'excerpt': 'Ada visits.'}]}
    with store.db:
        store.db.execute('INSERT INTO terms(data) VALUES (?)', (json.dumps(term),))
    units = []
    for chapter in book['chapters']:
        ident = chapter['id'] + '_a001'
        units.append({'id': ident, 'chapter_id': chapter['id']})
        path = root / 'artifacts' / 'pass1' / ident / 'result.json'
        atomic_json(path, {'terms': [], 'observations': []})
        key = 'pass1/' + ident
        store.save_job(key, ident, path, {})
        store.save_analysis_receipt(ident, {'key': key, 'fingerprint': ident})
    atomic_json(root / 'analysis_plan.json', units)
    store.finish_analysis(); store.close()


def translate(root, count=2):
    book = read_json(root / 'book.json')
    store = Store(root)
    for chunk in book['chunks'][:count]:
        for number in range(2, 6):
            path = root / 'artifacts' / f'pass{number}' / chunk['id'] / 'result.json'
            atomic_json(path, {'translations': [{'id': block['id'], 'text': 'Ada idzie.'} for block in chunk['blocks']]})
            store.save_job(f"pass{number}/{chunk['id']}", chunk['id'], path, {})
        store.finish_chunk(chunk['id'], str(path.relative_to(root)), ['T000001'], 'lexical')
    store.close()


def approve(service):
    draft = service.review('book', 'prepare', {})
    value = service.review('book', 'bulk', {'term_ids': ['T000001'], 'revision': draft['_revision']})
    value = service.review('book', 'confirmation', {'confirmed': True, 'revision': value['revision']})
    return service.approve('book', {'revision': value['revision']})


def test_pipeline_reuses_one_validated_book_for_sections_metadata_and_usage(api, monkeypatch):
    app, root, service, _ = api
    files = app.web.dependencies.files
    original = type(files).read_json
    reads = []

    def counted(instance, path):
        if path == root / 'book.json':
            reads.append(path)
        return original(instance, path)

    monkeypatch.setattr(type(files), 'read_json', counted)
    value = service.pipeline('book')
    assert value['sections'] and value['metadata']['title']
    assert len(reads) == 1


def test_pipeline_lifecycle_and_stale_approval(api):
    app, root, service, server = api
    first = service.pipeline('book')
    assert first['stage'] == 'analysis' and not first['analysis']['planned']
    assert first['actions']['translate']['reason'] == 'analysis_required'
    analyze(root)
    value = service.pipeline('book')
    assert value['analysis']['complete'] and all(u['state'] == 'completed' for u in value['analysis']['units'])
    assert value['stage'] == 'review' and value['actions']['approve']['reason'] == 'review_not_confirmed'
    assert approve(service)['pipeline']['stage'] == 'translation'
    translate(root, 1)
    value = service.pipeline('book')
    assert [u['status'] for u in value['units']] == ['done', 'pending']
    assert value['units'][0]['passes']['2']['checkpoint_state'] == 'retained'
    assert value['units'][0]['passes']['5']['checkpoint_state'] == 'completed'
    translate(root)
    assert service.pipeline('book')['translation_complete']
    assert service.pipeline('book')['actions']['publish']['reason'] == 'publication_not_ready'
    draft = service.review('book')
    changed = service.review('book', 'patch', {'revision': draft['_revision'], 'custom': 'Adela', 'reviewed': True}, 'T000001')
    confirmed = service.review('book', 'confirmation', {'revision': changed['revision'], 'confirmed': True})
    with pytest.raises(ReviewConflict):
        app.review.approve(ApproveCommand(root, expected_revision=draft['_revision']))
    assert service.approve('book', {'revision': confirmed['revision']})['stale_chunks'] == 2
    value = service.pipeline('book')
    assert all(u['status'] == 'stale' and u['passes']['5']['checkpoint_state'] == 'stale' for u in value['units'])
    assert value['publication']['state'] == 'not_ready'
    raw = json.dumps(request(server, 'GET', '/api/workspaces/book/pipeline')[1])
    assert str(root.parent.parent) not in raw and 'source_root' not in raw


def test_pipeline_corrupt_checkpoint_reports_error_without_recompute(api):
    _, root, service, _ = api
    analyze(root); approve(service); translate(root)
    path = next((root / 'artifacts' / 'pass1').glob('*/result.json'))
    path.write_text('{}')
    value = service.pipeline('book')
    assert any(u['state'] == 'error' for u in value['analysis']['units'])
    path = next((root / 'artifacts' / 'pass5').glob('*/result.json'))
    path.write_text('{}')
    # Publication validation itself may refuse corrupt P5; it must never repair it.
    from bookpipe.util import PipelineError
    try:
        value = service.pipeline('book')
        assert any(u['passes']['5']['checkpoint_state'] == 'error' for u in value['units'])
    except PipelineError:
        pass
    assert path.read_text() == '{}'


def test_review_http_operations_revision_conflicts_and_evidence(api):
    _, root, service, server = api
    analyze(root)
    base = '/api/workspaces/book'
    code, draft = request(server, 'POST', base + '/review/prepare', {})
    assert code == 200
    assert request(server, 'GET', base + '/review')[1] == draft
    with project_lock(root):
        pass  # no browser-lifetime lock
    evidence = request(server, 'GET', base + '/review/terms/T000001/evidence')
    assert evidence[0] == 200 and evidence[1]['entries'][0]['status'] == 'not_translated'
    code, changed = request(server, 'PATCH', base + '/review/terms/T000001', {'revision': draft['_revision'], 'user_notes': 'Check'})
    assert code == 200
    code, error = request(server, 'PATCH', base + '/review/terms/T000001', {'revision': draft['_revision'], 'custom': 'New'})
    assert code == 409 and error['error']['code'] == 'review_revision_conflict'
    code, bulk = request(server, 'POST', base + '/review/bulk-review', {'revision': changed['revision'], 'term_ids': ['T000001']})
    assert code == 200 and bulk['changed_count'] == 1
    for confirmed in (True, False, True):
        code, bulk = request(server, 'POST', base + '/review/confirmation', {'revision': bulk['revision'], 'confirmed': confirmed})
        assert code == 200 and bulk['summary']['confirmed'] is confirmed
    assert request(server, 'POST', base + '/approve', {'revision': draft['_revision']})[0] == 409
    assert request(server, 'POST', base + '/approve', {'revision': bulk['revision']})[0] == 200


def test_concurrent_review_requests_commit_exactly_once(api):
    _, root, service, server = api
    analyze(root)
    draft = service.review('book', 'prepare', {})
    def patch(index):
        return request(server, 'PATCH', '/api/workspaces/book/review/terms/T000001',
                       {'revision': draft['_revision'], 'user_notes': str(index)})[0]
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(patch, range(6)))
    assert codes.count(200) == 1 and codes.count(409) == 5


def test_reader_http_and_marker_concurrency(api):
    _, root, service, server = api
    analyze(root); approve(service); translate(root)
    base = '/api/workspaces/book/reader'
    code, metadata = request(server, 'GET', base)
    assert code == 200
    chapter_id = metadata['chapters'][0]['id']
    code, chapter = request(server, 'GET', base + '/chapters/' + chapter_id)
    assert code == 200 and chapter['complete']
    assert request(server, 'GET', base + '/chapters/no-such-chapter')[0] == 422
    assert request(server, 'GET', base + '/chapters/%2Fetc%2Fpasswd')[0] == 422
    block = chapter['blocks'][0]
    assert request(server, 'POST', base + '/context', {'chapter_id': chapter_id, 'block_id': block['id'], 'position': 1})[0] == 200
    assert request(server, 'GET', base + '/progress')[1]['total_words'] > 0
    revision = request(server, 'GET', base + '/markers')[1]['_revision']
    payload = {'revision': revision, 'chapter_id': chapter_id, 'block_id': block['id'], 'start': 0, 'end': 3, 'text': 'Ada'}
    code, created = request(server, 'POST', base + '/markers', payload)
    assert code == 200 and created['created']
    assert request(server, 'POST', base + '/markers', payload)[1]['error']['code'] == 'marker_revision_conflict'
    marker_url = base + '/markers/' + created['marker']['id']
    assert request(server, 'DELETE', marker_url, {'revision': revision})[0] == 409
    assert request(server, 'DELETE', marker_url, {'revision': created['revision']})[0] == 200
    with reader_lock(root), project_lock(root):
        assert request(server, 'GET', base)[0] == 200
        assert request(server, 'GET', base + '/chapters/' + chapter_id)[0] == 200
        assert request(server, 'GET', base + '/markers')[0] == 200
    with reader_lock(root):
        pass  # request left no lock


def test_queries_with_active_workers_same_and_other_workspace(api):
    _, root, service, server = api
    job = service.supervisor.start(JobSpec(str(root.parent), root.name, str(root), 'analyze'))
    running(service.supervisor, job)
    pipeline = request(server, 'GET', '/api/workspaces/book/pipeline')[1]
    assert pipeline['active_job']['job_id'] == job.job_id
    assert pipeline['actions']['analyze']['reason'] == 'workspace_busy'
    for suffix in ('reader', 'reader/progress', 'reader/markers', 'profiles', 'settings', 'usage'):
        assert request(server, 'GET', '/api/workspaces/book/' + suffix)[0] == 200
    other = root.parent / 'other'
    service.application.projects.import_book(ImportBookCommand(other, service.imports.root / 'book'))
    other_job = service.supervisor.start(JobSpec(str(root.parent), 'other', str(other), 'translate'))
    running(service.supervisor, other_job)
    assert request(server, 'GET', '/api/workspaces/book/reader')[0] == 200


def test_settings_are_read_only_and_allowlisted(api):
    _, root, service, server = api
    path = root / 'settings.json'
    value = read_json(path)
    value['profiles']['local']['endpoint'] = 'https://private.invalid'
    value['profiles']['local']['credential_env'] = 'PRIVATE_KEY'
    value['profiles']['local']['model'] = str(root / 'secret-model.gguf')
    atomic_json(path, value)
    before = path.read_bytes()
    code, value = request(server, 'GET', '/api/workspaces/book/settings')
    assert code == 200 and value['default_profile'] == 'local'
    assert set(value['resolved_passes']) == {'1', '2', '3', '4', '5'}
    raw = json.dumps(value)
    assert all(secret not in raw for secret in ('PRIVATE_KEY', 'private.invalid', 'auth_source', str(root), 'credential_env', 'runtime_root'))
    assert before == path.read_bytes()


MUTATIONS = [
    ('POST', '/api/workspaces'), ('POST', '/api/workspaces/book/prepare'),
    ('PATCH', '/api/workspaces/book/settings'), ('PATCH', '/api/workspaces/book/sections/ch0001'),
    ('POST', '/api/workspaces/book/archive'), ('POST', '/api/workspaces/book/restore'),
    ('POST', '/api/workspaces/book/review/confirm-and-approve'),
    ('POST', '/api/imports'), ('POST', '/api/workspaces/book/review/prepare'),
    ('PATCH', '/api/workspaces/book/review/terms/T000001'),
    ('POST', '/api/workspaces/book/review/bulk-review'), ('POST', '/api/workspaces/book/review/confirmation'),
    ('POST', '/api/workspaces/book/approve'), ('POST', '/api/workspaces/book/reader/context'),
    ('POST', '/api/workspaces/book/reader/markers'), ('DELETE', '/api/workspaces/book/reader/markers/M000001'),
]
READS = ['/api/library', '/api/requests/unknown', '/api/workspaces/book/preparation', '/api/workspaces/book/activity', '/api/workspaces/book/sections/ch0001/0', '/api/workspaces/book/publication/download', '/api/profiles', '/api/capabilities', '/api/import-sources', '/api/workspaces/book/pipeline',
         '/api/workspaces/book/review', '/api/workspaces/book/review/terms/T000001/evidence',
         '/api/workspaces/book/reader', '/api/workspaces/book/reader/progress',
         '/api/workspaces/book/reader/markers', '/api/workspaces/book/reader/chapters/ch0001',
         '/api/workspaces/book/profiles', '/api/workspaces/book/settings']


@pytest.mark.parametrize('method,path', MUTATIONS + [('GET', path) for path in READS])
@pytest.mark.parametrize('headers', [{'Host': 'evil.invalid'}, {'Origin': 'https://evil.invalid'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_every_new_route_enforces_origin(api, method, path, headers):
    response = request(api[3], method, path, {} if method != 'GET' else None, headers)
    assert response[0] == 403 and response[1]['error']['code'] == 'origin_rejected'


@pytest.mark.parametrize('method,path', MUTATIONS)
@pytest.mark.parametrize('kind,expected', [('content_type', 400), ('oversized', 413), ('malformed', 400), ('array', 400), ('unknown', 400)])
def test_every_mutation_validates_body(api, method, path, kind, expected):
    analyze(api[1]); api[2].review('book', 'prepare', {})
    connection = http.client.HTTPConnection(*api[3].server_address, timeout=3)
    raw = {'content_type': '{}', 'oversized': json.dumps({'x': 'a' * BODY_LIMIT}),
           'malformed': '{', 'array': '[]', 'unknown': '{"unexpected":true}'}[kind]
    connection.request(method, path, raw, {'Content-Type': 'text/plain' if kind == 'content_type' else 'application/json'})
    response = connection.getresponse()
    value = json.loads(response.read()); connection.close()
    assert response.status == expected and isinstance(value['error']['code'], str)


@pytest.mark.parametrize('suffix', ['pipeline', 'review', 'reader', 'reader/progress', 'reader/markers', 'profiles', 'settings'])
@pytest.mark.parametrize('identifier', ['%2e%2e', 'x%2Fy', '%2Fetc', 'x%5Cy'])
def test_query_workspace_traversal(api, suffix, identifier):
    assert request(api[3], 'GET', f'/api/workspaces/{identifier}/{suffix}')[0] == 400


def test_errors_and_evidence_do_not_leak_paths_or_provider_bodies(api, monkeypatch):
    _, root, service, server = api
    analyze(root); approve(service); translate(root)
    next((root / 'artifacts' / 'pass5').glob('*/result.json')).unlink()
    raw = json.dumps(request(server, 'GET', '/api/workspaces/book/review/terms/T000001/evidence')[1])
    assert str(root) not in raw
    def fail(*args, **kwargs):
        raise RuntimeError(f'{root} secret-provider-response credential=private')
    monkeypatch.setattr(service.application.workflow, 'pipeline_with_evidence', fail)
    code, value = request(server, 'GET', '/api/workspaces/book/pipeline')
    assert code == 500 and value == {'error': {'code': 'internal_error', 'message': 'Internal server error.', 'details': {}}}


def test_import_disabled_and_discovery(api):
    _, _, service, server = api
    assert request(server, 'GET', '/api/capabilities')[1]['import_enabled']
    assert request(server, 'GET', '/api/import-sources')[1] == {'sources': [{'source_id': 'book'}]}
    service.imports.root = None
    assert not request(server, 'GET', '/api/capabilities')[1]['import_enabled']
    assert request(server, 'POST', '/api/imports', {'workspace_id': 'new', 'source_id': 'book'})[1]['error']['code'] == 'import_disabled'
    assert request(server, 'GET', '/api/import-sources')[0] == 403


@pytest.mark.parametrize('payload', [
    {'workspace_id': '../escape'}, {'workspace_id': '/absolute'}, {'workspace_id': 'nested/name'},
    {'source_id': '../book'}, {'source_id': '/etc'}, {'source_id': 'book/../book'},
    {'source_id': 'book\\escape'}, {'opf': '../secret.opf'}, {'opf': '/secret.opf'},
    {'previous_volume': '../book'}, {'include_glob': '../*'}, {'model': '/private/model'},
    {'pass_profiles': []}, {'context_size': True}, {'sidecar_txt': 'yes'}, {'host': 'evil.invalid'},
])
def test_import_rejects_unsafe_inputs(api, payload):
    assert request(api[3], 'POST', '/api/imports', {'workspace_id': 'new', 'source_id': 'book', **payload})[0] == 400


def test_import_escaping_symlinks_and_destination_collision(api, tmp_path):
    _, root, service, server = api
    (service.imports.root / 'escape').symlink_to(tmp_path)
    assert request(server, 'POST', '/api/imports', {'workspace_id': 'new', 'source_id': 'escape'})[0] == 400
    (service.imports.root / 'book' / 'evil.html').symlink_to(tmp_path / 'secret')
    assert request(server, 'POST', '/api/imports', {'workspace_id': 'new', 'source_id': 'book'})[0] == 400
    (service.imports.root / 'book' / 'evil.html').unlink()
    assert request(server, 'POST', '/api/imports', {'workspace_id': 'book', 'source_id': 'book'})[1]['error']['code'] == 'destination_exists'
    (root.parent / 'alias').symlink_to(tmp_path)
    assert request(server, 'POST', '/api/imports', {'workspace_id': 'alias', 'source_id': 'book'})[0] == 400


def test_import_worker_reuses_application_and_discovery(api):
    _, root, service, _ = api
    spec = ImportJobSpec(str(root.parent), 'new', str(root.parent / 'new'), str(service.imports.root), 'book',
                         chapter_mode='file', profile='local', pass_profiles={'1': 'local'}, whole_section_limit=9000)
    stream = io.StringIO()
    assert execute(spec, JsonlProgressSink(stream), lambda sink: create_application(sink, provider_factory=LocalImportPool)) == 0
    assert 'new' in service.workspaces.list()
    assert service.pipeline('new')['stage'] == 'analysis'
    assert 'import_files_progress' in stream.getvalue()
    assert '"status":"succeeded"' in stream.getvalue()
    assert str(service.imports.root) not in stream.getvalue()


def test_import_disconnect_history_sse_and_conflicts(api):
    _, root, service, server = api
    payload = {'workspace_id': 'new', 'source_id': 'book'}
    code, job = request(server, 'POST', '/api/imports', payload)
    assert code == 202
    job = service.supervisor.get(job['job_id'])
    running(service.supervisor, job)
    assert request(server, 'POST', '/api/imports', payload)[0] == 409
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request('GET', '/api/events?workspace_id=new', headers={'Last-Event-ID': '0'})
    response = connection.getresponse()
    assert response.status == 200 and read_sse(response)['event'] == 'snapshot'
    assert read_sse(response)['event'] == 'progress'
    response.close(); connection.close()
    assert service.supervisor.get(job.job_id).state == 'running'
    service.supervisor.stop(job.job_id)
    assert terminal(service.supervisor, job).state == 'cancelled'
    assert service.supervisor.registry.events(0, workspace_root=str(root.parent), job_id=job.job_id)


def test_import_atomic_concurrent_requests(api):
    def start(_):
        return request(api[3], 'POST', '/api/imports', {'workspace_id': 'new', 'source_id': 'book'})[0]
    with ThreadPoolExecutor(max_workers=5) as pool:
        codes = list(pool.map(start, range(5)))
    assert codes.count(202) == 1 and codes.count(409) == 4


def test_real_import_to_publication_through_one_server(api, provider_server):
    """Only setup supplies a configured predecessor; all new-volume work uses HTTP."""
    from bookpipe.application.commands import AnalyzeCommand
    from bookpipe.runtime.supervisor import worker_command
    import shutil
    _, root, service, server = api
    provider_state, port = provider_server
    source = make_epub_source(service.imports.root)
    previous = root.parent / 'previous'
    app = create_application()
    app.projects.import_book(ImportBookCommand(previous, source, host='127.0.0.1', port=port))
    app.pipeline.analyze(AnalyzeCommand(previous))
    draft = app.review.prepare(previous)
    repository = app.review.repository(previous)
    bulk = repository.review_terms([t['id'] for t in draft['terms']], draft['_revision'])
    confirmation = repository.set_confirmed(True, bulk['revision'])
    app.review.approve(ApproveCommand(previous, expected_revision=confirmation['revision']))
    incoming = service.imports.root / 'next-volume'
    shutil.copytree(source, incoming)
    text = incoming / 'EPUB' / 'text' / 'one.xhtml'
    text.write_text(text.read_text().replace('Relay', 'Relay Relay'))
    service.supervisor.command_factory = worker_command
    code, job = request(server, 'POST', '/api/imports', {
        'workspace_id': 'next', 'source_id': 'next-volume', 'previous_volume': 'previous',
        'opf': 'EPUB/package.opf', 'profile': 'local', 'input_encoding': 'utf-8',
        'chapter_mode': 'file', 'include_glob': '*.xhtml', 'whole_section_limit': 10000,
    })
    assert code == 202
    result = terminal(service.supervisor, service.supervisor.get(job['job_id']))
    assert result.state == 'succeeded', result
    assert 'next' in [row['workspace_id'] for row in request(server, 'GET', '/api/workspaces')[1]['workspaces']]
    base = '/api/workspaces/next'
    def run(operation, **options):
        code, job = request(server, 'POST', base + '/jobs', {'operation': operation, **options})
        assert code == 202
        result = terminal(service.supervisor, service.supervisor.get(job['job_id']))
        assert result.state == 'succeeded', result
        return request(server, 'GET', base + '/pipeline')[1]
    assert run('analyze')['stage'] == 'review'
    draft = request(server, 'POST', base + '/review/prepare', {})[1]
    bulk = request(server, 'POST', base + '/review/bulk-review', {'revision': draft['_revision'], 'term_ids': [t['id'] for t in draft['terms']]})[1]
    confirmed = request(server, 'POST', base + '/review/confirmation', {'revision': bulk['revision'], 'confirmed': True})[1]
    assert request(server, 'POST', base + '/approve', {'revision': confirmed['revision']})[0] == 200
    assert not run('translate', chunk_limit=1)['translation_complete']
    complete = run('translate')
    assert complete['stage'] == 'complete' and complete['publication']['state'] == 'published'
    assert complete['publication']['current']
    calls = dict(provider_state.calls)
    assert run('publish')['publication']['current']
    assert dict(provider_state.calls) == calls
    assert request(server, 'GET', base + '/reader/progress')[1]['total_words'] > 0
    assert request(server, 'GET', base + '/usage')[1]['units']
    assert request(server, 'GET', base + '/settings')[1]['source'] == 'project'
    assert str(root.parent.parent) not in json.dumps(complete)


def test_marker_concurrent_mutations_commit_once(api):
    _, root, service, server = api
    analyze(root); approve(service); translate(root)
    chapter = service.reader('book', 'chapter', identifier='ch0001')
    revision = service.reader('book', 'markers')['_revision']
    payload = {'revision': revision, 'chapter_id': 'ch0001', 'block_id': chapter['blocks'][0]['id'],
               'start': 0, 'end': 3, 'text': 'Ada'}
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: request(server, 'POST', '/api/workspaces/book/reader/markers', payload)[0], range(5)))
    assert results.count(200) == 1 and results.count(409) == 4


def test_pipeline_reports_failed_publication_and_retry(api):
    _, root, service, _ = api
    analyze(root); approve(service); translate(root)
    atomic_json(root / 'publication.json', {'format_version': 1, 'last_attempt': {
        'status': 'failed', 'publication_fingerprint': None, 'target_language': 'pl',
        'error': str(root / 'private-file'),
    }})
    value = service.pipeline('book')
    assert value['publication']['state'] == 'failed' and value['actions']['publish']['allowed']
    assert str(root) not in json.dumps(value)


def test_approval_revision_checked_before_any_authoritative_write(api):
    app, root, service, _ = api
    analyze(root)
    draft = service.review('book', 'prepare', {})
    current = service.review('book', 'bulk', {'revision': draft['_revision'], 'term_ids': ['T000001']})
    current = service.review('book', 'confirmation', {'revision': current['revision'], 'confirmed': True})
    before = (root / 'terms.review.json').read_bytes()
    with pytest.raises(ReviewConflict):
        app.review.approve(ApproveCommand(root, expected_revision=draft['_revision']))
    assert (root / 'terms.review.json').read_bytes() == before
    assert not (root / 'lexicon.approved.json').exists()
    assert not service.pipeline('book')['approved']
    with project_lock(root), pytest.raises(LockConflict):
        app.review.approve(ApproveCommand(root, expected_revision=current['revision']))


def test_legacy_settings_query_does_not_migrate(api):
    _, root, service, _ = api
    path = root / 'settings.json'
    legacy = read_json(path)
    for key in ('profiles', 'default_profile', 'pass_profiles', 'format_version'):
        legacy.pop(key, None)
    atomic_json(path, legacy)
    before = path.read_bytes()
    assert service.settings('book')['default_profile'] == 'local'
    assert path.read_bytes() == before and not (root / 'backups').exists()


def test_new_serializers_discard_private_fields_and_legacy_errors(api):
    from bookpipe.runtime.models import Job
    from bookpipe.server.serialization import review, reader
    _, root, service, server = api
    secret = 'private-provider-response'
    analyze(root)
    draft = service.review('book', 'prepare', {})
    draft['internal_path'] = str(root)
    draft['terms'][0]['provider_response'] = secret
    draft['terms'][0]['candidates'][0]['authorization'] = secret
    assert secret not in json.dumps(review(draft))
    assert 'internal_path' not in review(draft)
    assert reader({'book_fingerprint': 'x', 'title': 'x', 'chapters': [], 'project': str(root)}, 'metadata') == {
        'book_fingerprint': 'x', 'title': 'x', 'chapters': []}
    job = Job('legacy-private', str(root.parent), root.name, str(root), 'import', state='failed',
              error={'type': 'RuntimeError', 'message': secret, 'provider_body': secret})
    service.supervisor.registry.save(job)
    original = service.supervisor.registry.append(job.job_id, {'kind': 'publication_failed', 'message': secret,
          'values': {'error': secret, 'prompt': secret, 'output_path': str(root), 'target_language': 'pl'}})
    response = request(server, 'GET', '/api/jobs/legacy-private')[1]
    assert secret not in json.dumps(response) and str(root) not in json.dumps(response)
    assert service.supervisor.registry.get(job.job_id).last_event == original


def test_import_cli_configuration_and_global_profiles_before_first_workspace(api, tmp_path):
    from bookpipe.cli import parser
    args = parser().parse_args(['serve', '--workspace-root', str(tmp_path), '--import-root', str(tmp_path)])
    assert args.import_root == tmp_path
    response = request(api[3], 'GET', '/api/profiles')
    assert response[0] == 200 and response[1]['source'] == 'defaults' and response[1]['profiles']


@pytest.mark.parametrize('raw', ['{"revision":"x","revision":"y"}', '{"position":NaN}', '{"position":Infinity}'])
def test_nonstandard_json_rejected(api, raw):
    connection = http.client.HTTPConnection(*api[3].server_address, timeout=3)
    connection.request('POST', '/api/workspaces/book/reader/context', raw, {'Content-Type': 'application/json'})
    response = connection.getresponse()
    assert response.status == 400
    assert json.loads(response.read())['error']['code'] == 'invalid_request'
    connection.close()


def test_excluded_sections_remain_visible_without_translation_units(api):
    app, root, service, _ = api
    source = service.imports.root / 'with-frontmatter'
    source.mkdir()
    (source / '01.html').write_text('<h1>Chapter One</h1><p>Ada went outside.</p>')
    (source / '02.html').write_text('<h1>About the Author</h1><p>A short author biography.</p>')
    app.projects.import_book(ImportBookCommand(root.parent / 'frontmatter', source, chapter_mode='headings'))
    value = service.pipeline('frontmatter')
    assert value['excluded_sections'] and value['excluded_sections'][0]['role'] != 'narrative'
    assert value['excluded_sections'][0]['id'] not in [u['chapter_id'] for u in value['units']]


def test_sse_preserves_existing_in_root_workspace_aliases(api):
    _, root, service, server = api
    (root.parent / 'alias').symlink_to(root)
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request('GET', '/api/events?workspace_id=alias')
    response = connection.getresponse()
    assert response.status == 200 and read_sse(response)['event'] == 'snapshot'
    response.close(); connection.close()


def test_request_scoped_services_preserve_frozen_manifest_validation(api):
    _, root, service, server = api
    analyze(root); service.review('book', 'prepare', {})
    book = read_json(root / 'book.json')
    book['chapters'][0]['blocks'][0]['text'] = 'Unexpected source change'
    atomic_json(root / 'book.json', book)
    for suffix in ('review', 'reader', 'reader/markers', 'reader/chapters/ch0001'):
        assert request(server, 'GET', '/api/workspaces/book/' + suffix)[0] == 422
    assert request(server, 'DELETE', '/api/workspaces/book/reader/markers/M000001', {'revision': 'stale'})[0] == 422
