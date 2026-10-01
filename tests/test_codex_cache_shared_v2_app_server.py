"""Real installed app-server -> loopback Responses SSE mock. No auth/live model."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bookpipe import codex_cache_shared_v2 as messages, codex_transport
from bookpipe.engine import Runner
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, read_json
from test_codex_cache import PROMPTS, RESULTS, SETTINGS, inputs_for, project
from test_codex_cache_shared_v2 import provider


@pytest.fixture
def local_responses(tmp_path, monkeypatch):
    if not shutil.which('codex'):
        pytest.skip('Installed Codex required for provider-facing integration test')
    version = subprocess.check_output(['codex', '--version'], text=True).strip()
    state = {'cli_version': version, 'requests': [], 'headers': [], 'invalid_first': False, 'invalid_calls': set(), 'output_overrides': {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(request)
            state['headers'].append({k: self.headers.get(k) for k in ('session-id', 'x-codex-turn-metadata')})
            assert self.path == '/v1/responses'
            assert not self.headers.get('Authorization')
            users = [x for x in request['input'] if x.get('type') == 'message' and x.get('role') == 'user']
            suffix = json.loads(users[-1]['content'][0]['text'])
            n = suffix['ACTIVE_PASS']
            output = messages.encode_output(RESULTS[n], n, messages.context_for(inputs_for(n), n))
            if (state['invalid_first'] and len(state['requests']) == 1) or len(state['requests']) in state['invalid_calls']:
                output['t' if n in (3, 5) else 'c'] = []
            output = state['output_overrides'].get(len(state['requests']), output)
            rid = f"response-{len(state['requests'])}"
            item = {'type':'message', 'id':f'message-{rid}', 'role':'assistant', 'phase':'final_answer',
                    'content':[{'type':'output_text', 'text':json.dumps(output, ensure_ascii=False), 'annotations':[]}]}
            events = [
                {'type':'response.created', 'response':{'id':rid}},
                {'type':'response.output_item.done', 'output_index':0, 'item':item},
                {'type':'response.completed', 'response':{'id':rid, 'status':'completed', 'output':[item],
                    'usage':{'input_tokens':100, 'output_tokens':5, 'total_tokens':105,
                             'input_tokens_details':{'cached_tokens':0}, 'output_tokens_details':{'reasoning_tokens':0}}}},
            ]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    argv = codex_transport.app_server_argv
    monkeypatch.setattr(codex_transport, 'app_server_argv', lambda executable: argv(executable) + [
        '-c', 'model_provider="local_mock"',
        '-c', 'model_providers.local_mock.name="Local mock"',
        '-c', f'model_providers.local_mock.base_url="http://127.0.0.1:{server.server_port}/v1"',
        '-c', 'model_providers.local_mock.wire_api="responses"',
        '-c', 'model_providers.local_mock.requires_openai_auth=false',
        '-c', 'model_providers.local_mock.supports_websockets=false',
        '-c', 'model_providers.local_mock.request_max_retries=0',
        '-c', 'model_providers.local_mock.stream_max_retries=0',
    ])
    # This tests the installed implementation, not only our internal message plan.
    request = codex_transport._RpcSession.request
    def observed(rpc, method, params, deadline):
        before = len(state['requests'])
        result = request(rpc, method, params, deadline)
        if method == 'thread/inject_items':
            assert len(state['requests']) == before, 'Injection started model inference'
        return result
    monkeypatch.setattr(codex_transport._RpcSession, 'request', observed)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def configured_provider(project, tmp_path):
    p = provider(project)
    p.timeout = 20
    p.settings['runtime_root'] = str(tmp_path/'isolated-runtime')
    p.settings['options']['late_usage_wait'] = 0.01
    # These tests explicitly exercise the supported fresh-root rollback.
    p.settings['options']['translation_thread_strategy'] = 'fresh-root'
    assert not p.settings['options'].get('auth_source')
    return p


def transport_methods(attempt):
    events = [json.loads(line) for line in (attempt/'transport.jsonl').read_text().splitlines()]
    return [x['payload'] for x in events if x['direction'] == 'outbound' and x['kind'] == 'rpc' and 'method' in x['payload']]


def data_user_messages(request):
    users = [x for x in request['input'] if x.get('type') == 'message' and x.get('role') == 'user']
    # A platform-owned environment message may precede the application data.
    return [x for x in users if x['content'][0]['text'].startswith('{')]


def test_real_app_server_distinct_boundaries_one_request_and_fresh_roots(project, tmp_path, local_responses):
    p = configured_provider(project, tmp_path)
    store = Store(project)
    roots, data = [], []
    try:
        runner = Runner(store, p, SETTINGS, Display(True))
        for n in range(1, 6):
            before = len(local_responses['requests'])
            assert runner.run(n, f'pass{n}/chunk1', inputs_for(n))[0] == RESULTS[n]
            assert len(local_responses['requests']) == before + 1
            attempt = next((project/'artifacts'/f'pass{n}').rglob('response_meta.json')).parent
            meta = read_json(attempt/'response_meta.json')
            plan = read_json(attempt/'request.transport.json')
            rpc = transport_methods(attempt)
            methods = [x['method'] for x in rpc]
            assert methods.count('thread/start') == methods.count('turn/start') == 1
            assert not {'thread/fork','thread/resume','turn/steer'} & set(methods)
            assert methods.count('thread/inject_items') == (1 if n == 1 else 3 if n in (4, 5) else 2)
            injections = [x for x in rpc if x['method'] == 'thread/inject_items']
            assert [{'method':x['method'], 'params':x['params']} for x in injections] == plan['injections']
            assert next(x['params'] for x in rpc if x['method']=='turn/start') == plan['turn']
            assert next(x['params'] for x in rpc if x['method']=='thread/start') == plan['thread']
            assert all(x['params']['threadId'] == meta['thread_id'] for x in injections)
            roots.append(meta['thread_id'])
            actual = data_user_messages(local_responses['requests'][-1])
            assert len(actual) == (2 if n == 1 else 4 if n in (4, 5) else 3)
            expected = [x['params']['items'][0] for x in injections] + [messages.user_message(plan['turn']['input'][0]['text'])]
            assert [{k:v for k,v in x.items() if k!='id'} for x in actual] == expected
            assert plan['message_plan'][2:] == expected
            assert not any(x.get('role') == 'assistant' for x in local_responses['requests'][-1]['input'])
            assert local_responses['requests'][-1]['model'] == meta['requested_model'] == meta['reported_model'] == 'gpt-6.1-sol'
            assert meta['requested_effort'] == meta['reported_effort'] == 'low'
            assert local_responses['requests'][-1]['text']['format']['schema'] == messages.TRANSPORT_SCHEMA
            assert read_json(attempt/'usage.json')['input_tokens'] == 100
            assert read_json(attempt/'validation.json')['final_validation'] == 'passed'
            data.append(expected)
        assert len(set(roots)) == 5
        assert len({json.dumps(x[0], ensure_ascii=False) for x in data}) == 1
        assert len({json.dumps(x[1], ensure_ascii=False) for x in data[1:]}) == 1
        assert data[3][:3] == data[4][:3]
    finally:
        store.close()


@pytest.mark.parametrize('n', [3, 4, 5])
def test_real_retry_fresh_root_complete_messages_and_no_failed_history(project, tmp_path, local_responses, n):
    local_responses['invalid_first'] = True
    p = configured_provider(project, tmp_path)
    store = Store(project)
    try:
        result = Runner(store, p, {**SETTINGS, 'json_retries':1}, Display(True)).run(n, f'pass{n}/chunk1', inputs_for(n))
        assert result[0] == RESULTS[n]
        assert len(local_responses['requests']) == 2
        first, second = [data_user_messages(x) for x in local_responses['requests']]
        strip = lambda x: [{k:v for k,v in item.items() if k!='id'} for item in x]
        assert strip(first[:-1]) == strip(second[:-1])
        upstream = 'CORRECTION_LEDGER' if n == 5 else 'SEMANTIC_AUDIT'
        assert json.loads(second[-1]['content'][0]['text'])[upstream] == json.loads(first[-1]['content'][0]['text'])[upstream]
        assert 'VALIDATION_ERROR' in json.loads(second[-1]['content'][0]['text'])
        assert not any(x.get('role') == 'assistant' for x in local_responses['requests'][1]['input'])
        attempts = sorted((project/'artifacts').rglob('response_meta.json'))
        metas = [read_json(x) for x in attempts]
        assert len({x['thread_id'] for x in metas}) == 2
        assert [x['wire_format'] for x in metas] == [messages.WIRE_FORMAT]*2
    finally:
        store.close()


def test_unsupported_injection_stops_before_model_request(project, tmp_path, local_responses, monkeypatch):
    request = codex_transport._RpcSession.request
    def unsupported(rpc, method, params, deadline):
        if method == 'thread/inject_items':
            raise PipelineError('Codex RPC thread/inject_items failed: method unsupported')
        return request(rpc, method, params, deadline)
    monkeypatch.setattr(codex_transport._RpcSession, 'request', unsupported)
    store = Store(project)
    try:
        with pytest.raises(PipelineError, match='inject_items'):
            Runner(store, configured_provider(project, tmp_path), SETTINGS, Display(True)).run(2, 'pass2/chunk1', inputs_for(2))
        assert local_responses['requests'] == []
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    finally:
        store.close()


def test_message_plan_evidence_failure_does_not_trigger_model_retry(project, tmp_path, local_responses, monkeypatch):
    from bookpipe.evidence import AttemptRecorder, EvidenceError
    write = AttemptRecorder.transport_request
    def failed(recorder, plan, schema):
        if plan.get('turn', {}).get('threadId'):
            raise EvidenceError('Disk error saving the complete injection/turn plan')
        return write(recorder, plan, schema)
    monkeypatch.setattr(AttemptRecorder, 'transport_request', failed)
    store = Store(project)
    try:
        with pytest.raises(EvidenceError, match='Disk error'):
            Runner(store, configured_provider(project, tmp_path), {**SETTINGS, 'json_retries':2}, Display(True)).run(4, 'pass4/chunk1', inputs_for(4))
        assert local_responses['requests'] == []
        assert len(list((project/'artifacts').rglob('attempt.json'))) == 1
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    finally:
        store.close()
