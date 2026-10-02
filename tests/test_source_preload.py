"""P0 web read isolation and native loopback command acceptance (no cloud)."""
import copy
from pathlib import Path

import pytest

from bookpipe.application.commands import ImportBookCommand, PreloadCommand
from bookpipe.application.source_preload import LocalCodexCounter
from bookpipe.bootstrap import create_application
from bookpipe.engine import analysis_plan
from bookpipe.processing import effective_book, ConfigConflict
from bookpipe.source_sessions import DEFAULT_EXECUTION, PersistentSourceSessionManager
from bookpipe.store import Store
from bookpipe.util import atomic_json, read_json, PipelineError
from test_application import LocalImportPool
from test_source_sessions import native_mock
from test_server_api import api
from test_runtime import request


@pytest.fixture
def preload_book(tmp_path):
    return make_preload_book(tmp_path)


def make_preload_book(tmp_path, paragraphs=1):
    source, root = tmp_path / 'source', tmp_path / 'workspaces/book'
    source.mkdir()
    for n in range(1, 3):
        text = ''.join(f'<p>Zażółć gęślą jaźń {n}.{i}. &lt;script&gt;escaped source&lt;/script&gt;</p>' for i in range(paragraphs))
        (source / f'chapter{n}.html').write_text(f'<html lang="pl"><h1>Chapter {n}</h1>{text}</html>')
    create_application(provider_factory=LocalImportPool).projects.import_book(
        ImportBookCommand(root, source, chapter_mode='file', local_token_estimate=True))
    settings = read_json(root / 'settings.json')
    settings['profiles']['preload-test'] = {'provider': 'codex', 'model': 'gpt-5.6-sol',
        'context_size': 200000, 'reasoning_effort': 'medium', 'runtime_root': str(tmp_path / 'native-private'),
        'options': {'late_usage_wait': .02}, 'request_timeout': 20}
    settings['default_profile'] = 'preload-test'
    settings['pass_profiles'] = {}
    settings['pipeline_execution'] = copy.deepcopy(DEFAULT_EXECUTION)
    atomic_json(root / 'settings.json', settings)
    return create_application(), root, settings


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def target(value, chapter=0):
    return value['chapters'][chapter]['targets'][0]


def test_pristine_reads_are_pure_and_equivalent_to_execution(preload_book, monkeypatch):
    app, root, settings = preload_book
    before = files(root)
    def forbidden(*args, **kwargs):
        raise AssertionError('GET attempted provider/native/mutation work')
    monkeypatch.setattr(PersistentSourceSessionManager, '__init__', forbidden)
    monkeypatch.setattr('bookpipe.provider_registry.ProviderPool.__init__', forbidden)
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('shutil.which', forbidden)
    for _ in range(2):
        value = app.source_preload.inventory(root)
        assert value['planning_state'] == 'prospective'
        assert value['summary']['required'] == 2  # one exact source shared by all five passes
        assert value['summary']['state'] == 'pending'
        assert value['views']['analysis']['required'] == 2
        assert value['views']['translation']['required'] == 2
        assert len(target(value)['consumers']) == 5
        assert target(value)['slot_id'] is None
        preview = app.source_preload.preview(root, target(value)['target_id'])
        assert not preview['available'] and preview['source_kind'] == 'planned'
        assert 'Zażółć' in ' '.join(b['text'] for b in preview['source'])
        assert target(value)['label'] == 'Whole chapter'
        assert target(value)['source_words'] == sum(len(b['text'].split()) for b in preview['source'])
        assert target(value)['source_utf8_bytes'] == sum(len(b['text'].encode('utf-8')) for b in preview['source'])
        assert target(value)['source_blocks'] == len(preview['source'])
        pipeline = app.workflow.pipeline(root)
        assert pipeline['source_preload']['required'] == 2
    assert files(root) == before
    assert not (root / 'analysis_plan.json').exists()
    store = Store(root)
    book = effective_book(read_json(root / 'book.json'), root, phase='analysis')
    prospective = app.source_preload.build(root, store)[1]
    plan = analysis_plan(store, book, LocalCodexCounter(settings['profiles']['preload-test']), settings)
    assert {binding[2]['id'] for binding in prospective.values() if binding[3] == 1} == {u['id'] for u in plan}
    saved = app.source_preload.inventory(root)
    assert saved['planning_state'] == 'saved'
    assert [t['target_id'] for c in value['chapters'] for t in c['targets']] == [t['target_id'] for c in saved['chapters'] for t in c['targets']]
    store.close()


def test_aliases_efforts_eligibility_and_legacy(preload_book):
    app, root, settings = preload_book
    settings['profiles']['alias'] = copy.deepcopy(settings['profiles']['preload-test'])
    settings['pass_profiles'] = {'2': 'alias'}
    atomic_json(root / 'settings.json', settings)
    value = app.source_preload.inventory(root)
    assert value['summary']['required'] == 2
    assert target(value)['profiles'] == ['preload-test', 'alias']
    settings['profiles']['alias']['reasoning_effort'] = 'high'
    atomic_json(root / 'settings.json', settings)
    assert app.source_preload.inventory(root)['summary']['required'] == 4
    atomic_json(root / 'web.config.json', {'pass_profiles': {}, 'sections': {'ch0001': {'processing': 'translate'}, 'ch0002': {'processing': 'excluded'}}})
    value = app.source_preload.inventory(root)
    assert value['summary']['required'] == 2
    assert all(c['pass_no'] > 1 for t in value['chapters'][0]['targets'] for c in t['consumers'])
    assert value['chapters'][1]['reason'] == 'Excluded from processing.'
    settings.pop('pipeline_execution')
    atomic_json(root / 'settings.json', settings)
    assert app.source_preload.inventory(root)['summary']['state'] == 'not_applicable'


@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('separate_translation', [False, True])
def test_whole_book_analysis_barrier_and_source_preload_order(preload_book, native_mock, monkeypatch, manual, separate_translation):
    """Installed native runtime, fourteen loopback turns maximum; no cloud/auth."""
    import json
    from bookpipe import source_session_codec as codec
    from bookpipe.application.commands import AnalyzeCommand, ApproveCommand, TranslateCommand
    from bookpipe.engine import Runner

    app, root, settings = preload_book
    if separate_translation:
        settings['profiles']['translation-test'] = {**settings['profiles']['preload-test'], 'reasoning_effort': 'high'}
        settings['pass_profiles'] = {str(n): 'translation-test' for n in range(2, 6)}
        atomic_json(root / 'settings.json', settings)
    inventory = app.source_preload.inventory(root)
    chapters = inventory['chapters']
    units = [c['unit_id'] for chapter in chapters for t in chapter['targets'] for c in t['consumers'] if c['pass_no'] == 1]
    source_chapters = {t['scope_id']: c['chapter_id'] for c in chapters for t in c['targets']}
    actual_inputs = {}
    original = Runner.run

    def capture(self, number, key, inputs, **kwargs):
        actual_inputs[number] = inputs
        return original(self, number, key, inputs, **kwargs)

    def output(envelope):
        n = envelope['ACTIVE_PASS']
        if n == 0:
            return copy.deepcopy(codec.READY)
        inputs = actual_inputs[n]
        if n == 1:
            value = {'terms': [], 'observations': []}
        elif n == 2:
            value = {'checks': [{'sid': s['id'], 'risk': 'low'} for s in inputs['SOURCE_SENTENCES']], 'issues': []}
        elif n == 3:
            value = {'translations': [{'id': b['id'], 'text': 'Translated: ' + b['text']} for b in inputs['SOURCE_BLOCKS']]}
        elif n == 4:
            value = {'checks': [{'sid': s['id'], 'status': 'ok'} for s in inputs['SOURCE_SENTENCES']], 'corrections': []}
        else:
            value = inputs['POLISH_DRAFT']
        return codec.encode_output(value, n, codec.context_for(inputs, n))

    def turns():
        result = []
        for body in native_mock['calls']:
            user = [item for item in body['input'] if item.get('role') == 'user'][-1]
            envelope = json.loads(user['content'][0]['text'])
            result.append((source_chapters[envelope['SOURCE_REF']['scope_id']], envelope['ACTIVE_PASS']))
        return result

    monkeypatch.setattr(Runner, 'run', capture)
    native_mock['output_factory'] = output
    if manual:
        later = target(inventory, 1)
        app.source_preload.run(PreloadCommand(root, later['target_id'], inventory['intent_revision']), app.pipeline.progress)
        with pytest.raises(PipelineError, match='earlier P1 units'):
            app.pipeline.analyze(AnalyzeCommand(root, unit_id=units[1]))
        assert turns() == [('ch0002', 0)]
        app.pipeline.analyze(AnalyzeCommand(root, unit_id=units[0]))
        assert turns() == [('ch0002', 0), ('ch0001', 0), ('ch0001', 1)]
        # A stale approval flag must not permit any targeted translation stage.
        store = Store(root)
        store.set('approved', True)
        store.close()
        before = len(native_mock['calls'])
        chunk = read_json(root / 'book.json')['chunks'][0]['id']
        for n in range(2, 6):
            with pytest.raises(PipelineError, match='analyze to completion'):
                app.pipeline.translate(TranslateCommand(root, chunk_id=chunk, pass_no=n))
        assert len(native_mock['calls']) == before
        store = Store(root)
        store.set('approved', False)
        store.close()
        app.pipeline.analyze(AnalyzeCommand(root, unit_id=units[1]))
        expected = [('ch0002', 0), ('ch0001', 0), ('ch0001', 1), ('ch0002', 1)]
    else:
        app.pipeline.analyze(AnalyzeCommand(root))
        expected = [('ch0001', 0), ('ch0001', 1), ('ch0002', 0), ('ch0002', 1)]
    assert turns() == expected
    if separate_translation:
        # Pending translation configurations are intentional and must not be
        # mistaken for skipped earlier analysis chapters in the P0 inventory.
        for chapter in app.source_preload.inventory(root)['chapters']:
            analysis, translation = chapter['targets']
            assert analysis['baseline_state'] == 'accepted'
            assert translation['baseline_state'] == 'pending'
            assert all(c['pass_no'] > 1 for c in translation['consumers'])
        sections = app.web.section_summaries(root, app.workflow.pipeline(root))
        assert all(s['passes']['0']['state'] == 'completed' for s in sections)
        assert all(s['passes']['0']['completed'] == s['passes']['0']['required'] == 1 for s in sections)
    with pytest.raises(PipelineError, match='approve'):
        app.pipeline.translate(TranslateCommand(root))
    assert turns() == expected
    app.review.approve(ApproveCommand(root), confirm_review=True)
    app.pipeline.translate(TranslateCommand(root))
    assert turns() == expected + [(chapter, n) for chapter in ('ch0001', 'ch0002')
                                 for n in ([0, 2, 3, 4, 5] if separate_translation else range(2, 6))]
    assert len(native_mock['calls']) == (14 if separate_translation else 12)
    # Preloaded P0 is reused by later consumers; cache measurement stays factual.
    from bookpipe.usage import usage_by_unit_report
    groups = [p for u in usage_by_unit_report(root).units for p in u.passes]
    assert sum(p.physical_attempt_count for p in groups if p.pass_no == 0) == (4 if separate_translation else 2)
    assert all(p.cached_input_tokens.value == 0 for p in groups)
    assert app.source_preload.inventory(root)['summary']['accepted'] == (4 if separate_translation else 2)


def test_native_target_only_reuse_selection_receipt_and_held_state(preload_book, native_mock):
    app, root, settings = preload_book
    value = app.source_preload.inventory(root)
    chosen = target(value, 1)  # no dependency on earlier P0/P1
    command = PreloadCommand(root, chosen['target_id'], value['intent_revision'])
    app.source_preload.run(command, app.pipeline.progress)
    assert len(native_mock['calls']) == 1
    assert not (root / 'analysis_plan.json').exists()
    assert not (root / 'analysis_inputs').exists()
    store = Store(root)
    assert not store.get('analysis_done') and not store.get('approved')
    from bookpipe.infrastructure.read_store import ReadStore
    reader = ReadStore(root)
    assert all(key.startswith('pass0/') for key in reader.checkpoint_inventory())
    reader.close()
    store.close()
    value = app.source_preload.inventory(root)
    chosen = target(value, 1)
    assert chosen['baseline_state'] == 'accepted'
    assert value['summary']['accepted'] == 1
    preview = app.source_preload.preview(root, chosen['target_id'])
    assert preview['available'] and preview['session']['reported_model'] == 'gpt-5.6-sol'
    app.source_preload.run(PreloadCommand(root, chosen['target_id'], value['intent_revision']), app.pipeline.progress)
    assert len(native_mock['calls']) == 1
    from bookpipe.usage import usage_by_unit_report
    ledger = usage_by_unit_report(root)
    groups = [p for u in ledger.units for p in u.passes if p.pass_no == 0]
    assert len(groups) == 1 and groups[0].physical_attempt_count == groups[0].provider_call_count == 1
    assert groups[0].attempts[0].selection_status == 'verified'
    assert groups[0].input_tokens.value == 100 and groups[0].output_tokens.value == 5
    from bookpipe.server.serialization import usage as public_usage
    exposed = public_usage(groups[0])
    assert exposed['attempts'][0]['attempt_id'].startswith('pa_')
    assert exposed['accepted_attempt_id'] == exposed['attempts'][0]['attempt_id']
    assert 'artifacts/' not in str(exposed)
    path = next(root.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    record = read_json(path)
    record.update(state='accepted_cleanup_pending', cleanup_required=True)
    atomic_json(path, record)
    assert target(app.source_preload.inventory(root), 1)['baseline_state'] == 'accepted'
    receipt = root / record['p0_attempt'] / 'selection.verification.json'
    receipt.unlink()
    before = files(root)
    assert target(app.source_preload.inventory(root), 1)['baseline_state'] == 'unverifiable'
    assert files(root) == before and not receipt.exists()
    assert usage_by_unit_report(root).units[0].passes[0].attempts[0].selection_status is None
    assert len(native_mock['calls']) == 1


@pytest.mark.parametrize('field,value', [('reasoning_effort', 'high'), ('model', 'gpt-6.1-sol')])
def test_stale_intent_fails_before_native_work(preload_book, monkeypatch, field, value):
    app, root, settings = preload_book
    inventory = app.source_preload.inventory(root)
    settings['profiles']['preload-test'][field] = value
    atomic_json(root / 'settings.json', settings)
    monkeypatch.setattr(PersistentSourceSessionManager, '__init__', lambda *args: pytest.fail('constructed native manager'))
    with pytest.raises(ConfigConflict):
        app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)


@pytest.mark.parametrize('page', [-1, True, 10001, '0'])
def test_preview_rejects_pages_and_paths(preload_book, page):
    app, root, _ = preload_book
    value = app.source_preload.inventory(root)
    with pytest.raises(ValueError):
        app.source_preload.preview(root, target(value)['target_id'], page)
    with pytest.raises(ValueError):
        app.source_preload.preview(root, '../auth.json')


def test_unicode_split_plan_and_saved_authority(preload_book):
    from bookpipe.engine import calculate_analysis_plan
    app, root, settings = preload_book
    book = read_json(root / 'book.json')
    book['chapters'][0]['blocks'][0]['text'] = ('Zażółć gęślą. 日本語文。🙂 ' * 400)
    book['chapters'][0]['source_tokens_quality'] = 'estimated'
    settings['analysis_source_limit'] = 1200
    counter = LocalCodexCounter(settings['profiles']['preload-test'])
    expected = calculate_analysis_plan(book, counter, settings)
    store = Store(root)
    assert analysis_plan(store, book, counter, settings) == expected
    assert read_json(root / 'analysis_plan.json') == expected
    assert any('.a' in b['id'] and 'start' in b for u in expected for b in u['blocks'])
    settings['analysis_source_limit'] = 2000
    assert analysis_plan(store, book, counter, settings) == expected
    store.close()


def test_unresolved_nonlocal_planner_and_known_translation_targets(preload_book):
    app, root, settings = preload_book
    settings['default_profile'] = 'local'
    settings['pass_profiles'] = {'2': 'preload-test'}
    atomic_json(root / 'settings.json', settings)
    atomic_json(root / 'web.config.json', {'pass_profiles': {}, 'sections': {'ch0001': {'profiles': {'1': 'preload-test'}}}})
    value = app.source_preload.inventory(root)
    assert value['planning_state'] == 'unresolved'
    assert value['summary']['unresolved'] == 1
    assert value['views']['analysis']['unresolved'] == 1
    assert value['views']['translation']['unresolved'] == 0
    assert value['summary']['required'] == 2
    unresolved = next(t for t in value['chapters'][0]['targets'] if t['planning_state'] == 'unresolved')
    assert unresolved['scope_id'] is None and not unresolved['can_run']
    assert app.source_preload.preview(root, unresolved['target_id'])['source']
    assert not (root / 'analysis_plan.json').exists()


def test_completed_consumers_keep_accepted_coverage_and_do_not_create_unnecessary_p0(preload_book, native_mock, monkeypatch):
    app, root, settings = preload_book
    initial = app.source_preload.inventory(root)
    chosen = target(initial)
    app.source_preload.run(PreloadCommand(root, chosen['target_id'], initial['intent_revision']), app.pipeline.progress)
    # Saved consumer fixtures use genuine Store checkpoints. The read projection
    # shares the existing translation checkpoint evaluator; only its outcome is
    # supplied here to isolate current-vs-historical coverage from pass codecs.
    monkeypatch.setattr('bookpipe.application.source_preload.translation_checkpoints', lambda *args: {str(n): 'completed' for n in range(2,6)})
    store = Store(root)
    value, bindings = app.source_preload.build(root, store)
    with store.db:
        for source, profile, unit, number, record in bindings.values():
            path = root / f'test-saved-{unit["id"]}.json'
            atomic_json(path, {'terms': [], 'observations': []})
            store.save_job('pass1/' + unit['id'], 'saved-fixture', path, {})
            store.set('analysis:' + unit['id'], {'key': 'pass1/' + unit['id'], 'fingerprint': 'saved-fixture'})
    store.close()
    value = app.source_preload.inventory(root)
    assert value['summary']['required'] == value['summary']['accepted'] == 1
    assert value['summary']['state'] == 'completed'
    assert target(value, 1)['relevance'] == 'satisfied' and not target(value, 1)['can_run']
    assert len(native_mock['calls']) == 1


@pytest.mark.parametrize('damage', ['receipt_missing', 'receipt_wrong_binding', 'receipt_type', 'metadata_model', 'metadata_effort', 'source_wrong', 'source_symlink', 'manifest_corrupt', 'manifest_submissions', 'history_wrong', 'history_type', 'generation_wrong'])
def test_evidence_damage_is_localized_and_never_green(preload_book, native_mock, damage, tmp_path):
    app, root, settings = preload_book
    initial = app.source_preload.inventory(root)
    chosen = target(initial)
    app.source_preload.run(PreloadCommand(root, chosen['target_id'], initial['intent_revision']), app.pipeline.progress)
    path = next(root.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    record = read_json(path)
    receipt = root / record['p0_attempt'] / 'selection.verification.json'
    source = path.parents[2] / 'source.json'
    if damage == 'receipt_missing':
        receipt.unlink()
    elif damage == 'receipt_type':
        atomic_json(receipt, [])
    elif damage == 'receipt_wrong_binding':
        saved = read_json(receipt); saved['binding']['requested_effort'] = 'high'; atomic_json(receipt, saved)
    elif damage.startswith('metadata_'):
        metadata = root / record['p0_attempt'] / 'response_meta.json'
        saved = read_json(metadata)
        saved['reported_' + damage.removeprefix('metadata_')] = 'different'
        atomic_json(metadata, saved)
    elif damage == 'source_wrong':
        package = read_json(source); package['canonical_blocks'][0]['text'] = 'Foreign chapter'; atomic_json(source, package)
    elif damage == 'source_symlink':
        foreign = tmp_path / 'private-source.json'; foreign.write_bytes(source.read_bytes()); source.unlink(); source.symlink_to(foreign)
    elif damage == 'manifest_corrupt':
        path.write_text('{broken')
    elif damage == 'manifest_submissions':
        record['submissions'] = [None]; atomic_json(path, record)
    elif damage == 'history_type':
        atomic_json(root / record['p0_attempt'] / 'native.history.json', {'wrong': True})
    elif damage == 'generation_wrong':
        record['generation'] = 2; atomic_json(path, record)
    else:
        saved = read_json(root / record['p0_attempt'] / 'native.history.json'); saved[0]['status'] = 'interrupted'; atomic_json(root / record['p0_attempt'] / 'native.history.json', saved)
    before = files(root)
    value = app.source_preload.inventory(root)
    assert target(value)['baseline_state'] == 'unverifiable' and not target(value)['can_run']
    assert target(value, 1)['baseline_state'] == 'pending'
    preview = app.source_preload.preview(root, chosen['target_id'])
    if damage.startswith('source_'):
        assert not preview['source'] and preview['reason']
    assert files(root) == before and len(native_mock['calls']) == 1
    if damage.startswith('metadata_'):
        from bookpipe.usage import usage_by_unit_report
        assert usage_by_unit_report(root).units[0].passes[0].attempts[0].selection_status is None


def test_preload_worker_recovers_completed_p0_without_another_turn(preload_book, native_mock, monkeypatch):
    import io
    from bookpipe.runtime.models import JobSpec
    from bookpipe.runtime.worker import execute
    from bookpipe.runtime.protocol import JsonlProgressSink
    app, root, _ = preload_book
    initial = app.source_preload.inventory(root)
    submit = PersistentSourceSessionManager.submit
    def lose_after_completion(self, *args):
        submit(self, *args)
        raise KeyboardInterrupt
    monkeypatch.setattr(PersistentSourceSessionManager, 'submit', lose_after_completion)
    with pytest.raises(KeyboardInterrupt):
        app.source_preload.run(PreloadCommand(root, target(initial)['target_id'], initial['intent_revision']), app.pipeline.progress)
    monkeypatch.setattr(PersistentSourceSessionManager, 'submit', submit)
    inventory = app.source_preload.inventory(root)
    spec = JobSpec(str(root.parent), root.name, str(root), 'preload', preload_target_id=target(inventory)['target_id'],
                   expected_preload_revision=inventory['intent_revision'])
    output = io.StringIO()
    assert execute(spec, JsonlProgressSink(output), lambda sink: create_application(sink)) == 0
    assert len(native_mock['calls']) == 1
    assert target(app.source_preload.inventory(root))['baseline_state'] == 'accepted'
    assert '"status":"succeeded"' in output.getvalue()
    assert not (root / 'analysis_plan.json').exists()


def test_both_http_adapters_validate_preview_and_idempotent_target_job(api):
    app, root, service, server = api
    settings = read_json(root / 'settings.json')
    settings['default_profile'] = 'codex-sol-medium'
    atomic_json(root / 'settings.json', settings)
    inventory = request(server, 'GET', '/api/workspaces/book/source-preload')[1]
    chosen = target(inventory)
    before = files(root)
    for _ in range(2):
        assert request(server,'GET','/api/workspaces/book/pipeline')[0] == 200
        assert request(server,'GET','/api/workspaces/book/source-preload')[0] == 200
        status, preview = request(server,'GET',f'/api/workspaces/book/source-preload/targets/{chosen["target_id"]}/preview?page=0')
        assert status == 200 and preview['source']
        assert not any(key in str(preview) for key in ('runtime_root', 'session_manifest', 'TASK_INSTRUCTIONS', 'auth_source'))
    assert files(root) == before
    for query in ('page=-1','page=10001','page=1&page=2','page=1&private=1','page=','page=true'):
        assert request(server,'GET',f'/api/workspaces/book/source-preload/targets/{chosen["target_id"]}/preview?{query}')[0] == 400
    assert request(server,'GET','/api/workspaces/book/source-preload/targets/pt_'+'f'*64+'/preview')[0] == 404
    assert request(server,'GET','/api/workspaces/missing/source-preload')[0] == 404
    payload = {'operation':'preload','preload_target_id':chosen['target_id'], 'expected_preload_revision': inventory['intent_revision'],
               'request_key': 'p0-http-fixture-key'}
    status, first = request(server,'POST','/api/workspaces/book/jobs',payload)
    assert status == 202
    assert request(server,'POST','/api/workspaces/book/jobs',payload)[1]['job_id'] == first['job_id']
    assert request(server,'GET','/api/requests/p0-http-fixture-key')[1]['job_id'] == first['job_id']


@pytest.mark.parametrize('change', ['processing', 'source', 'archive', 'generation', 'held'])
def test_worker_refuses_changed_or_held_intent_without_another_turn(preload_book, native_mock, change):
    app, root, settings = preload_book
    inventory = app.source_preload.inventory(root)
    app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)
    before = app.source_preload.inventory(root)
    command = PreloadCommand(root, target(before)['target_id'], before['intent_revision'])
    path = next(root.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    record = read_json(path)
    if change == 'processing':
        atomic_json(root / 'web.config.json', {'pass_profiles': {}, 'sections': {'ch0001': {'processing': 'excluded'}}})
    elif change == 'source':
        (root / 'prompts/pass0.txt').write_text('Different readiness source contract')
    elif change == 'archive':
        atomic_json(root / 'web.lifecycle.json', {'archived': True})
    elif change == 'generation':
        store = Store(root)
        with store.db:
            store.set('source_slot_generation:' + record['compatibility_id'], 2)
        store.close()
    else:
        receipt = root / record['p0_attempt'] / 'selection.verification.json'
        value = read_json(receipt); value['status'] = 'held'; atomic_json(receipt, value)
    with pytest.raises(PipelineError):
        app.source_preload.run(command, app.pipeline.progress)
    assert len(native_mock['calls']) == 1
    assert not (root / 'analysis_plan.json').exists()


def test_legacy_runtime_root_does_not_relocate_source_sessions(preload_book, native_mock, monkeypatch):
    app, root, settings = preload_book
    inventory = app.source_preload.inventory(root)
    app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)
    settings['profiles']['preload-test']['runtime_root'] = str(root.parent.parent / 'other-private-runtime')
    atomic_json(root / 'settings.json', settings)
    monkeypatch.setattr('shutil.which', lambda *args: pytest.fail('Read projection performed executable discovery'))
    current = app.source_preload.inventory(root)
    assert current['summary']['accepted'] == 1 and current['summary']['retained'] == 0
    assert target(current)['baseline_state'] == 'accepted'
    assert target(current)['target_id'] == target(inventory)['target_id']
    assert len(native_mock['calls']) == 1


@pytest.mark.parametrize('missing', [False, True])
def test_damaged_attempt_manifest_does_not_erase_physical_usage(preload_book, native_mock, missing):
    from bookpipe.usage import usage_by_unit_report
    app, root, _ = preload_book
    inventory = app.source_preload.inventory(root)
    app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)
    path = next(root.glob('artifacts/*/sources/*/pass0/*/attempt_*/attempt.json'))
    if missing:
        path.unlink()
    else:
        path.write_text('{bad')
    before = files(root)
    value = app.source_preload.inventory(root)
    assert target(value)['baseline_state'] == 'unverifiable'
    groups = [p for u in usage_by_unit_report(root).units for p in u.passes if p.pass_no == 0]
    assert len(groups) == 1 and groups[0].physical_attempt_count == 1
    assert groups[0].input_tokens.value == 100
    assert groups[0].attempts[0].selection_status is None and not groups[0].attempts[0].accepted_checkpoint
    assert files(root) == before


def test_foreign_checkpoint_is_rejected_before_reading_it(preload_book, native_mock, monkeypatch, tmp_path):
    app, root, _ = preload_book
    inventory = app.source_preload.inventory(root)
    app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)
    foreign = tmp_path / 'private.json'
    foreign.write_text('{"private":true}')
    store = Store(root)
    with store.db:
        store.db.execute('UPDATE jobs SET result_path=? WHERE key LIKE ?', (str(foreign), 'pass0/%'))
    store.close()
    original = Path.read_bytes
    def guarded(path):
        assert path != foreign, 'GET read a foreign checkpoint before rejecting its binding'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    value = app.source_preload.inventory(root)
    assert target(value)['baseline_state'] == 'unverifiable'
    assert target(value, 1)['baseline_state'] == 'pending'


def test_multiple_native_contracts_are_unknown_instead_of_guessing(preload_book, native_mock):
    from bookpipe.util import digest
    app, root, _ = preload_book
    inventory = app.source_preload.inventory(root)
    app.source_preload.run(PreloadCommand(root, target(inventory)['target_id'], inventory['intent_revision']), app.pipeline.progress)
    path = next(root.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    record = read_json(path)
    other = copy.deepcopy(record)
    other['compatibility']['protocol'] = {'cli_version': 'other', 'schema_hashes': {}}
    other['compatibility_id'] = digest(other['compatibility'])
    other['slot_id'] = digest({'compatibility': other['compatibility_id'], 'generation': 1})
    atomic_json(path.parent.parent / other['slot_id'] / 'manifest.json', other)
    current = app.source_preload.inventory(root)
    assert target(current)['baseline_state'] == 'unverifiable' and not target(current)['can_run']
    assert target(current)['slot_id'] is None and current['summary']['retained'] == 2
    assert len(native_mock['calls']) == 1


def test_safe_p0_metadata_preserves_zero_and_rejects_native_paths():
    from bookpipe.progress import ProgressEvent
    from bookpipe.runtime.protocol import progress_value, public_envelope
    values = {'pass_no': 0, 'scope_id': 'scope-1', 'slot_id': 'slot-2', 'physical_record_id': 'physical-3',
              'parent_consumer_pass': 3, 'chapter_id': 'ch0001', 'input_tokens': 0,
              'runtime_root': '/private/native', 'prompt': 'private text'}
    event = progress_value(ProgressEvent(kind='source_preload_started', values=values))
    assert event['values'] == {k: v for k, v in values.items() if k not in {'runtime_root', 'prompt'}}
    public = public_envelope({'event': event})
    assert public['event']['values']['pass_no'] == 0
    event['values'].update(slot_id='/private/native', scope_id='../outside', physical_record_id='/private/attempt', parent_consumer_pass=0)
    assert not {'slot_id', 'scope_id', 'physical_record_id', 'parent_consumer_pass'} & public_envelope({'event': event})['event']['values'].keys()


@pytest.mark.parametrize('operation', ['analyze', 'translate', 'preload'])
def test_runtime_p0_stage_is_correlated_and_cleanup_does_not_light_it(api, operation, monkeypatch):
    from bookpipe.runtime.models import Job
    app, root, service, _ = api
    settings = read_json(root / 'settings.json'); settings['default_profile'] = 'codex-sol-medium'
    atomic_json(root / 'settings.json', settings)
    job = Job('p0-correlation', str(root.parent), root.name, str(root), operation, state='running')
    registry = service.supervisor.registry
    registry.save(job)
    current = service.source_preload('book')
    chosen = target(current)
    chosen['slot_id'] = 'slot-current'
    monkeypatch.setattr(app.source_preload, 'inventory', lambda _: copy.deepcopy(current))
    values = {'pass_no': 0, 'scope_id': chosen['scope_id'], 'slot_id': 'slot-current',
              'chapter_id': chosen['chapter_id'], 'parent_consumer_pass': 1 if operation == 'analyze' else 3}
    registry.append(job.job_id, {'kind': 'source_preload_started', 'values': values})
    pipeline = service.pipeline('book')
    assert pipeline['source_preload']['state'] == 'running'
    assert pipeline['sections'][0]['passes']['0']['runtime_state'] == 'running'
    assert not any('runtime_state' in pipeline['sections'][0]['passes'][str(n)] for n in range(1, 6))
    # Long-running inference evicts the start event from the bounded activity
    # tail; stage status and the exact target must survive both GET and reload.
    for _ in range(125):
        registry.append(job.job_id, {'kind': 'provider_waiting', 'values': {}})
    assert len(registry.recent_events(workspace_root=str(root.parent), workspace_id=root.name, job_id=job.job_id)) == 120
    assert service.pipeline('book')['sections'][0]['passes']['0']['runtime_state'] == 'running'
    inventory = service.source_preload('book')
    assert target(inventory)['baseline_state'] == 'running'
    assert inventory['summary']['running'] == 1 and inventory['summary']['accepted'] == 0
    registry.append(job.job_id, {'kind': 'source_preload_completed', 'values': values})
    pipeline = service.pipeline('book')
    assert pipeline['source_preload']['state'] != 'running'
    assert 'runtime_state' not in pipeline['sections'][0]['passes']['0']
    assert target(service.source_preload('book'))['baseline_state'] == 'pending'  # completion event is not acceptance
    if operation != 'preload':
        assert pipeline['sections'][0]['passes'][str(values['parent_consumer_pass'])]['runtime_state'] == 'running'
    registry.append(job.job_id, {'kind': 'source_session_cleanup_completed', 'values': {**values, 'pass_no': 3}})
    assert service.pipeline('book')['source_preload']['state'] != 'running'
    # A terminal event from a different job is absent from this job's projection.
    other = Job('old-p0', str(root.parent), root.name, str(root), 'preload', state='failed')
    registry.save(other); registry.append(other.job_id, {'kind': 'source_preload_started', 'values': values})
    assert service.pipeline('book')['source_preload']['state'] != 'running'
