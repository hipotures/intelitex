"""Token-free source-session contracts and installed native lifecycle regressions."""
from __future__ import annotations
import copy
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bookpipe import codex_transport, source_session_codec as codec
from bookpipe.contracts import InferenceUnitContext
from bookpipe.engine import Runner
from bookpipe.evidence import EvidenceError
from bookpipe.source_sessions import DEFAULT_EXECUTION, PersistentSourceSessionManager, scope_for
from bookpipe.stages import STAGES
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, read_json
from test_codex_cache import RESULTS, SETTINGS, inputs_for, project
from test_codex_cache_shared_v2 import provider


def configured(root, tmp_path):
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'prompts/pass0.txt', root / 'prompts/pass0.txt')
    client = provider(root)
    client.timeout = 20
    client.settings['runtime_root'] = str(tmp_path / 'private-native')
    client.settings['options']['late_usage_wait'] = .02
    settings = copy.deepcopy(SETTINGS)
    settings['pipeline_execution'] = copy.deepcopy(DEFAULT_EXECUTION)
    settings["json_retries"] = 1
    # Direct fixtures also carry the execution contract in semantic identity.
    client.resolved_profile['pipeline_execution'] = copy.deepcopy(DEFAULT_EXECUTION)
    return client, settings


@pytest.fixture
def native_mock(tmp_path, monkeypatch):
    if not shutil.which('codex'):
        pytest.skip('Installed Codex required; mock provider never spends tokens')
    state = {'calls': [], 'rpc': [], 'invalid': set(), 'failed': set(), 'overrides': {},
             'hold_pass': None, 'arrived': threading.Event(), 'release': threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/responses'
            assert not self.headers.get('Authorization')
            state['calls'].append(body)
            users = [i for i in body['input'] if i.get('type') == 'message' and i.get('role') == 'user']
            envelope = json.loads(users[-1]['content'][0]['text'])
            n = envelope['ACTIVE_PASS']
            if state['hold_pass'] == n:
                state['hold_pass'] = None
                state['arrived'].set()
                state['release'].wait(10)
            output = codec.READY if n == 0 else codec.encode_output(RESULTS[n], n, codec.context_for(inputs_for(n), n))
            if state.get('output_factory'):
                output = state['output_factory'](envelope)
            output = copy.deepcopy(state['overrides'].get(len(state['calls']), output))
            if len(state['calls']) in state['invalid']:
                output['t' if n in (3, 5) else 'c'] = []
            rid = f'response-{len(state["calls"])}'
            item = {'type': 'message', 'id': f'message-{rid}', 'role': 'assistant', 'phase': 'final_answer',
                    'content': [{'type': 'output_text', 'text': json.dumps(output, ensure_ascii=False), 'annotations': []}]}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'status': 'completed', 'output': [item],
                       'usage': {'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105,
                                 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens_details': {'reasoning_tokens': 0}}}}]
            if len(state['calls']) in state['failed']:
                events = [{'type': 'response.failed', 'response': {'id': rid, 'status': 'failed',
                           'error': {'code': 'server_error', 'message': 'injected native failure'}}}]
            data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A genuine interrupted native turn closes its SSE stream.

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    argv = codex_transport.app_server_argv
    monkeypatch.setattr(codex_transport, 'app_server_argv', lambda executable: argv(executable) + [
        '-c', 'model_provider="local_mock"', '-c', 'model_providers.local_mock.name="Local mock"',
        '-c', f'model_providers.local_mock.base_url="http://127.0.0.1:{server.server_port}/v1"',
        '-c', 'model_providers.local_mock.wire_api="responses"', '-c', 'model_providers.local_mock.requires_openai_auth=false',
        '-c', 'model_providers.local_mock.supports_websockets=false', '-c', 'model_providers.local_mock.request_max_retries=0',
        '-c', 'model_providers.local_mock.stream_max_retries=0'])
    request = codex_transport._RpcSession.request
    def observe(rpc, method, params, deadline):
        state['rpc'].append((method, copy.deepcopy(params)))
        assert method not in ('thread/fork', 'thread/rollback', 'thread/inject_items')
        return request(rpc, method, params, deadline)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', observe)
    try:
        yield state
    finally:
        state['release'].set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def run(root, tmp_path, n):
    client, settings = configured(root, tmp_path)
    with_store = Store(root)
    try:
        return Runner(with_store, client, settings, client.ui).run(n, f'pass{n}/unit', inputs_for(n))
    finally:
        client.close()
        with_store.close()


def manifest(root):
    paths = list(root.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    assert len(paths) == 1
    return read_json(paths[0])


def test_graph_and_scope_source_only():
    assert {n: s.dependencies for n, s in STAGES.items()} == {0: (), 1: (), 2: (), 3: (2,), 4: (2, 3), 5: (3, 4)}
    p1, p2 = inputs_for(1), inputs_for(2)
    scope = codec.resolve_scope(p1['SOURCE_BLOCKS'], 'ch1', 'revision')
    assert scope == codec.resolve_scope(p2['SOURCE_BLOCKS'], 'ch1', 'revision')
    for changes in ({'text': 'changed'}, {'id': 'changed'}, {'scene_start': False}):
        blocks = copy.deepcopy(p1['SOURCE_BLOCKS']); blocks[0].update(changes)
        assert codec.resolve_scope(blocks, 'ch1', 'revision').scope_id != scope.scope_id
    wire = json.loads(codec.readiness_message(scope, 'Read and acknowledge only.'))
    assert set(wire['SOURCE']) == {'SOURCE_BLOCKS', 'SOURCE_LOOKUP'}
    assert not any(k in json.dumps(wire) for k in ('EXISTING_MEMORY', 'APPROVED_LEXICON', 'POLISH_DRAFT', 'PREVIOUS_CONTEXT'))


@pytest.mark.parametrize('n', range(1, 6))
def test_semantic_round_trip_and_source_free(n, project):
    inputs = inputs_for(n)
    prompt = (project / 'prompts' / f'pass{n}.txt').read_text()
    scope = codec.resolve_scope(inputs['SOURCE_BLOCKS'], 'ch1', 'revision')
    text, ctx = codec.encode_input(inputs, n, scope, prompt)
    assert codec.decode_input(text, scope, ctx) == inputs
    envelope = json.loads(text)
    assert 'SOURCE' not in envelope and 'SOURCE_BLOCKS' not in envelope['TASK_DATA']
    if n in (2, 4):
        assert all(len(row) == 4 and all(type(x) is int for x in row) for row in envelope['TASK_DATA']['SOURCE_SENTENCES'])
    assert codec.decode_output(codec.encode_output(RESULTS[n], n, ctx), n, ctx) == RESULTS[n]
    retry = {**inputs, 'VALIDATION_ERROR': 'wrong canonical identifier', 'RETRY_INSTRUCTION': 'fix'}
    text, ctx = codec.encode_input(retry, n, scope, prompt)
    assert codec.decode_input(text, scope, ctx) == retry


def test_unicode_repeated_sentences_and_unrepresentable_fail_closed(project):
    inputs = inputs_for(2)
    inputs['SOURCE_BLOCKS'][0]['text'] = '“Żółw.” “Żółw.”\n\n*Emphasis.*'
    bid = inputs['SOURCE_BLOCKS'][0]['id']
    inputs['SOURCE_SENTENCES'] = [{'id': 's1', 'block_id': bid, 'text': '“Żółw.”'}, {'id': 's2', 'block_id': bid, 'text': '“Żółw.”'}]
    scope = codec.resolve_scope(inputs['SOURCE_BLOCKS'], 'ch1', 'r')
    text, ctx = codec.encode_input(inputs, 2, scope, (project / 'prompts/pass2.txt').read_text())
    assert codec.decode_input(text, scope, ctx) == inputs
    refs = json.loads(text)['TASK_DATA']['SOURCE_SENTENCES']
    assert refs[1][2] > refs[0][2]
    inputs['SOURCE_SENTENCES'][1]['text'] = 'unrepresentable'
    with pytest.raises(PipelineError, match='exact P0 source range'):
        codec.encode_input(inputs, 2, scope, (project / 'prompts/pass2.txt').read_text())


@pytest.mark.parametrize('bad', [None, {'p': 0}, {**codec.READY, 'p': 1}, {**codec.READY, 'o': [{}]}, {**codec.READY, 'p': 0.0}])
def test_readiness_shape(bad):
    with pytest.raises(Exception):
        codec.decode_output(bad, 0)


def test_installed_all_passes_one_p0_cold_resume(project, tmp_path, native_mock):
    for n in range(1, 6):
        result, path, fp = run(project, tmp_path, n)
        assert result == RESULTS[n]
        record = manifest(project)
        assert record['p0_status'] == 'ready' and record['state'] == 'baseline_ready'
        assert record['retained_active_turns'] == 1
        assert Path(record['runtime']).is_dir()
    assert len(native_mock['calls']) == 6
    p0_id = manifest(project)['p0_turn_id']
    starts = [p for method, p in native_mock['rpc'] if method == 'thread/start']
    assert len(starts) == 1 and starts[0]['historyMode'] == 'paginated'
    reverts = [p for method, p in native_mock['rpc'] if method == 'thread/revert']
    assert len(reverts) == 5 and all(p['beforeTurnId'] != p0_id for p in reverts)
    for call in native_mock['calls'][1:]:
        users = [i for i in call['input'] if i.get('role') == 'user' and i['content'][0].get('text', '').startswith('{')]
        assert len(users) == 2  # P0 plus exactly one active task
        envelopes = [json.loads(i['content'][0]['text']) for i in users]
        assert envelopes[0]['ACTIVE_PASS'] == 0
        assert 'SOURCE' not in envelopes[1]
        assert call['text']['format']['schema'] == codec.TRANSPORT_SCHEMA
    # Zero cached tokens did not cause any retry.
    run(project, tmp_path, 5)
    assert len(native_mock['calls']) == 6
    from bookpipe.usage import usage_by_unit_report
    report = usage_by_unit_report(project)
    assert any(p.pass_no == 0 for unit in report.units for p in unit.passes)


def test_validation_retry_restores_p0(project, tmp_path, native_mock):
    native_mock['invalid'].add(2)
    result, _, _ = run(project, tmp_path, 3)
    assert result == RESULTS[3]
    assert len(native_mock['calls']) == 3
    assert manifest(project)['retained_active_turns'] == 1
    users = [i for i in native_mock['calls'][-1]['input'] if i.get('role') == 'user' and i['content'][0].get('text', '').startswith('{')]
    assert len(users) == 2
    task = json.loads(users[-1]['content'][0]['text'])
    assert 'VALIDATION_ERROR' in task['TASK_DATA'] and 'SOURCE_BLOCKS' not in task['TASK_DATA']


def test_accepted_then_revert_failure_only_cleans(project, tmp_path, native_mock, monkeypatch):
    request = codex_transport._RpcSession.request
    def fail(rpc, method, params, deadline):
        if method == 'thread/revert':
            raise PipelineError('injected revert failure')
        return request(rpc, method, params, deadline)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', fail)
    with pytest.raises(PipelineError, match='injected revert failure'):
        run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2
    assert manifest(project)['state'] == 'baseline_ready'


def test_result_storage_failure_retains_exact_completed_output(project, tmp_path, native_mock, monkeypatch):
    save = Store.save_job
    def fail(store, key, *args, **kwargs):
        if key.startswith('pass3/'):
            raise EvidenceError('injected storage failure')
        return save(store, key, *args, **kwargs)
    monkeypatch.setattr(Store, 'save_job', fail)
    with pytest.raises(EvidenceError, match='storage failure'):
        run(project, tmp_path, 3)
    assert manifest(project)['state'] == 'terminal_uncommitted'
    assert not any(m == 'thread/revert' for m, p in native_mock['rpc'])
    monkeypatch.setattr(Store, 'save_job', save)
    run(project, tmp_path, 3)
    assert len(native_mock['calls']) == 2
    assert manifest(project)['state'] == 'baseline_ready'


def test_allow_generate_false_and_full_bound(project, tmp_path, monkeypatch):
    client, settings = configured(project, tmp_path)
    store = Store(project)
    try:
        with pytest.raises(PipelineError, match='no current saved result'):
            Runner(store, client, settings, client.ui).run(2, 'pass2/unit', inputs_for(2), allow_generate=False)
        assert not list((tmp_path / 'private-native').glob('*'))
        scope = scope_for(store, 'pass2/unit', inputs_for(2))
        manager = PersistentSourceSessionManager(store, client, scope)
        client.source_manager = manager
        body = client.body((project / 'prompts/pass2.txt').read_text(), inputs_for(2), {}, 2)
        client.context = len(body['input'].encode()) + client.settings['planning_output_reserve'] + 2048
        with pytest.raises(PipelineError, match='conservative request bound'):
            client.preflight(body)
        assert not manager.manifest_path.exists()
    finally:
        store.close()


def test_rpc_late_events_are_not_reused(tmp_path):
    rpc = object.__new__(codex_transport._RpcSession)
    rpc.state = {}; rpc.notifications = []; rpc.progress = None
    rpc.reset_turn(None, 'thread-new'); rpc.bind_turn('turn-new')
    for thread, turn in (('thread-old', 'turn-new'), ('thread-new', 'turn-old')):
        rpc.dispatch({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': 'completed'}}})
        rpc.dispatch({'method': 'thread/tokenUsage/updated', 'params': {'threadId': thread, 'turnId': turn, 'tokenUsage': {'last': {'totalTokens': 123}}}})
    assert rpc.state['terminal'] is None and not rpc.state['usage_events']
    rpc.dispatch({'method': 'turn/completed', 'params': {'threadId': 'thread-new', 'turn': {'id': 'turn-new', 'status': 'completed'}}})
    assert rpc.state['terminal']['id'] == 'turn-new'


@pytest.mark.parametrize('stage', [0, 2])
def test_lost_turn_start_ack_recovers_without_duplicate(project, tmp_path, native_mock, monkeypatch, stage):
    request = codex_transport._RpcSession.request
    lost = False
    def lose(rpc, method, params, deadline):
        nonlocal lost
        result = request(rpc, method, params, deadline)
        if method == 'turn/start' and json.loads(params['input'][0]['text'])['ACTIVE_PASS'] == stage and not lost:
            lost = True
            rpc.bind_turn(result['turn']['id'])
            rpc.drain_until_terminal(deadline)
            rpc.wait_late_usage(.03)
            raise PipelineError('injected lost turn/start acknowledgement')
        return result
    monkeypatch.setattr(codex_transport._RpcSession, 'request', lose)
    with pytest.raises(PipelineError, match='lost turn/start'):
        run(project, tmp_path, 2)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    result, _, _ = run(project, tmp_path, 2)
    assert result == RESULTS[2]
    assert len(native_mock['calls']) == 2
    assert manifest(project)['state'] == 'baseline_ready'


def test_lost_revert_ack_repairs_clean_marker(project, tmp_path, native_mock, monkeypatch):
    request = codex_transport._RpcSession.request
    lost = False
    def lose(rpc, method, params, deadline):
        nonlocal lost
        result = request(rpc, method, params, deadline)
        if method == 'thread/revert' and not lost:
            lost = True
            raise PipelineError('injected lost revert acknowledgement')
        return result
    monkeypatch.setattr(codex_transport._RpcSession, 'request', lose)
    with pytest.raises(PipelineError, match='lost revert'):
        run(project, tmp_path, 2)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2
    assert sum(m == 'thread/revert' for m, p in native_mock['rpc']) == 1
    assert manifest(project)['state'] == 'baseline_ready'


def reconcile_only(root, tmp_path, stage):
    """Use a fresh manager without allowing a new P0 or consumer inference."""
    client, settings = configured(root, tmp_path)
    store = Store(root)
    try:
        manager = PersistentSourceSessionManager(store, client, scope_for(store, f'pass{stage}/unit', inputs_for(stage)))
        manager.recover_only()
    finally:
        client.close()
        store.close()


@pytest.mark.parametrize('terminal', ['interrupted', 'failed', 'validation_failed'])
def test_failed_outcome_lost_revert_ack_recovers_without_inference(project, tmp_path, native_mock, monkeypatch, terminal):
    from bookpipe.artifacts import attempt_manifests
    from bookpipe.operations import usage_report
    from bookpipe.usage import usage_by_unit_report

    run(project, tmp_path, 2)  # An accepted consumer must never be regenerated.
    p0 = manifest(project)['p0_turn_id']
    if terminal == 'failed':
        native_mock['failed'].add(3)
    elif terminal == 'validation_failed':
        native_mock['invalid'].add(3)
    else:
        native_mock['hold_pass'] = 3
        drain = codex_transport._RpcSession.drain_until_terminal
        def interrupt(rpc, deadline):
            if rpc.recorder.identity.get('pass_no') == 3:
                assert native_mock['arrived'].wait(10)
                rpc.request('turn/interrupt', {'threadId': rpc.state['thread_id'], 'turnId': rpc.state['turn_id']}, deadline)
                native_mock['release'].set()
            return drain(rpc, deadline)
        monkeypatch.setattr(codex_transport._RpcSession, 'drain_until_terminal', interrupt)
    request = codex_transport._RpcSession.request
    def lose_revert(rpc, method, params, deadline):
        result = request(rpc, method, params, deadline)
        if method == 'thread/revert':
            raise PipelineError('injected lost failed-turn revert acknowledgement')
        return result
    if terminal == 'validation_failed':
        monkeypatch.setattr(codex_transport._RpcSession, 'request', lose_revert)
        with pytest.raises(PipelineError, match='lost failed-turn revert'):
            run(project, tmp_path, 3)
    else:
        with pytest.raises(PipelineError):
            run(project, tmp_path, 3)
        if terminal == 'interrupted':
            monkeypatch.setattr(codex_transport._RpcSession, 'drain_until_terminal', drain)
        monkeypatch.setattr(codex_transport._RpcSession, 'request', lose_revert)
        with pytest.raises(PipelineError, match='lost failed-turn revert'):
            reconcile_only(project, tmp_path, 3)
    dirty = manifest(project)
    assert dirty['state'] == 'reverting' and dirty['cleanup_required']
    attempt = project / dirty['active']['attempt']
    outcome = read_json(attempt / 'attempt.json')
    assert outcome['evidence_complete']
    assert outcome['lifecycle']['validation'] == ('failed' if terminal == 'validation_failed' else 'not_run')
    usage = (attempt / 'usage.json').read_bytes()
    attempts = list(attempt_manifests(project))
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    reconcile_only(project, tmp_path, 3)
    clean = manifest(project)
    assert clean['state'] == 'baseline_ready' and not clean['cleanup_required']
    assert clean['p0_turn_id'] == p0 and clean['active'] is None
    assert (attempt / 'usage.json').read_bytes() == usage
    assert list(attempt_manifests(project)) == attempts
    assert sum(g['attempts'] for g in usage_report(project)['groups']) == 3
    assert sum(p.provider_call_count for u in usage_by_unit_report(project).units for p in u.passes) == 3
    assert read_json(attempt / 'attempt.json')['lifecycle'] == outcome['lifecycle']
    assert len(native_mock['calls']) == 3
    assert sum(m == 'thread/revert' for m, _ in native_mock['rpc']) == 2  # P2 and failed P3 only.
    store = Store(project)
    try:
        assert store.has_job('pass2/unit') and not store.has_job('pass3/unit')
    finally:
        store.close()
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 3


@pytest.mark.parametrize('stage', [0, 2])
@pytest.mark.parametrize('bad', ['model', 'effort', 'missing_effort', 'missing_model'])
@pytest.mark.parametrize('boundary', ['rejected', 'before_verification', 'old_stop_metadata'])
def test_selection_rejection_survives_recovery(project, tmp_path, native_mock, monkeypatch, stage, bad, boundary):
    import bookpipe.source_sessions as sessions

    observe = sessions.rollout_effort_evidence
    crash = boundary != 'rejected'
    def selection(path, turn_id):
        nonlocal crash
        result = observe(path, turn_id)
        if len(native_mock['calls']) == (1 if stage == 0 else 2):
            if crash:
                crash = False
                raise KeyboardInterrupt('injected crash before selection verification')
            result.update({'rollout_model': None} if bad == 'missing_model' else
                          {'rollout_model': 'wrong-model'} if bad == 'model' else
                          {'rollout_effort': 'high' if bad == 'effort' else None})
        return result
    monkeypatch.setattr(sessions, 'rollout_effort_evidence', selection)
    with pytest.raises(PipelineError if boundary == 'rejected' else KeyboardInterrupt):
        run(project, tmp_path, 2)
    record = manifest(project)
    attempt = project / record['active']['attempt']
    assert (attempt / 'answer.txt').is_file() and (attempt / 'native.history.json').is_file()
    usage = (attempt / 'usage.json').read_bytes()
    calls = len(native_mock['calls'])
    if boundary == 'rejected':
        initial_receipt = (attempt / 'selection.verification.json').read_bytes()
        assert read_json(attempt / 'attempt.json')['evidence_complete']
        # Later matching observations must not erase an explicit rejection.
        monkeypatch.setattr(sessions, 'rollout_effort_evidence', observe)
    elif boundary == 'old_stop_metadata':
        # A historical generic completed marker is not a verification receipt.
        meta = read_json(attempt / 'response_meta.json') if (attempt / 'response_meta.json').exists() else {}
        atomic_json(attempt / 'response_meta.json', {**meta, 'finish_reason': 'stop', 'status': 'completed',
                    'wire_format': codec.WIRE_FORMAT, 'codec_map_version': codec.MAP_VERSION,
                    'session_manifest': str(next(project.glob('artifacts/*/sources/*/sessions/*/manifest.json')).relative_to(project)),
                    'accepted_attempt': str(attempt.relative_to(project)), 'usage_status': 'reported',
                    'reported_model': 'gpt-6.1-sol', 'reported_effort': 'low'})
    with pytest.raises(PipelineError, match='selection|reported|verifiable'):
        run(project, tmp_path, 2)
    assert len(native_mock['calls']) == calls
    assert not any(m == 'thread/revert' for m, _ in native_mock['rpc'])
    assert (attempt / 'usage.json').read_bytes() == usage
    receipt = read_json(attempt / 'selection.verification.json')
    if boundary == 'rejected':
        assert (attempt / 'selection.verification.json').read_bytes() == initial_receipt
    assert receipt['status'] == ('held' if bad.startswith('missing_') else 'rejected')
    assert receipt['binding']['requested_model'] == 'gpt-6.1-sol'
    assert receipt['binding']['requested_effort'] == 'low'
    assert receipt['binding']['turn_id'] == manifest(project)['active']['turn_id']
    evidence = read_json(attempt / 'attempt.json')
    assert evidence['evidence_complete'] and evidence['lifecycle']['generation'] == 'completed'
    assert evidence['lifecycle']['validation'] == 'not_run'
    assert evidence['response']['finish_reason'] == 'selection_' + receipt['status']
    store = Store(project)
    try:
        assert not store.has_job('pass2/unit')
        if stage == 0:
            assert not store.has_job('pass0/' + record['scope']['scope_id'])
    finally:
        store.close()


@pytest.mark.parametrize('stage', [0, 2])
def test_matching_selection_recovers_crash_before_verification(project, tmp_path, native_mock, monkeypatch, stage):
    import bookpipe.source_sessions as sessions

    observe = sessions.rollout_effort_evidence
    def crash(path, turn_id):
        if len(native_mock['calls']) == (1 if stage == 0 else 2):
            raise KeyboardInterrupt('injected pre-verification crash')
        return observe(path, turn_id)
    monkeypatch.setattr(sessions, 'rollout_effort_evidence', crash)
    with pytest.raises(KeyboardInterrupt):
        run(project, tmp_path, 2)
    monkeypatch.setattr(sessions, 'rollout_effort_evidence', observe)
    result, _, _ = run(project, tmp_path, 2)
    assert result == RESULTS[2] and len(native_mock['calls']) == 2
    assert manifest(project)['state'] == 'baseline_ready'
    receipts = list(project.glob('artifacts/**/attempt_*/selection.verification.json'))
    assert len(receipts) == 2
    assert all(read_json(p)['status'] == 'verified' for p in receipts)


def test_runner_saved_candidate_cannot_bypass_selection_gate(project, tmp_path, native_mock, monkeypatch):
    import bookpipe.source_sessions as sessions

    save = Store.save_job
    def crash(store, key, *args):
        if key == 'pass2/unit':
            raise KeyboardInterrupt('injected pre-checkpoint crash')
        return save(store, key, *args)
    monkeypatch.setattr(Store, 'save_job', crash)
    with pytest.raises(KeyboardInterrupt):
        run(project, tmp_path, 2)
    monkeypatch.setattr(Store, 'save_job', save)
    record = manifest(project)
    attempt = project / record['active']['attempt']
    (attempt / 'selection.verification.json').unlink()
    assert read_json(attempt / 'response_meta.json')['finish_reason'] == 'stop'
    # Exercise Runner's separate candidate gate even if native reconciliation
    # did not run (e.g. a candidate belongs to an older slot).
    monkeypatch.setattr(PersistentSourceSessionManager, 'recover_only', lambda manager: None)
    observe = sessions.rollout_effort_evidence
    monkeypatch.setattr(sessions, 'rollout_effort_evidence', lambda path, turn_id:
                        {**observe(path, turn_id), 'rollout_effort': None})
    with pytest.raises(PipelineError, match='selection verification held'):
        run(project, tmp_path, 2)
    store = Store(project)
    try:
        assert not store.has_job('pass2/unit')
    finally:
        store.close()
    assert len(native_mock['calls']) == 2 and not any(m == 'thread/revert' for m, _ in native_mock['rpc'])


@pytest.mark.parametrize('damage', ['missing_history', 'wrong_turn', 'live_status', 'incomplete', 'wrong_task', 'missing_usage', 'wrong_response', 'wrong_p0'])
def test_removed_failed_suffix_still_requires_matching_evidence(project, tmp_path, native_mock, monkeypatch, damage):
    run(project, tmp_path, 2)
    native_mock['failed'].add(3)
    with pytest.raises(PipelineError):
        run(project, tmp_path, 3)
    request = codex_transport._RpcSession.request
    def lose(rpc, method, params, deadline):
        result = request(rpc, method, params, deadline)
        if method == 'thread/revert':
            raise PipelineError('injected lost revert reply')
        return result
    monkeypatch.setattr(codex_transport._RpcSession, 'request', lose)
    with pytest.raises(PipelineError, match='lost revert reply'):
        reconcile_only(project, tmp_path, 3)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    attempt = project / manifest(project)['active']['attempt']
    if damage == 'missing_history':
        (attempt / 'native.history.json').unlink()
    elif damage == 'missing_usage':
        (attempt / 'usage.json').unlink()
    elif damage == 'wrong_response':
        meta = read_json(attempt / 'response_meta.json')
        meta['turn_id'] = 'foreign'
        atomic_json(attempt / 'response_meta.json', meta)
    elif damage in ('wrong_turn', 'live_status', 'wrong_p0'):
        turns = read_json(attempt / 'native.history.json')
        if damage == 'wrong_p0':
            turns[0]['id'] = 'foreign'
        else:
            turns[-1]['id' if damage == 'wrong_turn' else 'status'] = 'foreign' if damage == 'wrong_turn' else 'inProgress'
        atomic_json(attempt / 'native.history.json', turns)
    else:
        evidence = read_json(attempt / 'attempt.json')
        if damage == 'incomplete':
            evidence['evidence_complete'] = False
        else:
            evidence['identity']['task_key'] = 'pass3/foreign'
        atomic_json(attempt / 'attempt.json', evidence)
    with pytest.raises(PipelineError, match='binding|owned terminal outcome'):
        reconcile_only(project, tmp_path, 3)
    assert manifest(project)['cleanup_required'] and manifest(project)['p0_status'] == 'ready'
    assert len(native_mock['calls']) == 3
    assert sum(m == 'thread/revert' for m, _ in native_mock['rpc']) == 2


def test_ack_without_history_proof_never_marks_ready(project, tmp_path, native_mock, monkeypatch):
    proof = PersistentSourceSessionManager.proof
    monkeypatch.setattr(PersistentSourceSessionManager, 'proof', lambda *a: (_ for _ in ()).throw(PipelineError('durability proof unavailable')))
    with pytest.raises(PipelineError, match='durability proof'):
        run(project, tmp_path, 2)
    assert manifest(project)['p0_status'] != 'ready'
    assert len(native_mock['calls']) == 1
    monkeypatch.setattr(PersistentSourceSessionManager, 'proof', proof)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2


def test_shared_auth_link_refresh_and_lease_refuses_second_owner(project, tmp_path):
    client, settings = configured(project, tmp_path)
    auth = tmp_path / 'authorized-auth.json'
    auth.write_text('{"fake":"original"}')
    auth.chmod(0o600)
    client.settings['options']['auth_source'] = str(auth)
    store = Store(project)
    try:
        scope = scope_for(store, 'pass2/unit', inputs_for(2))
        first = PersistentSourceSessionManager(store, client, scope)
        first.acquire()
        private = first.home / 'auth.json'
        assert private.is_symlink() and private.readlink() == auth
        assert private.stat().st_mode & 0o777 == 0o600
        private.write_text('{"fake":"refreshed"}')
        assert auth.read_text() == '{"fake":"refreshed"}'
        second = PersistentSourceSessionManager(store, client, scope)
        with pytest.raises(PipelineError, match='busy'):
            second.acquire()
        first.close()
        replacement = tmp_path / 'replacement-auth.json'
        replacement.write_text('{"fake":"relogged-in"}')
        replacement.chmod(0o600)
        replacement.replace(auth)
        second.acquire()
        assert private.is_symlink() and private.read_text() == '{"fake":"relogged-in"}'
        second.close()
    finally:
        store.close()


def test_missing_native_runtime_does_not_regenerate(project, tmp_path, native_mock):
    run(project, tmp_path, 2)
    record = manifest(project)
    Path(record['runtime']).rename(Path(record['runtime']).with_name('saved-private-runtime'))
    with pytest.raises(PipelineError, match='native source-session state is missing'):
        run(project, tmp_path, 3)
    assert len(native_mock['calls']) == 2


def test_p0_usage_does_not_block_p1_reset(project, tmp_path, native_mock):
    run(project, tmp_path, 1)
    store = Store(project)
    try:
        assert not store.has_dependent_p1_work()
        store.clear_p1()
        assert store.has_job('pass0/' + manifest(project)['scope']['scope_id'])
        assert not store.has_job('pass1/unit')
    finally:
        store.close()


def test_profile_switch_and_contract_change_have_distinct_slots(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # An external caller's cwd is not the code repository.
    client, settings = configured(project, tmp_path)
    store = Store(project)
    try:
        scope = scope_for(store, 'pass2/unit', inputs_for(2))
        first = PersistentSourceSessionManager(store, client, scope)
        client.model = 'explicit-other-model'
        second = PersistentSourceSessionManager(store, client, scope)
        assert first.slot_id != second.slot_id and first.scope == second.scope
        assert first.runtime != second.runtime
        client.model = 'gpt-6.1-sol'
        prompt = project / 'prompts/pass0.txt'
        prompt.write_text(prompt.read_text() + '\nRetain exact whitespace.\n')
        third = PersistentSourceSessionManager(store, client, scope)
        assert first.slot_id != third.slot_id
        # Another workspace with identical canonical source cannot share native state.
        other = tmp_path / 'other-project'; shutil.copytree(project / 'prompts', other / 'prompts')
        other_store = Store(other)
        other_client, _ = configured(other, tmp_path)
        try:
            other_scope = scope_for(other_store, 'pass2/unit', inputs_for(2))
            fourth = PersistentSourceSessionManager(other_store, other_client, other_scope)
            assert fourth.runtime != first.runtime
        finally:
            other_store.close()
    finally:
        store.close()


def test_crash_before_p0_submission_does_not_create_false_readiness(project, tmp_path, native_mock, monkeypatch):
    ensure = PersistentSourceSessionManager.ensure_p0
    monkeypatch.setattr(PersistentSourceSessionManager, 'ensure_p0', lambda *a: (_ for _ in ()).throw(PipelineError('before P0 submission')))
    with pytest.raises(PipelineError, match='before P0 submission'):
        run(project, tmp_path, 2)
    assert not native_mock['calls']
    monkeypatch.setattr(PersistentSourceSessionManager, 'ensure_p0', ensure)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2


def test_clean_checkpoint_no_provider_work_and_explicit_rebuild(project, tmp_path, native_mock, monkeypatch):
    from bookpipe.source_sessions import retire_session, session_inventory
    run(project, tmp_path, 2)
    before = len(native_mock['rpc'])
    run(project, tmp_path, 2)
    assert len(native_mock['rpc']) == before
    original = manifest(project)
    store = Store(project)
    client, settings = configured(project, tmp_path)
    try:
        public = session_inventory(project)
        assert str(tmp_path) not in json.dumps(public) and 'auth.json' not in json.dumps(public)
        scope = scope_for(store, 'pass2/unit', inputs_for(2))
        with pytest.raises(PipelineError, match='Archive or rebuild'):
            retire_session(store, original['slot_id'], purge=True)
        retire_session(store, original['slot_id'], rebuild=True)
        replacement = PersistentSourceSessionManager(store, client, scope)
        assert replacement.generation == 2 and replacement.slot_id != original['slot_id']
        assert store.has_job('pass2/unit')
        retire_session(store, original['slot_id'], purge=True)
        assert not Path(original['runtime']).exists()
        assert store.has_job('pass2/unit')
        assert not replacement.manifest_path.exists()  # Rebuild is lazy; maintenance spent no tokens.
    finally:
        store.close()


def test_foreign_suffix_is_never_deleted(project, tmp_path, native_mock, monkeypatch):
    # Keep a valid accepted P2 suffix, then introduce an unowned native turn into
    # the paginated inspection result. No revert may be issued by reconciliation.
    cleanup = PersistentSourceSessionManager.cleanup
    monkeypatch.setattr(PersistentSourceSessionManager, 'cleanup', lambda *a: (_ for _ in ()).throw(PipelineError('hold accepted suffix')))
    with pytest.raises(PipelineError, match='hold accepted'):
        run(project, tmp_path, 2)
    monkeypatch.setattr(PersistentSourceSessionManager, 'cleanup', cleanup)
    history = PersistentSourceSessionManager.history
    def foreign(manager):
        turns = history(manager)
        if len(turns) > 1:
            turns.append({'id': 'foreign', 'status': 'completed', 'items': [
                {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'unowned'}]},
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{}'}]})
        return turns
    monkeypatch.setattr(PersistentSourceSessionManager, 'history', foreign)
    with pytest.raises(PipelineError, match='Unknown/foreign'):
        run(project, tmp_path, 2)
    assert not any(m == 'thread/revert' for m, p in native_mock['rpc'])
    assert len(native_mock['calls']) == 2


def test_partial_native_turn_is_preserved_then_retries_only_incomplete_pass(project, tmp_path, native_mock, monkeypatch):
    drain = codex_transport._RpcSession.drain_until_terminal
    def interrupt_after_native_completion(rpc, deadline):
        # Native loopback inference completes deterministically; emulate an
        # interrupted terminal in native reconciliation, never accepting it.
        return drain(rpc, deadline)
    history = PersistentSourceSessionManager.history
    interrupted = False
    def partial(manager):
        nonlocal interrupted
        turns = history(manager)
        if len(turns) > 1 and not interrupted:
            interrupted = True
            turns[-1]['status'] = 'interrupted'
            turns[-1]['items'] = [i for i in turns[-1]['items'] if i['type'] != 'agentMessage']
        return turns
    monkeypatch.setattr(PersistentSourceSessionManager, 'history', partial)
    with pytest.raises(PipelineError, match='did not complete'):
        run(project, tmp_path, 3)
    store = Store(project)
    try:
        assert not store.has_job('pass3/unit')
    finally:
        store.close()
    monkeypatch.setattr(PersistentSourceSessionManager, 'history', history)
    # Actual native stored answer completed before the injected observation;
    # recovery proves that exact answer and accepts it with no additional call.
    run(project, tmp_path, 3)
    assert len(native_mock['calls']) == 2


@pytest.mark.parametrize('point', ['p0_result', 'p0_receipt', 'p0_ready', 'terminal', 'validated', 'job', 'accepted', 'cleanup'])
def test_recover_every_acceptance_boundary(project, tmp_path, native_mock, monkeypatch, point):
    """A crash at any durable boundary recovers with exactly P0 + one P2."""
    from bookpipe.evidence import AttemptRecorder
    import bookpipe.source_sessions as sessions
    originals = []
    fired = False
    def crash_once():
        nonlocal fired
        if not fired:
            fired = True
            raise KeyboardInterrupt('injected durable boundary')
    def patch(obj, name, predicate, after=True):
        original = getattr(obj, name)
        originals.append((obj, name, original))
        def wrapped(*args, **kwargs):
            if predicate(*args, **kwargs) and not after:
                crash_once()
            result = original(*args, **kwargs)
            if predicate(*args, **kwargs) and after:
                crash_once()
            return result
        monkeypatch.setattr(obj, name, wrapped)
    if point == 'p0_result':
        patch(sessions, 'atomic_json', lambda path, value: path.name == 'result.json' and value.get('ready') == codec.READY)
    elif point in ('p0_receipt', 'job'):
        patch(Store, 'save_job', lambda store, key, *a: key.startswith('pass0/' if point == 'p0_receipt' else 'pass2/'))
    elif point in ('p0_ready', 'terminal'):
        patch(PersistentSourceSessionManager, 'transition', lambda m, state, **kw:
              state == ('baseline_ready' if point == 'p0_ready' else 'terminal_uncommitted') and
              (kw.get('p0_status') == 'ready' if point == 'p0_ready' else (kw.get('active') or {}).get('pass_no') == 2))
    elif point == 'validated':
        patch(AttemptRecorder, 'finish', lambda r, **kw: r.identity.get('pass_no') == 2 and kw.get('validation') == 'passed')
    elif point == 'accepted':
        patch(AttemptRecorder, 'mark_accepted', lambda r: r.identity.get('pass_no') == 2)
    else:
        patch(PersistentSourceSessionManager, 'cleanup', lambda m: True, after=False)
    with pytest.raises(KeyboardInterrupt):
        run(project, tmp_path, 2)
    assert fired
    for obj, name, original in originals:
        monkeypatch.setattr(obj, name, original)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2
    assert manifest(project)['state'] == 'baseline_ready'


def test_all_history_pages_and_items_are_required(project, tmp_path):
    from types import SimpleNamespace
    client, settings = configured(project, tmp_path)
    store = Store(project)
    try:
        manager = PersistentSourceSessionManager(store, client, scope_for(store, 'pass2/unit', inputs_for(2)))
        manager.record = {'thread_id': 'native-thread', 'p0_status': 'ready'}
        calls = []
        def request(method, params, deadline):
            calls.append((method, params))
            if method == 'thread/turns/list':
                second = bool(params['cursor'])
                return {'data': [{'id': 'suffix' if second else 'p0', 'status': 'completed'}],
                        'nextCursor': None if second else 'turn-page2'}
            second = bool(params['cursor'])
            return {'data': [{'turnId': params['turnId'], 'item': {'type': 'agentMessage' if second else 'userMessage'}}],
                    'nextCursor': None if second else 'item-page2'}
        manager.rpc = SimpleNamespace(request=request)
        turns = manager.history()
        assert [t['id'] for t in turns] == ['p0', 'suffix']
        assert all(len(t['items']) == 2 for t in turns)
        assert len(calls) == 6
    finally:
        store.close()


def test_real_native_interruption_is_not_accepted(project, tmp_path, native_mock, monkeypatch):
    drain = codex_transport._RpcSession.drain_until_terminal
    interrupted = False
    native_mock['hold_pass'] = 3
    def interrupt(rpc, deadline):
        nonlocal interrupted
        if rpc.recorder.identity.get('pass_no') == 3 and not interrupted:
            interrupted = True
            assert native_mock['arrived'].wait(10)
            rpc.request('turn/interrupt', {'threadId': rpc.state['thread_id'], 'turnId': rpc.state['turn_id']}, deadline)
            native_mock['release'].set()
        return drain(rpc, deadline)
    monkeypatch.setattr(codex_transport._RpcSession, 'drain_until_terminal', interrupt)
    with pytest.raises(PipelineError, match='did not complete'):
        run(project, tmp_path, 3)
    store = Store(project)
    try:
        assert not store.has_job('pass3/unit')
    finally:
        store.close()
    monkeypatch.setattr(codex_transport._RpcSession, 'drain_until_terminal', drain)
    run(project, tmp_path, 3)
    assert manifest(project)['state'] == 'baseline_ready'
    assert len(native_mock['calls']) <= 3


def test_opt_in_preserves_all_legacy_checkpoints_without_preload(project, tmp_path, monkeypatch):
    from bookpipe.engine import response_schema
    from bookpipe.util import digest
    store = Store(project)
    client, settings = configured(project, tmp_path)
    try:
        for n in range(1, 6):
            inputs = inputs_for(n)
            prompt = (project / 'prompts' / f'pass{n}.txt').read_text()
            fp = digest({'prompt': prompt, 'inputs': inputs, 'schema': response_schema(n, inputs)})
            path = project / 'artifacts' / f'legacy{n}.json'; atomic_json(path, RESULTS[n])
            store.save_job(f'pass{n}/unit', fp, path, {'provider': 'codex', 'wire_format': 'cache-shared-v2'})
        store.set('review:fixture', {'approved': True, 'revision': 'human-reviewed'})
        monkeypatch.setattr(client, 'generate', lambda *a: pytest.fail('Existing checkpoint cannot generate'))
        for n in range(1, 6):
            assert Runner(store, client, settings, client.ui).run(n, f'pass{n}/unit', inputs_for(n))[0] == RESULTS[n]
        assert store.get('review:fixture') == {'approved': True, 'revision': 'human-reviewed'}
        assert store.get('source_scope_inventory') is None
        assert not list(project.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    finally:
        store.close()


def test_owned_multiple_suffix_revert_is_exclusive_and_idempotent(project, tmp_path):
    from types import SimpleNamespace
    from bookpipe.evidence import AttemptRecorder
    from bookpipe.util import digest
    store = Store(project)
    client, settings = configured(project, tmp_path)
    try:
        manager = PersistentSourceSessionManager(store, client, scope_for(store, 'pass2/unit', inputs_for(2)))
        source = codec.readiness_message(manager.scope, manager.p0_prompt)
        def turn(tid, text, output):
            return {'id': tid, 'status': 'completed', 'items': [
                {'type': 'userMessage', 'content': [{'type': 'text', 'text': text}]},
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps(output)}]}
        baseline = turn('p0', source, codec.READY)
        turns = [baseline]
        submissions = []
        recorders = []
        for n in (2, 3):
            directory = project / 'artifacts' / 'fake' / f'attempt_{n}'
            relative = str(directory.relative_to(project))
            recorder = AttemptRecorder(directory, {'pass_no': n, 'task_key': f'pass{n}/unit',
                'task_fingerprint': f'fingerprint{n}', 'requested_model': client.model})
            recorder.semantic({'pass_no': n, 'task_key': f'pass{n}/unit', 'task_fingerprint': f'fingerprint{n}',
                               'resolved_profile': client.resolved_profile}, codec.TRANSPORT_SCHEMA)
            recorder.transport_request({'wire_format': codec.WIRE_FORMAT, 'thread_id': 'thread',
                'session_id': 'session', 'slot_id': manager.slot_id,
                'turn': {'threadId': 'thread', 'model': client.model, 'effort': client.settings['reasoning_effort'],
                         'input': [{'type': 'text', 'text': f'owned-task{n}'}]}}, codec.TRANSPORT_SCHEMA)
            recorder.usage([], {'status': 'unavailable'})
            recorder.finish(generation='completed', validation='passed', metadata={'accepted_attempt': relative,
                'thread_id': 'thread', 'session_id': 'session', 'turn_id': f'turn{n}',
                'scope_id': manager.scope.scope_id, 'slot_id': manager.slot_id, 'status': 'completed'})
            recorders.append(recorder)
            result = directory / 'result.json'; atomic_json(result, RESULTS[n])
            store.save_job(f'pass{n}/unit', f'fingerprint{n}', result, {'accepted_attempt': relative})
            submissions.append({'intent_id': f'intent{n}', 'pass_no': n, 'input_sha256': digest(f'owned-task{n}'),
                                'turn_id': f'turn{n}', 'text': f'owned-task{n}', 'attempt': relative,
                                'task_key': f'pass{n}/unit', 'fingerprint': f'fingerprint{n}'})
            turns.append(turn(f'turn{n}', f'owned-task{n}', RESULTS[n]))
        for recorder in recorders:
            recorder.artifact_json('native.history.json', turns)
        manager.record = {'state': 'accepted_cleanup_pending', 'thread_id': 'thread', 'session_id': 'session',
            'p0_turn_id': 'p0', 'submissions': submissions, 'compatibility': manager.compatibility,
            'scope': manager.scope.reference, 'slot_id': manager.slot_id}
        manager.history = lambda: copy.deepcopy(turns)
        calls = []
        def revert(method, params, deadline):
            assert method == 'thread/revert' and params['beforeTurnId'] == 'turn2'
            calls.append(params)
            turns[:] = [baseline]
            return {}
        control = AttemptRecorder(project / 'artifacts/control', {'model_turn': False})
        manager.rpc = SimpleNamespace(request=revert, recorder=control)
        manager.cleanup(); manager.cleanup()
        assert len(calls) == 1 and manager.record['retained_active_turns'] == 1
        corrupt = copy.deepcopy(baseline); corrupt['items'].append({'type': 'contextCompaction'})
        with pytest.raises(PipelineError, match='compaction'):
            manager.proof(corrupt)
    finally:
        store.close()


def test_source_p1_acceptance_merge_is_idempotent(project, tmp_path, native_mock):
    from bookpipe.engine import source_blocks
    inputs = inputs_for(1)
    atomic_json(project / 'analysis_plan.json', [{'id': 'unit', 'chapter_id': 'chapter',
        'blocks': [{**b, 'order': i} for i, b in enumerate(inputs['SOURCE_BLOCKS'], 1)]}])
    context = InferenceUnitContext(unit_id='unit', chapter_id='chapter', analysis_unit_id='unit', unit_index=1)
    for iteration in range(2):
        store = Store(project)
        client, settings = configured(project, tmp_path)
        try:
            Runner(store, client, settings, client.ui).run(1, 'pass1/unit', inputs, unit_context=context)
            snapshot = (project / 'book_memory.json').read_bytes()
            if iteration:
                assert snapshot == original
            else:
                original = snapshot
            assert store.get('analysis:unit') is not None
        finally:
            client.close(); store.close()
    assert len(native_mock['calls']) == 2


@pytest.mark.parametrize('flag', ['stop_requested', 'reload_requested'])
def test_stop_and_reload_after_p0_never_submit_consumer(project, tmp_path, native_mock, monkeypatch, flag):
    from bookpipe.application.pipeline import ReloadAtCheckpoint
    original = PersistentSourceSessionManager.ensure_p0
    def stop(manager):
        original(manager)
        setattr(manager.provider.ui, flag, True)
    monkeypatch.setattr(PersistentSourceSessionManager, 'ensure_p0', stop)
    with pytest.raises((KeyboardInterrupt, ReloadAtCheckpoint)):
        run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 1
    assert manifest(project)['p0_status'] == 'ready'
    from bookpipe.usage import usage_by_unit_report
    from bookpipe.operations import usage_report
    assert sum(p.provider_call_count for u in usage_by_unit_report(project).units for p in u.passes) == 1
    assert sum(g['attempts'] for g in usage_report(project)['groups']) == 1
    monkeypatch.setattr(PersistentSourceSessionManager, 'ensure_p0', original)
    run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 2


@pytest.mark.parametrize('stage', [0, 2])
def test_unsent_durable_intent_recovers_without_false_billing(project, tmp_path, native_mock, monkeypatch, stage):
    request = codex_transport._RpcSession.request
    blocked = False
    def before_send(rpc, method, params, deadline):
        nonlocal blocked
        if method == 'turn/start' and not blocked and json.loads(params['input'][0]['text'])['ACTIVE_PASS'] == stage:
            blocked = True
            raise KeyboardInterrupt('intent persisted before RPC send')
        return request(rpc, method, params, deadline)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', before_send)
    with pytest.raises(KeyboardInterrupt):
        run(project, tmp_path, 2)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', request)
    run(project, tmp_path, 2)
    from bookpipe.operations import usage_report
    assert len(native_mock['calls']) == 2
    assert sum(g['attempts'] for g in usage_report(project)['groups']) == 2
    assert manifest(project)['state'] == 'baseline_ready'


def test_observed_smaller_native_window_stops_consumer_before_submission(project, tmp_path, native_mock, monkeypatch):
    original = PersistentSourceSessionManager.ensure_p0
    def narrow(manager):
        original(manager)
        manager.transition('baseline_ready', native_context_window=1000)
    monkeypatch.setattr(PersistentSourceSessionManager, 'ensure_p0', narrow)
    with pytest.raises(PipelineError, match='context'):
        run(project, tmp_path, 2)
    assert len(native_mock['calls']) == 1
    assert manifest(project)['p0_status'] == 'ready'


def test_p1_reset_reconciles_accepted_suffix_before_archiving(project, tmp_path, native_mock, monkeypatch):
    from bookpipe.application.analysis_reset import AnalysisResetService
    from bookpipe.bootstrap import create_application
    Store(project).close()
    atomic_json(project / 'book.json', {'source_fingerprint': 'scratch-source-v1', 'chapters': [], 'chunks': []})
    cleanup = PersistentSourceSessionManager.cleanup
    monkeypatch.setattr(PersistentSourceSessionManager, 'cleanup', lambda *a: (_ for _ in ()).throw(PipelineError('accepted P1 cleanup interrupted')))
    with pytest.raises(PipelineError, match='accepted P1'):
        run(project, tmp_path, 1)
    monkeypatch.setattr(PersistentSourceSessionManager, 'cleanup', cleanup)
    app = create_application()
    service = AnalysisResetService(app.projects.dependencies)
    before = service.status(project)
    assert before['has_data'] and before['can_reset']
    result = service.reset(project, before['revision'])
    assert not result['has_data']
    assert manifest(project)['p0_status'] == 'ready' and manifest(project)['state'] == 'baseline_ready'
    assert len(native_mock['calls']) == 2
    run(project, tmp_path, 1)
    assert len(native_mock['calls']) == 3  # Only P1; retained P0 was not reloaded.
    from bookpipe.operations import usage_report
    from bookpipe.usage import usage_by_unit_report
    assert sum(g['attempts'] for g in usage_report(project)['groups']) == 3
    assert sum(p.provider_call_count for u in usage_by_unit_report(project).units for p in u.passes) == 3
