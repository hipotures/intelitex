from __future__ import annotations
import copy
import json
from pathlib import Path
import pytest
from bookpipe import codex_cache_v2 as v2
from bookpipe.util import PipelineError, read_json
from experiments import codex_astra_cache_comparison as astra
from experiments import codex_cache_v3_session as probe
from experiments.codex_session_cache_probe import Recorder
from test_codex_cache_v3_session import data, FakeRpc

class AstraRpc(FakeRpc):
    roots=0
    def request(self, method, params, deadline):
        if method=='thread/start':
            self.requests.append((method,copy.deepcopy(params)))
            self.roots+=1
            return {'model':params['model'],'reasoningEffort':params['config']['model_reasoning_effort'],
                    'approvalPolicy':'never','sandbox':{'type':'readOnly'},
                    'thread':{'id':f'root-{self.roots}','sessionId':f'session-{self.roots}'}}
        return super().request(method,params,deadline)

@pytest.mark.parametrize('independent',[False,True])
def test_astra_routing_roots_payloads_validation_and_usage(tmp_path,monkeypatch,data,independent):
    p2,outputs=data
    wire,ctx=v2.encode_input(p2,2)
    frozen={'p2_inputs':p2,'p2_wire':wire.text,'codec_context':ctx.as_dict(),'schema':v2.TRANSPORT_SCHEMA,
            'base':'fixed','developer':'fixed','prompts':{str(n):'test' for n in range(2,6)},'max_attempts':2,
            'pass_config':astra.PASS_CONFIG,'execution_mode':'independent' if independent else 'session',
            'pricing':{'provider':'codex','rate':{'input':3,'cached_input':.3,'cache_write_input':3.75,'output':18}}}
    experiment=probe.SessionExperiment(tmp_path,frozen,Recorder(tmp_path))
    rpc=AstraRpc(experiment.recorder,outputs,ctx)
    monkeypatch.setattr(probe,'validate_params',lambda *a:None)
    monkeypatch.setattr(experiment,'reported_turn_context',lambda row,cfg:cfg)
    experiment.execute(rpc,123)
    assert rpc.roots==(4 if independent else 1)
    turns=[p for m,p in rpc.requests if m=='turn/start']
    assert [p['effort'] for p in turns]==['low','medium','low','medium']
    assert {p['model'] for p in turns}=={'gpt-6-astra'}
    assert len({p['threadId'] for p in turns})==(4 if independent else 1)
    for n,p in zip(range(2,6),turns):
        value=json.loads(p['input'][0]['text'])
        assert ('SOURCE_BLOCKS' in value)==(independent or n==2)
        semantic=read_json(tmp_path/f'P{n}_attempt_001/request.semantic.json')['input_payload']
        assert semantic==probe.canonical_inputs(p2,outputs,n)
        assert read_json(tmp_path/f'P{n}_attempt_001/result.json')==outputs[n]
    assert probe.aggregate(experiment.rows)['input_tokens']==4000
    assert probe.aggregate(experiment.rows)['total_tokens']==4400
    assert all(r['requested_model']==r['reported_model']=='gpt-6-astra' for r in experiment.rows)
    assert [r['reported_effort'] for r in experiment.rows]==['low','medium','low','medium']


def test_reported_turn_config_mismatch_fails(tmp_path):
    frozen={'codec_context':{'version':1}} # use real context
    p2={'SOURCE_BLOCKS':[{'id':'b','kind':'paragraph','text':'English.'}], 'SOURCE_SENTENCES':[{'id':'s','block_id':'b','text':'English.'}],
        'CHUNK_ID':'x','APPROVED_LEXICON':[],'OBSERVATIONS':[],'PREVIOUS_CONTEXT':{}}
    _,ctx=v2.encode_input(p2,2);frozen['codec_context']=ctx.as_dict()
    experiment=probe.SessionExperiment(tmp_path,frozen,Recorder(tmp_path))
    path=tmp_path/'rollout.jsonl';path.write_text(json.dumps({'type':'turn_context','payload':{'turn_id':'t','model':'gpt-6.1-sol','effort':'high'}}))
    experiment.thread={'path':str(path)}
    with pytest.raises(PipelineError,match='mismatch'):experiment.reported_turn_context({'turn_id':'t'},astra.PASS_CONFIG['2'])

@pytest.mark.parametrize('name',['auth.json','credentials.json','.env'])
def test_package_rejects_credentials(tmp_path,name):
    (tmp_path/name).write_text('secret')
    with pytest.raises(PipelineError,match='Authentication'):astra.safe_package(tmp_path)


def test_package_complete_reports(tmp_path):
    root=tmp_path/'experiment';root.mkdir()
    for name in ('summary.md','summary.json','comparison/quality-review.md','comparison/quality-review.json',
                 *(f'{v}/summary.json' for v in astra.VARIANTS)):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
    result=astra.safe_package(root)
    assert Path(result['archive']).is_file()
    assert result['members']>=8


def test_equal_start_guard(tmp_path,data):
    p2,_=data
    layout,_=v2.encode_input(p2,2)
    for name in astra.VARIANTS:
        path=tmp_path/name;path.mkdir()
        frozen={'p2_inputs':p2,'p2_wire':layout.text,'schema':v2.TRANSPORT_SCHEMA,'pass_config':astra.PASS_CONFIG}
        probe.private_json(path/'frozen.json',frozen)
        probe.private_json(path/'manifest.json',{'frozen_sha256':probe.digest(frozen)})
    assert astra.verify_start(tmp_path)
    target=tmp_path/astra.VARIANTS[1]/'frozen.json';frozen=read_json(target);frozen['p2_inputs']['CHUNK_ID']='changed'
    probe.private_json(target,frozen);probe.private_json(target.parent/'manifest.json',{'frozen_sha256':probe.digest(frozen)})
    with pytest.raises(PipelineError,match='differs'):astra.verify_start(tmp_path)
