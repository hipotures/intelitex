import copy
import json
from pathlib import Path

import pytest

from bookpipe.util import PipelineError
from experiments import codex_freshroot_cache_probe as p


def test_corpus_deterministic_unique_early_cell():
    a,b=p.corpus('A0'),p.corpus('A1')
    assert a==p.corpus('A0') and a!=b
    assert a.startswith('CELL_ID: A0') and b.startswith('CELL_ID: A1')
    assert len(a.split())>4500
    assert len(set(line for line in a.splitlines() if line.startswith('Paragraph')))==80
    with pytest.raises(PipelineError): p.corpus('bad\ncell')


def test_pairs_must_be_independent_and_identical():
    a=dict(id='a',sessionId='sa');b=dict(id='b',sessionId='sb')
    config=p.CELLS['A0'];w=Path('/tmp/empty')
    x=p.turn_parameters(a,w,p.corpus('A0'),config);y=p.turn_parameters(b,w,p.corpus('A0'),config)
    p.verify_pair(a,b,x,y)
    with pytest.raises(PipelineError): p.verify_pair(a,a,x,x)
    y['input'][0]['text']+='changed'
    with pytest.raises(PipelineError): p.verify_pair(a,b,x,y)


def test_declared_cwd_difference_only():
    a=dict(id='a',sessionId='sa');b=dict(id='b',sessionId='sb')
    x=p.turn_parameters(a,Path('/tmp/a'),'same',p.CELLS['C_cwd']);y=p.turn_parameters(b,Path('/tmp/b'),'same',p.CELLS['C_cwd'])
    p.verify_pair(a,b,x,y,True)
    with pytest.raises(PipelineError): p.verify_pair(a,b,x,y)
    y['effort']='high'
    with pytest.raises(PipelineError): p.verify_pair(a,b,x,y,True)


def test_fixed_low_no_override_no_fork():
    for config in p.CELLS.values():
        thread=p.thread_parameters(Path('/tmp/work'),config)
        turn=p.turn_parameters(dict(id='x'),Path('/tmp/work'),'reference',config)
        assert thread['model']==turn['model']=='gpt-6.1-sol'
        assert thread['config']==dict(model_reasoning_effort='low')
        assert turn['effort']=='low'
        assert thread['approvalPolicy']=='never' and thread['sandbox']=='read-only'
        assert not set(thread)&{'threadId','promptCacheKey','sessionId'}
        assert 'reasoning_effort_override' not in json.dumps(thread)


def test_single_factor_controls():
    a=p.CELLS['A0']
    for name,key in [('C_plain','structured'),('C_ephemeral','ephemeral'),('C_cwd','changing_cwd'),('C_http','http')]:
        assert [k for k in a if k!='description' and a[k]!=p.CELLS[name][k]]==[key]


def test_usage_uses_last_not_cumulative():
    events=[dict(last=dict(inputTokens=8000,cachedInputTokens=0,totalTokens=8001),total=dict(inputTokens=8000)),
            dict(last=dict(inputTokens=8010,cachedInputTokens=7900,totalTokens=8011),total=dict(inputTokens=16010))]
    row=p.usage_values(events)
    assert row['input_tokens']==8010 and row['cached_input_tokens']==7900 and row['total_tokens']==8011
    assert row['cache_write_input_tokens'] is None


def test_budget_and_repeat_fail_closed(tmp_path):
    p.safe_json(tmp_path/'calls.json',[])
    p.reserve_call(tmp_path,'A0','A')
    with pytest.raises(PipelineError):p.reserve_call(tmp_path,'A0','A')
    for i in range(29):p.reserve_call(tmp_path,str(i),'B')
    with pytest.raises(PipelineError):p.reserve_call(tmp_path,'overflow','A')
    assert len(p.read_json(tmp_path/'calls.json'))==30


def test_secrets_refused_and_no_file_written(tmp_path):
    with pytest.raises(PipelineError):p.safe_json(tmp_path/'secret.json',dict(value='Bearer '+'a'*40))
    assert not (tmp_path/'secret.json').exists()


def test_real_harness_methods_only_and_no_book_access():
    text=Path(p.__file__).read_text()
    assert '"thread/fork"' not in text and '"thread/resume"' not in text
    assert '/home/user/translations' not in text
    assert 'book.json' not in text
    assert 'auth.unlink(missing_ok=True)' in text
    assert 'start_new_session=True' in text


def test_actual_runner_two_fresh_roots_one_process(tmp_path, monkeypatch):
    """Exercise submission orchestration, not merely parameter construction."""
    import time
    config=p.CELLS['A0']
    d=tmp_path/'A0';d.mkdir()
    raw=p.corpus('A0').encode();(d/'input.txt').write_bytes(raw)
    p.safe_json(d/'frozen.json',dict(sha256=p.sha(raw)))
    p.safe_json(tmp_path/'manifest.json',dict(cells={'A0':config},production_snapshot={},cli='test'))
    p.safe_json(tmp_path/'calls.json',[])
    fakehome=tmp_path/'authorized';(fakehome/'.codex').mkdir(parents=True)
    (fakehome/'.codex/auth.json').write_text('{}')
    monkeypatch.setattr(p.Path,'home',lambda:fakehome)
    commands=[];requests=[]
    class Proc:
        pid=1
        def __init__(self,*a,**kw):commands.append((a,kw))
    class Rpc:
        def __init__(self,proc,rec):self.rec=rec;self.state={};self.notifications=[];self.roots=0
        def send(self,m):requests.append(m)
        def request(self,method,params,deadline):
            requests.append(dict(method=method,params=copy.deepcopy(params)))
            if method=='initialize':return {}
            if method=='thread/start':
                self.roots+=1
                return dict(model=p.MODEL,reasoningEffort=p.EFFORT,thread=dict(id=str(self.roots),sessionId='s'+str(self.roots),path=None))
            if method=='turn/start':return dict(turn=dict(id='t'+str(self.roots)))
            raise AssertionError(method)
        def reset_turn(self,tid):self.state={};self.rec.expected_thread=tid
        def drain_until_terminal(self,deadline):
            self.state.update(terminal=dict(status='completed'),terminal_error=None,context_altered=False,final_messages=['{"ok":"OK"}'],usage_events=[dict(last=dict(inputTokens=8000,cachedInputTokens=0,cacheWriteInputTokens=0,outputTokens=5,reasoningOutputTokens=0,totalTokens=8005),total=dict(inputTokens=8000))])
        def wait_late_usage(self,n):pass
    monkeypatch.setattr(p.subprocess,'Popen',Proc)
    monkeypatch.setattr(p,'ProbeRpc',Rpc)
    monkeypatch.setattr(p,'isolate_skills',lambda *a:[])
    monkeypatch.setattr(p,'validate_params',lambda *a:None)
    monkeypatch.setattr(p.CodexAppServerClient,'_cleanup_process',lambda *a,**k:None)
    p.run_cell(tmp_path,'A0')
    assert len(commands)==1
    roots=[r for r in requests if r.get('method')=='thread/start']
    turns=[r for r in requests if r.get('method')=='turn/start']
    assert len(roots)==len(turns)==2
    assert turns[0]['params']['threadId']!=turns[1]['params']['threadId']
    assert turns[0]['params']['input']==turns[1]['params']['input']
    assert not (d/'runtime/home/auth.json').exists()
    assert not any(r.get('method') in ('thread/fork','thread/resume') for r in requests)


def test_default_base_size_is_not_treated_as_corpus_failure():
    # Default Codex adds a large base/tool catalog; only minimal-base cells
    # can use provider input as the synthetic size calibration.
    assert not p.CELLS['B_base']['custom_base']
    assert not p.CELLS['A1']['custom_base']


def test_scrub_retains_only_safe_routing_metadata(tmp_path):
    import sqlite3
    directory=tmp_path/'runtime/sqlite';directory.mkdir(parents=True)
    database=directory/'logs_2.sqlite'
    conn=sqlite3.connect(database)
    conn.execute('create table logs(target text,feedback_log_body text,thread_id text)')
    identity='01234567-1234-1234-1234-123456789abc'
    conn.execute('insert into logs values(?,?,?)',('codex_api::endpoint::responses_websocket',
        'prompt_cache_key="'+identity+'" session-id="'+identity+'" Authorization: Bearer '+'a'*40,identity))
    conn.commit();conn.close()
    p.scrub_runtime_logs(tmp_path)
    assert not database.exists()
    obs=p.read_json(tmp_path/'safe-transport-observations.json')[0]
    assert obs['prompt_cache_keys']==obs['responses_session_ids']==[identity]
    assert obs['websocket_endpoint']
    assert 'Bearer' not in (tmp_path/'safe-transport-observations.json').read_text()
