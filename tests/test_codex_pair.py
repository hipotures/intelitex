"""Native cold-runtime paired resumes against a loopback-only Responses provider."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bookpipe import codex_pair, codex_parent, codex_cache_shared_v2 as messages
from bookpipe.engine import Runner, response_schema, _semantic_execution_signature
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, read_json, digest
from test_codex_cache import PROMPTS, RESULTS, SETTINGS, inputs_for, project
from test_codex_cache_shared_v2_app_server import local_responses, configured_provider, transport_methods
from test_codex_parent import attempts, application_items


def paired_provider(project, tmp_path):
    p = configured_provider(project, tmp_path)
    p.settings['options']['translation_thread_strategy'] = codex_pair.STRATEGY
    return p


def run_pass(project, tmp_path, n, settings=SETTINGS, inputs=None, **kwargs):
    store = Store(project)
    try:
        return Runner(store, paired_provider(project, tmp_path), settings, Display(True)).run(
            n, f'pass{n}/chunk1', inputs or inputs_for(n), **kwargs)
    finally:
        store.close()


def current_attempt(project, n):
    return attempts(project, n)[-1].parent


def roles(request):
    # Keep actual Responses assistant objects, not merely our message-plan metadata.
    return [x for x in request['input'] if x.get('type') == 'message'
            and (x.get('role') == 'assistant' or x.get('role') == 'user'
                 and x['content'][0]['text'].startswith('{'))]


def test_two_cold_pairs_actual_history_identity_and_immutable_snapshots(project, tmp_path, local_responses):
    metas, frozen = {}, {}
    for n in (2, 3, 4, 5):
        assert run_pass(project, tmp_path, n)[0] == RESULTS[n]
        assert len(local_responses['requests']) == n - 1
        assert not list((tmp_path/'isolated-runtime').iterdir())
        attempt = current_attempt(project, n)
        meta = metas[n] = read_json(attempt/'response_meta.json')
        plan = read_json(attempt/'request.transport.json')
        rpc = transport_methods(attempt)
        methods = [x['method'] for x in rpc]
        assert methods.count('turn/start') == 1
        assert not {'thread/fork', 'thread/revert', 'thread/compact'} & set(methods)
        assert meta['translation_thread_strategy'] == codex_pair.STRATEGY
        assert meta['pair'] == ('P2-P3' if n < 4 else 'P4-P5')
        assert meta['reported_model'] == 'gpt-6.1-sol' and meta['reported_effort'] == 'low'
        assert meta['isolation']['ephemeral'] is False
        assert (attempt/meta['rollout_copy']).is_file()
        actual = roles(local_responses['requests'][-1])
        if n in (2, 4):
            assert methods.count('thread/start') == 1 and 'thread/resume' not in methods
            assert meta['pair_role'] == 'pair_parent'
            assert meta['pair_parent_status'] == 'available'
            assert meta['pair_parent']['accepted_result_sha256'] == digest(RESULTS[n])
            assert meta['pair_parent']['canonical_consistency_verified'] is True
            assert [x['role'] for x in actual] == ['user'] * (3 if n == 2 else 4)
            assert read_json(attempt/'attempt.json')['lifecycle']['acceptance'] == 'checkpointed'
            frozen[n] = (attempt/meta['rollout_copy']).read_bytes()
        else:
            parent_n = n-1
            parent = metas[parent_n]
            assert methods.count('thread/resume') == 1 and 'thread/start' not in methods
            assert methods.count('thread/inject_items') == 0
            resume = next(x['params'] for x in rpc if x['method'] == 'thread/resume')
            assert resume == plan['resume'] and resume['excludeTurns'] is True
            assert 'beforeTurnId' not in resume and 'ephemeral' not in resume
            assert meta['thread_id'] == meta['resumed_thread_id'] == parent['thread_id']
            assert meta['session_id'] == meta['resumed_session_id'] == parent['session_id']
            assert meta['turn_id'] != parent['turn_id']
            assert meta['pair_role'] == 'pair_continuation'
            assert meta['execution_strategy'] == 'paired_resume'
            assert meta['pair_parent_status'] == 'available'
            assert [x['role'] for x in actual] == ['user'] * (3 if n == 3 else 4) + ['assistant', 'user']
            assert actual[-2]['content'][0]['text'].strip() == (current_attempt(project,parent_n)/'answer.txt').read_text().strip()
            assert meta['pair_parent']['accepted_result_sha256'] == digest(RESULTS[parent_n])
            assert not Path(resume['path']).exists()
            parent_path = current_attempt(project,parent_n)/parent['rollout_copy']
            assert parent_path.read_bytes() == frozen[parent_n]
        assert read_json(attempt/'validation.json')['final_validation'] == 'passed'
        # .last is a call, .total is a cumulative conversation snapshot.
        usage = read_json(attempt/'usage.json')
        assert usage['input_tokens'] == 100 and usage['output_tokens'] == 5
        assert usage['thread_total']['inputTokens'] == (200 if n in (3, 5) else 100)
        assert local_responses['requests'][-1]['prompt_cache_key'] == meta['session_id']
        assert next(x['params'] for x in rpc if x['method']=='turn/start') == plan['turn']
    assert metas[2]['thread_id'] != metas[4]['thread_id']
    # P4/P5 never inherit conversational P2/P3, but retain canonical audit/draft/ledger.
    for index in (2, 3):
        actual = roles(local_responses['requests'][index])
        active = [json.loads(x['content'][0]['text'])['ACTIVE_PASS'] for x in actual
                  if x['role']=='user' and 'ACTIVE_PASS' in json.loads(x['content'][0]['text'])]
        assert active == ([4] if index == 2 else [4, 5])
        assert all(json.loads(x['content'][0]['text'])['p'] == 4 for x in actual if x['role']=='assistant')


@pytest.mark.parametrize('n', [2, 3, 4, 5])
def test_retries_restore_only_accepted_parent_history(project, tmp_path, local_responses, n):
    if n in (3,5):
        run_pass(project,tmp_path,n-1)
        parent_meta = read_json(current_attempt(project,n-1)/'response_meta.json')
        frozen = (current_attempt(project,n-1)/parent_meta['rollout_copy']).read_bytes()
    count = len(local_responses['requests'])
    local_responses['invalid_calls'].add(count+1)
    assert run_pass(project,tmp_path,n,{**SETTINGS,'json_retries':1})[0] == RESULTS[n]
    assert len(local_responses['requests']) == count+2
    dirs = [p.parent for p in attempts(project,n)]
    assert len(dirs) == 2
    assert read_json(dirs[0]/'attempt.json')['lifecycle']['acceptance'] == 'not_accepted'
    assert read_json(dirs[1]/'attempt.json')['lifecycle']['acceptance'] == 'checkpointed'
    first, second = local_responses['requests'][-2:]
    assert [{k:v for k,v in x.items() if k!='id'} for x in roles(first)[:-1]] == [
        {k:v for k,v in x.items() if k!='id'} for x in roles(second)[:-1]]
    assert 'VALIDATION_ERROR' in json.loads(roles(second)[-1]['content'][0]['text'])
    metas = [read_json(p/'response_meta.json') for p in dirs]
    if n in (2,4):
        assert len({m['thread_id'] for m in metas}) == 2
        assert not (dirs[0]/'pair_parent.json').exists()
        assert (dirs[1]/'pair_parent.json').exists()
    else:
        assert {m['thread_id'] for m in metas} == {parent_meta['thread_id']}
        assert len({m['turn_id'] for m in metas}) == 2
        assert (current_attempt(project,n-1)/parent_meta['rollout_copy']).read_bytes() == frozen
        assert all(len([x for x in roles(r) if x['role']=='assistant']) == 1 for r in (first,second))
        assert all([x['method'] for x in transport_methods(p)].count('thread/resume') == 1 for p in dirs)


@pytest.mark.parametrize('n,failure', [(n,f) for n in (3,5) for f in (
    'missing','format','common','source','contract','schema','rollout','answer','cutoff','dependency','draft')
    if not (n==3 and f=='draft')])
def test_pair_guards_safe_complete_fallback(project, tmp_path, local_responses, n, failure):
    inputs = inputs_for(n)
    store = Store(project)
    try:
        runner = Runner(store,paired_provider(project,tmp_path),SETTINGS,Display(True))
        if failure!='missing':
            runner.run(n-1,f'pass{n-1}/chunk1',inputs_for(n-1))
            key=f'pass{n-1}/chunk1'
            receipt=store.get('selected_pass:'+key)
            job=store.job(key,receipt['fingerprint'])
            attempt=current_attempt(project,n-1)
            if failure in ('format','contract','schema'):
                field={'format':'wire_format','contract':'developer_instructions_sha256','schema':'transport_output_schema_sha256'}[failure]
                meta=job['meta'];meta[field]='wrong'
                store.save_job(key,receipt['fingerprint'],project/job['path'],meta)
            elif failure=='rollout': (attempt/job['meta']['rollout_copy']).unlink()
            elif failure=='answer': (attempt/'answer.txt').write_text('{}')
            elif failure=='cutoff':
                p=attempt/job['meta']['rollout_copy'];p.write_text(p.read_text().replace('task_started','missing'))
        if failure=='common': inputs['PREVIOUS_CONTEXT']['english']+='Changed'
        if failure=='source': inputs['SOURCE_BLOCKS'][0]['text']+='Changed'
        if failure=='dependency':
            if n==3: inputs['SEMANTIC_AUDIT']['checks'][0]['risk']='medium'
            else: inputs['CORRECTION_LEDGER']['checks'][0]['status']='needs_correction'
        if failure=='draft': inputs['POLISH_DRAFT']['translations'][0]['text']+='Changed'
        before=len(local_responses['requests'])
        # Mismatched canonical dependencies may themselves be semantically invalid;
        # guard resolution is tested before submission in that case.
        body=runner.client.body(PROMPTS[n],inputs,response_schema(n,inputs),n)
        resolution=codex_pair.resolve_parent(store,body,inputs)
        assert resolution['pair_parent_status']==('unavailable' if failure=='missing' else 'incompatible')
        if failure=='dependency':
            assert len(local_responses['requests'])==before
        else:
            runner.run(n,f'pass{n}/chunk1',inputs)
            assert len(local_responses['requests'])==before+1
            meta=read_json(current_attempt(project,n)/'response_meta.json')
            assert meta['execution_strategy']=='fresh_root_fallback'
            assert meta['pair_parent_status']==resolution['pair_parent_status']
            assert all(x['role']=='user' for x in roles(local_responses['requests'][-1]))
    finally: store.close()


def test_registry_defaults_rollback_non_codex_identity_and_bytes(project,tmp_path):
    settings={'profiles':{},'default_profile':'codex-sol-high','passes':SETTINGS['passes']}
    assert codex_parent.STRATEGIES==('fresh-root','p2-parent-ephemeral-fork-v1','paired-passes-v1')
    assert codex_parent.strategy({})==codex_pair.STRATEGY
    profile=resolve_profile(settings,3,project=project)[1]
    for s in codex_parent.STRATEGIES:
        settings['profiles']['codex-sol-high']=copy.deepcopy(profile)
        settings['profiles']['codex-sol-high']['options']['translation_thread_strategy']=s
        validate_profiles(settings,project)
        assert resolve_profile(settings,3,project=project)[1]['options']['translation_thread_strategy']==s
    settings['profiles']['codex-sol-high']['options']['translation_thread_strategy']='bad'
    with pytest.raises(PipelineError,match='translation_thread_strategy'): validate_profiles(settings,project)
    before={'resolved_profile':{'provider':'codex','options':{}}}
    after=copy.deepcopy(before);after['resolved_profile']['options']['translation_thread_strategy']=codex_pair.STRATEGY
    assert _semantic_execution_signature(before)==_semantic_execution_signature(after)
    p=paired_provider(project,tmp_path)
    for n in range(1,6):
        bodies=[]
        for s in codex_parent.STRATEGIES:
            p.settings['options']['translation_thread_strategy']=s
            bodies.append(p.body(PROMPTS[n],inputs_for(n),response_schema(n,inputs_for(n)),n))
        assert bodies[0]==bodies[1]==bodies[2]


@pytest.mark.parametrize('n',[3,5])
def test_historical_accepted_roots_and_recovery_require_no_regeneration(project,tmp_path,local_responses,n):
    store=Store(project)
    try:
        old=configured_provider(project,tmp_path)
        accepted=Runner(store,old,SETTINGS,Display(True)).run(n-1,f'pass{n-1}/chunk1',inputs_for(n-1))
        receipt=store.get(f'selected_pass:pass{n-1}/chunk1')
        job=store.job(f'pass{n-1}/chunk1',receipt['fingerprint'])
        for key in ('pair_parent','accepted_attempt','translation_thread_strategy','execution_strategy','cache_parent'):
            job['meta'].pop(key,None)
        store.save_job(f'pass{n-1}/chunk1',receipt['fingerprint'],project/job['path'],job['meta'])
        new=paired_provider(project,tmp_path)
        runner=Runner(store,new,SETTINGS,Display(True))
        assert runner.run(n-1,f'pass{n-1}/chunk1',inputs_for(n-1))==accepted
        assert len(local_responses['requests'])==1
        assert runner.run(n,f'pass{n}/chunk1',inputs_for(n))[0]==RESULTS[n]
        assert read_json(current_attempt(project,n)/'response_meta.json')['execution_strategy']=='paired_resume'
        assert len(local_responses['requests'])==2
    finally:store.close()


@pytest.mark.parametrize('n',[3,5])
def test_resume_rejection_before_inference_falls_back_and_preserves_snapshot(project,tmp_path,local_responses,monkeypatch,n):
    from bookpipe.codex_transport import _RpcSession
    run_pass(project,tmp_path,n-1)
    meta=read_json(current_attempt(project,n-1)/'response_meta.json')
    snapshot=current_attempt(project,n-1)/meta['rollout_copy']
    frozen=snapshot.read_bytes()
    request=_RpcSession.request
    def rejected(rpc,method,params,deadline):
        if method=='thread/resume':
            assert Path(params['path']).read_bytes()==frozen
            assert Path(params['path']).resolve()!=snapshot.resolve()
            raise PipelineError('Codex RPC thread/resume failed: unsupported')
        return request(rpc,method,params,deadline)
    monkeypatch.setattr(_RpcSession,'request',rejected)
    run_pass(project,tmp_path,n)
    assert len(local_responses['requests'])==2
    assert snapshot.read_bytes()==frozen
    meta=read_json(current_attempt(project,n)/'response_meta.json')
    assert meta['execution_strategy']=='fresh_root_fallback'
    assert meta['pair_parent_status']=='unavailable'
    assert all(x['role']=='user' for x in roles(local_responses['requests'][-1]))
    plan=read_json(current_attempt(project,n)/'request.transport.json')
    assert 'rejected_resume' in plan
    assert len(plan['message_plan'])==len(roles(local_responses['requests'][-1]))+2


@pytest.mark.parametrize('n',[2,4])
def test_pair_evidence_failure_does_not_spend_retry_or_accept(project,tmp_path,local_responses,monkeypatch,n):
    from bookpipe.evidence import AttemptRecorder,EvidenceError
    original=AttemptRecorder.artifact_json
    def failed(recorder,name,value):
        if name=='pair_parent.json':raise EvidenceError('Cannot persist pair snapshot')
        return original(recorder,name,value)
    monkeypatch.setattr(AttemptRecorder,'artifact_json',failed)
    with pytest.raises(EvidenceError,match='pair snapshot'):
        run_pass(project,tmp_path,n,{**SETTINGS,'json_retries':2})
    assert len(local_responses['requests'])==1
    store=Store(project)
    try:assert not store.get(f'selected_pass:pass{n}/chunk1')
    finally:store.close()


@pytest.mark.parametrize('n',[3,5])
def test_interrupted_continuation_is_not_accepted_or_retried(project,tmp_path,local_responses,monkeypatch,n):
    from bookpipe.codex_transport import _RpcSession
    run_pass(project,tmp_path,n-1)
    original=_RpcSession.drain_until_terminal
    def interrupted(rpc,deadline):
        original(rpc,deadline);rpc.state['terminal']['status']='interrupted'
    monkeypatch.setattr(_RpcSession,'drain_until_terminal',interrupted)
    with pytest.raises(PipelineError,match='interrupted'):
        run_pass(project,tmp_path,n,{**SETTINGS,'json_retries':2})
    assert len(local_responses['requests'])==2
    assert len(attempts(project,n))==1
    store=Store(project)
    try:
        assert not store.get(f'selected_pass:pass{n}/chunk1')
        assert store.get(f'selected_pass:pass{n-1}/chunk1')
    finally:store.close()


def test_normal_and_targeted_paths_preserve_pairs_and_current_accepted_parent(project,tmp_path,local_responses):
    from bookpipe.application.pipeline import execute_target_pass
    from bookpipe.engine import translate
    from test_codex_cache_v2 import fixture_book
    store=Store(project)
    try:
        book=fixture_book(store)
        p=paired_provider(project,tmp_path)
        translate(store,book,p,SETTINGS,Display(True),1)
        execute_target_pass(store,book,p,SETTINGS,Display(True),'chunk1',4,True)
        execute_target_pass(store,book,p,SETTINGS,Display(True),'chunk1',5,True)
        metas={n:[read_json(path) for path in attempts(project,n)] for n in (2,3,4,5)}
        assert metas[3][0]['thread_id']==metas[2][0]['thread_id']
        assert {m['thread_id'] for m in metas[4]}.isdisjoint({metas[2][0]['thread_id']})
        assert {m['thread_id'] for m in metas[5]}=={m['thread_id'] for m in metas[4]}
        for request in local_responses['requests'][2:]:
            assert all(json.loads(x['content'][0]['text'])['p']==4 for x in roles(request) if x['role']=='assistant')
    finally:store.close()


@pytest.mark.parametrize('n',[3,5])
def test_selected_model_effort_remain_authoritative_on_resume(project,tmp_path,local_responses,n):
    run_pass(project,tmp_path,n-1)
    store=Store(project)
    try:
        child=paired_provider(project,tmp_path)
        child.model='gpt-6-astra';child.settings['reasoning_effort']='medium'
        Runner(store,child,SETTINGS,Display(True)).run(n,f'pass{n}/chunk1',inputs_for(n))
        meta=read_json(current_attempt(project,n)/'response_meta.json')
        assert meta['execution_strategy']=='paired_resume'
        assert meta['requested_model']==meta['reported_model']=='gpt-6-astra'
        assert meta['requested_effort']==meta['reported_effort']=='medium'
        assert local_responses['requests'][-1]['model']=='gpt-6-astra'
        assert local_responses['requests'][-1]['reasoning']['effort']=='medium'
    finally:store.close()


def test_p1_outside_pairs_and_actual_salvation_defaults(project,tmp_path,local_responses):
    from bookpipe.provider_registry import ProviderPool
    run_pass(project,tmp_path,1)
    meta=read_json(current_attempt(project,1)/'response_meta.json')
    assert meta['execution_strategy']=='fresh-root'
    assert 'pair_role' not in meta
    root=Path('/home/user/translations/salvation-03')
    if root.is_dir():
        files=[root/'settings.json',root/'web.config.json']
        before=[p.read_bytes() for p in files]
        pool=ProviderPool(read_json(files[0]),Display(True),root)
        try:
            table=[]
            for n in range(1,6):
                p=pool.for_pass(n)
                assert p.settings['options']['p1_wire_format']=='cache-shared-v2'
                assert p.settings['options']['translation_wire_format']=='cache-shared-v2'
                assert p.settings['options']['translation_thread_strategy']=='paired-passes-v1'
                table.append([n,p.profile_name,p.provider,p.model,p.settings['reasoning_effort']])
            assert table==[[1,'codex-astra-high','codex','gpt-6-astra','high']]+[
                [n,'codex-sol-high','codex','gpt-6.1-sol','high'] for n in (2,3,4,5)]
        finally:pool.close()
        assert [p.read_bytes() for p in files]==before


def test_repaired_canonical_audit_is_explicit_even_when_history_contains_raw(project,tmp_path,local_responses):
    raw=messages.encode_output(RESULTS[2],2,messages.context_for(inputs_for(2),2))
    raw['i']=[{'s':0,'x':'Żuraw … waits','k':1,'m':'meaning','c':'constraint','q':1}]
    local_responses['output_overrides'][1]=raw
    accepted=run_pass(project,tmp_path,2)[0]
    assert accepted['issues'][0]['source_span']=='Żuraw waits'
    meta=read_json(current_attempt(project,2)/'response_meta.json')
    assert meta['pair_parent']['deterministic_repairs']
    inputs=inputs_for(3);inputs['SEMANTIC_AUDIT']=accepted
    run_pass(project,tmp_path,3,inputs=inputs)
    request=local_responses['requests'][-1]
    assert json.loads(roles(request)[-2]['content'][0]['text'])['i'][0]['x']=='Żuraw … waits'
    assert json.loads(roles(request)[-1]['content'][0]['text'])['SEMANTIC_AUDIT']['i'][0]['x']=='Żuraw waits'
    assert read_json(current_attempt(project,3)/'request.semantic.json')['input_payload']['SEMANTIC_AUDIT']==accepted


def test_supported_effort_update_preserved_on_native_resume(project,tmp_path,local_responses,monkeypatch):
    from bookpipe import codex_transport
    from bookpipe.codex_transport import CodexAppServerClient
    from bookpipe.codex_effort import resolve_effort_plan
    catalog=tmp_path/'model-catalog.json'
    catalog.write_text(json.dumps({'models':[{
        'slug':'gpt-6.1-sol','display_name':'Local test','description':'Mock only',
        'default_reasoning_level':'low','supported_reasoning_levels':[
            {'effort':e,'description':e} for e in ('low','medium','high')],
        'supports_reasoning_effort_updates':True,'shell_type':'disabled',
        'visibility':'list','supported_in_api':True,'priority':0,'support_verbosity':False,
        'include_apps_usage_instructions':False,'truncation_policy':{'mode':'tokens','limit':10000},
        'experimental_supported_tools':[],'context_window':100000,
        'base_instructions':'Test fixture; thread baseInstructions replaces this.'}]}))
    argv=codex_transport.app_server_argv
    monkeypatch.setattr(codex_transport,'app_server_argv',lambda executable:argv(executable)+[
        '-c', 'model_catalog_json='+json.dumps(str(catalog)),
        '-c', 'model_providers.local_mock.name="OpenAI"'])
    def advertised(client,rpc,home,installed,requested,deadline):
        metadata={'supports_reasoning_effort_updates':True,'default_reasoning_level':'low',
                  'supported_reasoning_levels':[{'effort':e} for e in ('low','medium','high')]}
        return resolve_effort_plan(requested,metadata,feature_available=True),{'source':'test catalog'}
    monkeypatch.setattr(CodexAppServerClient,'_effort_plan',advertised)
    store=Store(project)
    try:
        p=paired_provider(project,tmp_path)
        p.settings['reasoning_effort']='medium'
        Runner(store,p,SETTINGS,Display(True)).run(2,'pass2/chunk1',inputs_for(2))
        p.settings['reasoning_effort']='low'
        Runner(store,p,SETTINGS,Display(True)).run(3,'pass3/chunk1',inputs_for(3))
        for n,effort in ((2,'medium'),(3,'low')):
            meta=read_json(current_attempt(project,n)/'response_meta.json')
            assert meta['reported_effort']==meta['requested_effort']==effort
            assert meta['reasoning_effort_update_mode']=='configuration_update'
            assert meta['request_effort_baseline']=='low'
            plan=read_json(current_attempt(project,n)/'request.transport.json')
            config=plan['thread' if n==2 else 'resume']['config']
            assert config['model_reasoning_effort']=='low'
            assert config['features.reasoning_effort_override'] is True
            updates=[x['reasoning']['effort'] for x in local_responses['requests'][n-2]['input']
                     if x.get('type')=='configuration_update']
            assert updates[-1]==effort
    finally:store.close()


def test_pair_history_context_bound_falls_back_before_inference(project,tmp_path,local_responses):
    run_pass(project,tmp_path,2)
    store=Store(project)
    try:
        p=paired_provider(project,tmp_path)
        body=p.body(PROMPTS[3],inputs_for(3),response_schema(3,inputs_for(3)),3)
        root_count=p.preflight(body)
        body.update(codex_pair.resolve_parent(store,body,inputs_for(3)))
        extra=body['pair_parent']['additional_history_utf8_bytes']
        assert extra>0
        assert p.preflight(body)==root_count+extra
        p.context=root_count+p.settings['planning_output_reserve']+p.settings['options'].get('context_margin_tokens',2048)
        assert p.preflight(body)==root_count
        assert 'pair_parent' not in body
        assert body['execution_strategy']=='fresh_root_fallback'
        assert body['pair_parent_status']=='incompatible'
        assert len(local_responses['requests'])==1
    finally:store.close()


@pytest.mark.parametrize('n',[3,5])
def test_completed_parent_recovery_across_strategy_change_is_not_billable(project,tmp_path,local_responses,n):
    store=Store(project)
    try:
        old=configured_provider(project,tmp_path)
        accepted=Runner(store,old,SETTINGS,Display(True)).run(n-1,f'pass{n-1}/chunk1',inputs_for(n-1))
        with store.db:
            store.db.execute('DELETE FROM jobs');store.db.execute('DELETE FROM kv')
        runner=Runner(store,paired_provider(project,tmp_path),SETTINGS,Display(True))
        assert runner.run(n-1,f'pass{n-1}/chunk1',inputs_for(n-1))==accepted
        assert len(local_responses['requests'])==1
        assert runner.run(n,f'pass{n}/chunk1',inputs_for(n))[0]==RESULTS[n]
        assert len(local_responses['requests'])==2
        assert read_json(current_attempt(project,n)/'response_meta.json')['execution_strategy']=='paired_resume'
    finally:store.close()


def test_resumed_parent_usage_is_not_a_missing_continuation_usage(project,tmp_path,local_responses,monkeypatch):
    from bookpipe.codex_transport import _RpcSession
    run_pass(project,tmp_path,2)
    original_request=_RpcSession.request
    original_dispatch=_RpcSession.dispatch
    def restored(rpc,method,params,deadline):
        result=original_request(rpc,method,params,deadline)
        if method=='thread/resume':
            rpc.dispatch({'method':'thread/tokenUsage/updated','params':{'threadId':result['thread']['id'],
                'turnId':'old-parent-turn','tokenUsage':{'last':{'inputTokens':999999,'outputTokens':999999},
                    'total':{'inputTokens':999999,'outputTokens':999999}}}})
        return result
    def unavailable(rpc,message):
        if message.get('method')=='thread/tokenUsage/updated' and rpc.state['turn_submitted']:
            return  # Simulate no current-turn usage event, not a zero-token call.
        return original_dispatch(rpc,message)
    monkeypatch.setattr(_RpcSession,'request',restored)
    monkeypatch.setattr(_RpcSession,'dispatch',unavailable)
    run_pass(project,tmp_path,3)
    attempt=current_attempt(project,3)
    usage=read_json(attempt/'usage.json')
    assert usage['status']=='unavailable' and usage['input_tokens'] is None
    assert read_json(attempt/'restored_thread_usage.json')[0]['last']['inputTokens']==999999
    assert read_json(attempt/'usage.raw.json')==[]
