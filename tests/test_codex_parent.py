"""Installed Codex, separate cold runtimes, loopback-only provider; no credentials."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bookpipe import codex_parent, codex_cache_shared_v2 as messages
from bookpipe.engine import Runner, _semantic_execution_signature, response_schema
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, digest, read_json
from test_codex_cache import PROMPTS, RESULTS, SETTINGS, inputs_for, project
from test_codex_cache_shared_v2_app_server import (
    local_responses, configured_provider, data_user_messages, transport_methods,
)


def active_provider(project, tmp_path):
    p = configured_provider(project, tmp_path)
    p.settings['options']['translation_thread_strategy'] = codex_parent.STRATEGY
    return p


def attempts(project, n):
    return sorted((project / 'artifacts' / f'pass{n}').rglob('response_meta.json'))


def application_items(request):
    return [{k:v for k,v in x.items() if k != 'id'} for x in data_user_messages(request)]


def test_cold_runtime_native_forks_and_actual_provider_isolation(project, tmp_path, local_responses):
    p = active_provider(project, tmp_path)
    store = Store(project)
    try:
        Runner(store, p, SETTINGS, Display(True)).run(2, 'pass2/chunk1', inputs_for(2))
    finally:
        store.close()  # Simulate restart of both worker and app-server.
    parent_attempt = attempts(project, 2)[0].parent
    parent_meta = read_json(parent_attempt/'response_meta.json')
    parent = read_json(parent_attempt/'cache_parent.json')
    assert parent['accepted_result_sha256'] == digest(RESULTS[2])
    assert read_json(parent_attempt/'attempt.json')['lifecycle']['acceptance'] == 'checkpointed'
    assert (parent_attempt/parent['parent_rollout']).is_file()
    assert not list((tmp_path/'isolated-runtime').iterdir())
    assert parent_meta['execution_strategy'] == 'p2_parent'
    parent_methods = transport_methods(parent_attempt)
    assert [x['method'] for x in parent_methods].count('thread/start') == 1
    assert [x['method'] for x in parent_methods].count('thread/inject_items') == 2
    roots = {parent_meta['thread_id']}
    p2_source_common = application_items(local_responses['requests'][0])[:2]
    for n in (3, 4, 5):
        store = Store(project)
        try:
            p = active_provider(project, tmp_path)
            result = Runner(store, p, SETTINGS, Display(True)).run(n, f'pass{n}/chunk1', inputs_for(n))
            assert result[0] == RESULTS[n]
        finally:
            store.close()
        assert len(local_responses['requests']) == n - 1
        assert not list((tmp_path/'isolated-runtime').iterdir())
        attempt = attempts(project, n)[0].parent
        meta = read_json(attempt/'response_meta.json')
        plan = read_json(attempt/'request.transport.json')
        rpc = transport_methods(attempt)
        methods = [x['method'] for x in rpc]
        assert methods.count('thread/fork') == methods.count('turn/start') == 1
        assert not {'thread/start', 'thread/resume', 'thread/revert'} & set(methods)
        fork = next(x['params'] for x in rpc if x['method']=='thread/fork')
        assert fork == plan['fork']
        assert fork['ephemeral'] is True and fork['excludeTurns'] is True
        assert fork['beforeTurnId'] == parent['parent_turn_id']
        assert fork['threadId'] == parent['parent_thread_id']
        assert fork['path'] == plan['parent_runtime_rollout']
        assert Path(fork['path']).name == parent['native_rollout_name']
        assert not Path(fork['path']).exists()  # Temporary import removed with the child runtime.
        assert meta['cache_parent']['parent_rollout'] == str((parent_attempt/parent['parent_rollout']).resolve())
        assert meta['forked_from_id'] == parent['parent_thread_id']
        assert meta['execution_strategy'] == 'ephemeral_fork'
        assert meta['cache_parent_status'] == 'available'
        assert meta['isolation']['ephemeral'] is True
        assert meta['session_id'] != parent['parent_session_id']
        assert meta['thread_path'] is None and meta['rollout_copy'] is None
        assert not (attempt/'codex'/'rollout.jsonl').exists()
        roots.add(meta['thread_id'])
        actual = application_items(local_responses['requests'][-1])
        assert actual[:2] == p2_source_common
        assert len(actual) == (3 if n == 3 else 4)
        expected = p.body(PROMPTS[n], inputs_for(n), response_schema(n, inputs_for(n)), n)
        assert actual == expected['injected_items'] + [messages.user_message(expected['input'])]
        assert plan['message_plan'][2:] == actual
        assert methods.count('thread/inject_items') == (0 if n == 3 else 1)
        assert not any(x.get('role') == 'assistant' or x.get('type') == 'reasoning'
                       for x in local_responses['requests'][-1]['input'])
        assert not any('"ACTIVE_PASS":2' in json.dumps(x, ensure_ascii=False) for x in actual)
        assert meta['reported_model'] == 'gpt-6.1-sol' and meta['reported_effort'] == 'low'
        assert read_json(attempt/'validation.json')['final_validation'] == 'passed'
        assert read_json(attempt/'usage.json')['input_tokens'] == 100
        assert local_responses['requests'][-1]['prompt_cache_key'] == parent['parent_session_id']
    assert len(roots) == 4
    assert application_items(local_responses['requests'][2])[:3] == application_items(local_responses['requests'][3])[:3]


@pytest.mark.parametrize('n', [2, 3, 4, 5])
def test_retries_use_clean_parents_or_sibling_forks(project, tmp_path, local_responses, n):
    store = Store(project)
    try:
        runner = Runner(store, active_provider(project, tmp_path), {**SETTINGS, 'json_retries':1}, Display(True))
        if n > 2:
            runner.run(2, 'pass2/chunk1', inputs_for(2))
        count = len(local_responses['requests'])
        local_responses['invalid_calls'].add(count + 1)
        assert runner.run(n, f'pass{n}/chunk1', inputs_for(n))[0] == RESULTS[n]
        assert len(local_responses['requests']) == count + 2
        dirs = [p.parent for p in attempts(project, n)]
        assert len(dirs) == 2
        metas = [read_json(p/'response_meta.json') for p in dirs]
        assert len({m['thread_id'] for m in metas}) == 2
        assert read_json(dirs[0]/'attempt.json')['lifecycle']['acceptance'] == 'not_accepted'
        assert read_json(dirs[1]/'attempt.json')['lifecycle']['acceptance'] == 'checkpointed'
        if n == 2:
            assert not (dirs[0]/'cache_parent.json').exists()
            assert (dirs[1]/'cache_parent.json').exists()
            body = runner.client.body(PROMPTS[3], inputs_for(3), response_schema(3, inputs_for(3)), 3)
            resolved = codex_parent.resolve_parent(store, body, inputs_for(3))
            assert resolved['cache_parent']['parent_thread_id'] == metas[1]['thread_id']
        else:
            assert {m['cache_parent']['parent_thread_id'] for m in metas} == {read_json(attempts(project,2)[0])['thread_id']}
            assert all(m['execution_strategy'] == 'ephemeral_fork' for m in metas)
        first, second = [application_items(r) for r in local_responses['requests'][-2:]]
        assert first[:-1] == second[:-1]
        assert 'VALIDATION_ERROR' in json.loads(second[-1]['content'][0]['text'])
        assert all(not any(x.get('role') == 'assistant' for x in r['input']) for r in local_responses['requests'])
    finally:
        store.close()


@pytest.mark.parametrize('failure', ['missing', 'old-format', 'common', 'source', 'contract', 'schema', 'rollout', 'cutoff', 'audit'])
def test_parent_guards_fallback_without_rerunning_p2(project, tmp_path, local_responses, failure):
    store = Store(project)
    try:
        p = active_provider(project, tmp_path)
        runner = Runner(store, p, SETTINGS, Display(True))
        if failure != 'missing':
            runner.run(2, 'pass2/chunk1', inputs_for(2))
            receipt = store.get('selected_pass:pass2/chunk1')
            job = store.job('pass2/chunk1', receipt['fingerprint'])
            attempt = attempts(project,2)[0].parent
            if failure in ('old-format', 'contract', 'schema'):
                key = {'old-format':'wire_format', 'contract':'developer_instructions_sha256', 'schema':'transport_output_schema_sha256'}[failure]
                meta = job['meta']; meta[key] = 'cache-v2' if failure == 'old-format' else 'wrong'
                store.save_job('pass2/chunk1', receipt['fingerprint'], project/job['path'], meta)
            elif failure == 'rollout':
                (attempt/job['meta']['rollout_copy']).unlink()
            elif failure == 'cutoff':
                path = attempt/job['meta']['rollout_copy']
                path.write_text(path.read_text().replace('task_started', 'missing_boundary'))
        inputs = inputs_for(3)
        if failure == 'common': inputs['PREVIOUS_CONTEXT']['english'] += 'Changed'
        if failure == 'source': inputs['SOURCE_BLOCKS'][0]['text'] += 'Changed'
        if failure == 'audit': inputs['SEMANTIC_AUDIT']['checks'][0]['risk'] = 'medium'
        before = len(local_responses['requests'])
        runner.run(3, 'pass3/chunk1', inputs)
        assert len(local_responses['requests']) == before + 1
        meta = read_json(attempts(project,3)[0])
        assert meta['execution_strategy'] == 'fresh_root_fallback'
        assert meta['cache_parent_status'] == ('unavailable' if failure == 'missing' else 'incompatible')
        methods = [x['method'] for x in transport_methods(attempts(project,3)[0].parent)]
        assert methods.count('thread/start') == 1 and 'thread/fork' not in methods
    finally:
        store.close()


def test_old_accepted_v2_evidence_needs_no_new_metadata_or_inference(project, tmp_path, local_responses):
    store = Store(project)
    try:
        runner = Runner(store, active_provider(project,tmp_path), SETTINGS, Display(True))
        runner.run(2, 'pass2/chunk1', inputs_for(2))
        receipt = store.get('selected_pass:pass2/chunk1')
        job = store.job('pass2/chunk1', receipt['fingerprint'])
        meta = job['meta']
        for key in ('cache_parent', 'accepted_attempt', 'translation_thread_strategy', 'execution_strategy'):
            meta.pop(key, None)
        store.save_job('pass2/chunk1', receipt['fingerprint'], project/job['path'], meta)
        body = runner.client.body(PROMPTS[3], inputs_for(3), response_schema(3, inputs_for(3)), 3)
        assert codex_parent.resolve_parent(store, body, inputs_for(3))['cache_parent_status'] == 'available'
        runner.run(3, 'pass3/chunk1', inputs_for(3))
        assert len(local_responses['requests']) == 2
        assert read_json(attempts(project,3)[0])['execution_strategy'] == 'ephemeral_fork'
    finally:
        store.close()


def test_strategy_default_override_validation_and_canonical_identity(project):
    settings = {'profiles':{}, 'default_profile':'codex-sol-high', 'passes':SETTINGS['passes']}
    for n in range(1, 6):
        _, profile, _ = resolve_profile(settings, n, project=project)
        assert profile['options']['translation_thread_strategy'] == codex_parent.DEFAULT_STRATEGY
        assert profile['options']['translation_wire_format'] == messages.WIRE_FORMAT
    settings['profiles']['codex-sol-high'] = copy.deepcopy(profile)
    settings['profiles']['codex-sol-high']['options']['translation_thread_strategy'] = 'fresh-root'
    assert resolve_profile(settings, 3, project=project)[1]['options']['translation_thread_strategy'] == 'fresh-root'
    for value in ('bad', True, None):
        settings['profiles']['codex-sol-high']['options']['translation_thread_strategy'] = value
        with pytest.raises(PipelineError, match='translation_thread_strategy'): validate_profiles(settings)
    previous = {'resolved_profile':{'provider':'codex', 'options':{'auth_source':'same'}}}
    current = copy.deepcopy(previous)
    current['resolved_profile']['options']['translation_thread_strategy'] = codex_parent.STRATEGY
    assert _semantic_execution_signature(previous) == _semantic_execution_signature(current)
    p = active_provider(project, project)
    for n in range(1, 6):
        before = p.body(PROMPTS[n], inputs_for(n), response_schema(n,inputs_for(n)), n)
        p.settings['options']['translation_thread_strategy'] = 'fresh-root'
        assert before == p.body(PROMPTS[n], inputs_for(n), response_schema(n,inputs_for(n)), n)
        p.settings['options']['translation_thread_strategy'] = codex_parent.STRATEGY


def test_normal_and_targeted_pipeline_use_same_accepted_parent(project, tmp_path, local_responses):
    from bookpipe.application.pipeline import execute_target_pass
    from bookpipe.engine import translate
    from test_codex_cache_v2 import fixture_book
    store = Store(project)
    try:
        book = fixture_book(store)
        p = active_provider(project, tmp_path)
        translate(store, book, p, SETTINGS, Display(True), 1)
        execute_target_pass(store, book, p, SETTINGS, Display(True), 'chunk1', 4, True)
        execute_target_pass(store, book, p, SETTINGS, Display(True), 'chunk1', 5, True)
        assert [json.loads(application_items(r)[-1]['content'][0]['text'])['ACTIVE_PASS']
                for r in local_responses['requests']] == [2,3,4,5,4,5]
        parent = read_json(attempts(project,2)[0])
        for n in (3,4,5):
            for path in attempts(project,n):
                meta = read_json(path)
                assert meta['execution_strategy'] == 'ephemeral_fork'
                assert meta['cache_parent']['parent_thread_id'] == parent['thread_id']
        for request in local_responses['requests']:
            assert not any(x.get('role') == 'assistant' for x in request['input'])
    finally:
        store.close()


def test_native_fork_rejection_falls_back_before_one_complete_inference(project, tmp_path, local_responses, monkeypatch):
    from bookpipe.codex_transport import _RpcSession
    store = Store(project)
    try:
        runner = Runner(store, active_provider(project,tmp_path), SETTINGS, Display(True))
        runner.run(2, 'pass2/chunk1', inputs_for(2))
        request = _RpcSession.request
        def rejected(rpc, method, params, deadline):
            if method == 'thread/fork':
                raise PipelineError('Codex RPC thread/fork failed: method unsupported')
            return request(rpc, method, params, deadline)
        monkeypatch.setattr(_RpcSession, 'request', rejected)
        runner.run(4, 'pass4/chunk1', inputs_for(4))
        assert len(local_responses['requests']) == 2
        meta = read_json(attempts(project,4)[0])
        assert meta['execution_strategy'] == 'fresh_root_fallback'
        assert meta['cache_parent_status'] == 'unavailable'
        assert len(application_items(local_responses['requests'][-1])) == 4
        assert not any(x.get('role') == 'assistant' for x in local_responses['requests'][-1]['input'])
    finally:
        store.close()


def test_p2_parent_evidence_failure_never_spends_retry(project, tmp_path, local_responses, monkeypatch):
    original = AttemptRecorder.artifact_json
    def failed(recorder, name, value):
        if name == 'cache_parent.json':
            raise EvidenceError('Cannot persist cache_parent.json')
        return original(recorder, name, value)
    monkeypatch.setattr(AttemptRecorder, 'artifact_json', failed)
    store = Store(project)
    try:
        with pytest.raises(EvidenceError, match='cache_parent.json'):
            Runner(store, active_provider(project,tmp_path), {**SETTINGS,'json_retries':2}, Display(True)).run(2, 'pass2/chunk1', inputs_for(2))
        assert len(local_responses['requests']) == 1
        assert not store.has_job('pass2/chunk1')
        assert not store.get('selected_pass:pass2/chunk1')
    finally:
        store.close()


def test_recovery_ignores_only_strategy_and_uses_recorded_parent(project, tmp_path, local_responses):
    store = Store(project)
    try:
        p = configured_provider(project,tmp_path)  # Explicit old fresh-root selection.
        accepted = Runner(store,p,SETTINGS,Display(True)).run(2, 'pass2/chunk1', inputs_for(2))
        with store.db:
            store.db.execute('DELETE FROM jobs'); store.db.execute('DELETE FROM kv')
        new = active_provider(project,tmp_path)
        assert Runner(store,new,SETTINGS,Display(True)).run(2, 'pass2/chunk1', inputs_for(2)) == accepted
        assert len(local_responses['requests']) == 1
        Runner(store,new,SETTINGS,Display(True)).run(3, 'pass3/chunk1', inputs_for(3))
        assert read_json(attempts(project,3)[0])['execution_strategy'] == 'ephemeral_fork'
        assert len(local_responses['requests']) == 2
    finally:
        store.close()


def test_opt_out_non_codex_and_salvation_resolution(project):
    from bookpipe.provider_registry import ProviderPool
    for kind in ('openai','vllm'):
        profile = {'provider':kind,'enabled':True,'model':'test','context_size':10000,'options':{},
                   **({'endpoint':'http://localhost:8080/v1'} if kind == 'vllm' else {})}
        settings = {'profiles':{'custom':profile}, 'default_profile':'custom', 'passes':SETTINGS['passes']}
        assert resolve_profile(settings,3,project=project)[1]['options'] == {}
    root = Path('/home/user/translations/salvation-03')
    if root.is_dir():
        settings_path, web = root/'settings.json', root/'web.config.json'
        before = [p.read_bytes() for p in (settings_path, web)]
        settings = read_json(settings_path)
        pool = ProviderPool(settings, Display(True), root)
        for n in (2,3,4,5):
            p = pool.for_pass(n)
            assert p.provider == 'codex'
            assert p.settings['options']['translation_wire_format'] == messages.WIRE_FORMAT
            assert p.settings['options']['translation_thread_strategy'] == codex_parent.DEFAULT_STRATEGY
            selected = settings['profiles'].get(p.profile_name)
            if selected:
                assert p.model == selected['model']
                assert p.settings['reasoning_effort'] == selected['reasoning_effort']
        assert [p.read_bytes() for p in (settings_path, web)] == before


def test_model_effort_switch_remains_authoritative_for_sibling(project, tmp_path, local_responses):
    store = Store(project)
    try:
        parent = active_provider(project,tmp_path)
        Runner(store,parent,SETTINGS,Display(True)).run(2, 'pass2/chunk1', inputs_for(2))
        child = active_provider(project,tmp_path)
        child.model = 'gpt-6-astra'
        child.settings['reasoning_effort'] = 'medium'
        Runner(store,child,SETTINGS,Display(True)).run(3, 'pass3/chunk1', inputs_for(3))
        request = local_responses['requests'][-1]
        meta = read_json(attempts(project,3)[0])
        assert meta['execution_strategy'] == 'ephemeral_fork'
        assert request['model'] == meta['requested_model'] == meta['reported_model'] == 'gpt-6-astra'
        assert request['reasoning']['effort'] == meta['requested_effort'] == meta['reported_effort'] == 'medium'
    finally:
        store.close()


def test_interrupted_child_does_not_checkpoint_or_continue_history(project, tmp_path, local_responses, monkeypatch):
    from bookpipe.codex_transport import _RpcSession
    store = Store(project)
    try:
        p = active_provider(project,tmp_path)
        runner = Runner(store,p,{**SETTINGS,'json_retries':2},Display(True))
        runner.run(2, 'pass2/chunk1', inputs_for(2))
        original = _RpcSession.drain_until_terminal
        def interrupted(rpc, deadline):
            original(rpc,deadline)
            rpc.state['terminal']['status'] = 'interrupted'
        monkeypatch.setattr(_RpcSession,'drain_until_terminal',interrupted)
        with pytest.raises(PipelineError, match='interrupted'):
            runner.run(3, 'pass3/chunk1', inputs_for(3))
        assert len(local_responses['requests']) == 2
        assert not store.has_job('pass3/chunk1')
        assert not store.get('selected_pass:pass3/chunk1')
        assert len(attempts(project,3)) == 1
        assert store.get('selected_pass:pass2/chunk1')
    finally:
        store.close()


def test_parent_byte_import_and_changed_pre_turn_history_fail_closed(project, tmp_path, local_responses, monkeypatch):
    from bookpipe.codex_transport import _RpcSession
    store = Store(project)
    try:
        runner = Runner(store,active_provider(project,tmp_path),SETTINGS,Display(True))
        runner.run(2, 'pass2/chunk1', inputs_for(2))
        parent_meta = read_json(attempts(project,2)[0])
        original_path = attempts(project,2)[0].parent/parent_meta['rollout_copy']
        frozen = original_path.read_bytes()
        request = _RpcSession.request
        def capture(rpc, method, params, deadline):
            if method == 'thread/fork':
                assert Path(params['path']).read_bytes() == frozen
            return request(rpc, method, params, deadline)
        monkeypatch.setattr(_RpcSession,'request',capture)
        runner.run(3, 'pass3/chunk1', inputs_for(3))
        assert original_path.read_bytes() == frozen
        # Inject an assistant item BEFORE the accepted P2 boundary in the export.
        events = [json.loads(x) for x in frozen.decode().splitlines()]
        index = next(i for i,e in enumerate(events) if e.get('type')=='event_msg' and e['payload'].get('type')=='task_started')
        events.insert(index, {'type':'response_item','payload':{'type':'message','role':'assistant',
                                                              'content':[{'type':'output_text','text':'previous answer'}]}})
        original_path.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        body = runner.client.body(PROMPTS[4],inputs_for(4),response_schema(4,inputs_for(4)),4)
        assert codex_parent.resolve_parent(store,body,inputs_for(4))['cache_parent_status']=='incompatible'
        assert len(local_responses['requests']) == 2
    finally:
        store.close()
