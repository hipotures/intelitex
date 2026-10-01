from __future__ import annotations

import copy
import json
from dataclasses import replace

import jsonschema
import pytest

from bookpipe import codex_cache_shared as old, codex_cache_shared_v2 as messages
from bookpipe.codex_cache import encode_value
from bookpipe.engine import Runner, _decode_transport_result, response_schema, validate_result
from bookpipe.evidence import AttemptRecorder, EvidenceError
from bookpipe.profiles import resolve_profile, validate_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, digest, read_json
from test_codex_cache import PROMPTS, RESULTS, SETTINGS, client, inputs_for, project
from test_codex_cache_shared import OfflineShared, tasks
from test_codex_cache_v2 import OfflineV2, rich_inputs


def provider(root):
    return client(root, p1_wire_format=messages.WIRE_FORMAT, translation_wire_format=messages.WIRE_FORMAT)


def test_messages_developer_schema_and_prefix_identity(project, tasks):
    inputs, _ = tasks
    bodies = [provider(project).body(PROMPTS[n], inputs[n], response_schema(n, inputs[n]), n) for n in range(1, 6)]
    assert len({encode_value(b['output_schema']) for b in bodies}) == 1
    assert bodies[0]['output_schema'] == old.TRANSPORT_SCHEMA
    assert len(encode_value(bodies[0]['output_schema']).encode()) == 2758
    assert len({b['developer_instructions'] for b in bodies}) == 1
    assert len({b['injected_items'][0]['content'][0]['text'] for b in bodies}) == 1
    assert len({b['injected_items'][1]['content'][0]['text'] for b in bodies[1:]}) == 1
    assert bodies[3]['injected_items'] == bodies[4]['injected_items']
    for n, b in enumerate(bodies, 1):
        layout, ctx = messages.encode_input(inputs[n], n)
        assert [set(json.loads(x)) for x in layout.messages[:-1]] == [
            {'CACHE_SHARED_V2', 'SOURCE'}, *([{'TRANSLATION_COMMON'}] if n > 1 else []),
            *([{'POLISH_DRAFT'}] if n in (4, 5) else []),
        ]
        suffix = json.loads(b['input'])
        assert list(suffix)[-1] == 'ACTIVE_PASS' and suffix['ACTIVE_PASS'] == n
        assert not set(suffix) & {'SOURCE', 'SOURCE_BLOCKS', 'SOURCE_LOOKUP', 'TRANSLATION_COMMON', 'POLISH_DRAFT'}
        assert messages.decode_input(layout, ctx) == inputs[n]
        assert ctx.as_dict()['wire_format'] == messages.WIRE_FORMAT
        diag = b['cache_diagnostics']
        for label, text in zip(('source', *(['translation_common'] if n > 1 else []),
                                *(['draft'] if n in (4, 5) else []), 'active_pass'), layout.messages):
            assert diag[f'{label}_message_sha256'] == digest(text)
            assert diag[f'{label}_message_utf8_bytes'] == len(text.encode())
        assert diag['full_input_sha256'] == digest(layout.text)
        assert diag['input_utf8_bytes'] == sum(len(x.encode()) for x in layout.messages)
        expected = len(b['developer_instructions'].encode()) + diag['input_utf8_bytes']
        assert provider(project).preflight(b) == expected


@pytest.mark.parametrize('n', range(1, 6))
@pytest.mark.parametrize('retry', [False, True])
def test_all_input_and_output_roundtrips_reuse_exact_old_codec(tasks, n, retry):
    inputs, results = tasks
    if retry:
        inputs[n].update(VALIDATION_ERROR='Source "block/z" failed', RETRY_INSTRUCTION='original', ALLOWED_EVIDENCE_IDS=['block/z'])
    before = copy.deepcopy(inputs[n])
    layout, ctx = messages.encode_input(inputs[n], n)
    old_layout, old_ctx = old.encode_input(inputs[n], n)
    assert messages.decode_input(layout, ctx) == old.decode_input(old_layout, old_ctx) == before
    assert inputs[n] == before
    assert messages.CodecContext.from_dict(ctx.as_dict()) == ctx
    compact = messages.encode_output(results[n], n, ctx)
    assert compact == old.encode_output(results[n], n, old_ctx)
    decoded = _decode_transport_result(compact, messages.WIRE_FORMAT, inputs[n], ctx.as_dict(), expected_pass=n)
    assert decoded == results[n]
    jsonschema.validate(decoded, response_schema(n, inputs[n]))
    validate_result(n, decoded, inputs[n])


def test_retry_stage_changes_and_different_source(tasks):
    inputs, _ = tasks
    original = {n: messages.encode_input(inputs[n], n)[0] for n in range(1, 6)}
    for n in range(1, 6):
        inputs[n].update(VALIDATION_ERROR='Different error with canonical IDs', RETRY_INSTRUCTION='Retry')
        if n in (3, 4): inputs[n]['SEMANTIC_AUDIT']['checks'].reverse()
        if n == 5: inputs[n]['CORRECTION_LEDGER']['checks'].reverse()
        layout, ctx = messages.encode_input(inputs[n], n)
        assert layout.injected_messages == original[n].injected_messages
        assert layout.active_pass_message != original[n].active_pass_message
        assert 'canonical IDs' not in layout.active_pass_message
        assert messages.decode_input(layout, ctx) == inputs[n]
    inputs[1]['SOURCE_BLOCKS'].append({'id':'later', 'kind':'paragraph', 'text':'Later.\n'})
    assert messages.encode_input(inputs[1], 1)[0].source_message != original[2].source_message
    for n in (4, 5):
        inputs[n]['POLISH_DRAFT']['translations'][0]['text'] += ' Changed.'
        layout, _ = messages.encode_input(inputs[n], n)
        assert layout.source_message == original[n].source_message
        assert layout.translation_common_message == original[n].translation_common_message
        assert layout.draft_message != original[n].draft_message


@pytest.mark.parametrize('n', range(1, 6))
@pytest.mark.parametrize('bad', [True, False, -1, 1.0, 999, '0', None])
def test_invalid_references_and_inactive_arrays_still_rejected(tasks, n, bad):
    inputs, results = tasks
    ctx = messages.context_for(inputs[n], n)
    compact = messages.encode_output(results[n], n, ctx)
    if n == 1: compact['a'][0]['e'][0] = bad
    elif n in (2, 4): compact['c'][0]['s'] = bad
    else: compact['t'][0]['b'] = bad
    with pytest.raises((PipelineError, jsonschema.ValidationError)): messages.decode_output(compact, n, ctx)
    compact = messages.encode_output(results[n], n, ctx)
    compact['p'] = n % 5 + 1
    with pytest.raises(PipelineError, match='pass'): messages.decode_output(compact, n, ctx)
    compact = messages.encode_output(results[n], n, ctx)
    compact['t' if n in (1, 2, 4) else 'c'] = [{'b':0, 't':'extra'}] if n in (1, 2, 4) else [{'s':0, 'v':0}]
    with pytest.raises(PipelineError, match='inactive'): messages.decode_output(compact, n, ctx)


@pytest.mark.parametrize('n', range(1, 6))
def test_custom_rules_preserved_and_output_contracts_fail_closed(n):
    prompts = dict(PROMPTS)
    rule = "\nRetain the narrator's dry irony.\n"
    prompts[n] = rule + prompts[n] + rule
    assert messages.developer_contract(prompts).count(encode_value(rule)[1:-1]) == 2
    assert 'FINAL user message' in messages.developer_contract(prompts)
    prompts[n] = PROMPTS[n] + '\nReturn only a string.'
    with pytest.raises(PipelineError, match='contract'): messages.developer_contract(prompts)


def test_incorrect_message_domains_and_saved_versions_fail_closed():
    layout, ctx = messages.encode_input(inputs_for(1), 1)
    for bad in (replace(layout, translation_common_message='{}'), replace(layout, draft_message='{}'),
                replace(layout, source_message='{"CACHE_SHARED_V2":99,"SOURCE":{}}')):
        with pytest.raises(PipelineError): messages.decode_input(bad, ctx)
    for wire in ('cache-shared-v1', 'cache-v2', 'future'):
        with pytest.raises(PipelineError): messages.CodecContext.from_dict({**ctx.as_dict(), 'wire_format':wire})
    with pytest.raises(EvidenceError):
        _decode_transport_result(messages.encode_output(RESULTS[1], 1, ctx), messages.WIRE_FORMAT, inputs_for(1),
                                 {**ctx.as_dict(), 'block_ids':['corrupt']}, expected_pass=1)


class OfflineMessages(OfflineShared):
    def __init__(self, root):
        super().__init__(root)
        self.settings['options'].update(p1_wire_format=messages.WIRE_FORMAT, translation_wire_format=messages.WIRE_FORMAT)

    def generate(self, body, directory, recorder):
        self.calls.append(body)
        n = body['pass_no']
        output = messages.encode_output(RESULTS[n], n, messages.CodecContext.from_dict(body['codec_context']))
        raw = encode_value(output)
        recorder.transport_request({k:v for k,v in body.items() if k != 'codec_context'}, body['output_schema'])
        recorder.write_answer(raw)
        return raw, {'wire_format':messages.WIRE_FORMAT, 'provider':'codex', 'finish_reason':'stop',
                     'status':'completed', 'usage_status':'reported', **body['cache_diagnostics']}


@pytest.mark.parametrize('n', range(1, 6))
def test_checkpoint_fingerprint_and_completed_message_attempt_recovery(project, n):
    store = Store(project)
    try:
        p = OfflineMessages(project)
        runner = Runner(store, p, SETTINGS, Display(True))
        inputs = inputs_for(n)
        assert runner.fingerprint(n, inputs) == digest({'prompt':PROMPTS[n], 'inputs':inputs, 'schema':response_schema(n, inputs)})
        accepted = runner.run(n, f'pass{n}/chunk1', inputs)
        attempt = next((project/'artifacts').rglob('decoded.canonical.json')).parent
        assert read_json(attempt/'decoded.canonical.json') == RESULTS[n]
        assert read_json(attempt/'validation.json')['final_validation'] == 'passed'
        assert read_json(attempt/'codec.context.json')['wire_format'] == messages.WIRE_FORMAT
        with store.db:
            store.db.execute('DELETE FROM jobs'); store.db.execute('DELETE FROM kv')
        new = OfflineShared(project)
        assert Runner(store, new, SETTINGS, Display(True)).run(n, f'pass{n}/chunk1', inputs) == accepted
        assert not new.calls
    finally: store.close()


@pytest.mark.parametrize('wire', ['canonical', 'cache-v1', 'cache-v2', 'cache-shared-v1'])
@pytest.mark.parametrize('n', range(2, 6))
def test_old_translation_history_recoverable_without_new_inference(project, wire, n):
    store = Store(project)
    try:
        previous = OfflineShared(project) if wire == old.WIRE_FORMAT else OfflineV2(project, wire)
        accepted = Runner(store, previous, SETTINGS, Display(True)).run(n, f'pass{n}/chunk1', inputs_for(n))
        with store.db:
            store.db.execute('DELETE FROM jobs'); store.db.execute('DELETE FROM kv')
        current = OfflineMessages(project)
        assert Runner(store, current, SETTINGS, Display(True)).run(n, f'pass{n}/chunk1', inputs_for(n)) == accepted
        assert not current.calls
    finally: store.close()


def test_profiles_accept_new_format_but_keep_current_defaults(project):
    p = provider(project)
    validate_profiles({'profiles':{'test':p.resolved_profile}, 'default_profile':'test'})
    for n in range(1, 6):
        _, profile, _ = resolve_profile({'profiles':{}, 'default_profile':'codex-sol-high', 'passes':SETTINGS['passes']}, n, project=project)
        assert profile['options']['p1_wire_format'] == old.WIRE_FORMAT
        assert profile['options']['translation_wire_format'] == old.WIRE_FORMAT


@pytest.mark.parametrize('n,mutate', [
    (1, lambda v: v['terms'][0].update(source='Invented absent term')),
    (1, lambda v: v['terms'][0].update(candidates=[])),
    (2, lambda v: v['checks'].pop()),
    (2, lambda v: v['checks'].append(v['checks'][0])),
    (2, lambda v: v['issues'][0].update(source_span='unsupported')),
    (3, lambda v: v['translations'].reverse()),
    (4, lambda v: v['checks'][0].update(status='ok')),
    (4, lambda v: v['corrections'][0].update(draft_span='unsupported')),
    (5, lambda v: v['translations'].pop()),
    (5, lambda v: v['translations'][0].update(text=' ')),
])
def test_complete_canonical_constraints_after_message_decoding(tasks, n, mutate):
    inputs, results = tasks
    ctx = messages.context_for(inputs[n], n)
    decoded = messages.decode_output(messages.encode_output(results[n], n, ctx), n, ctx)
    mutate(decoded)
    with pytest.raises((PipelineError, jsonschema.ValidationError)):
        jsonschema.validate(decoded, response_schema(n, inputs[n]))
        validate_result(n, decoded, inputs[n])


def test_normal_and_targeted_pipeline_keep_dependencies(project):
    from bookpipe.application.pipeline import execute_target_pass
    from bookpipe.engine import translate
    from test_codex_cache_v2 import fixture_book
    store = Store(project)
    book = fixture_book(store)
    p = OfflineMessages(project)
    try:
        translate(store, book, p, SETTINGS, Display(True), 1)
        execute_target_pass(store, book, p, SETTINGS, Display(True), 'chunk1', 4, True)
        execute_target_pass(store, book, p, SETTINGS, Display(True), 'chunk1', 5, True)
        assert [b['pass_no'] for b in p.calls] == [2, 3, 4, 5, 4, 5]
        for body in p.calls:
            n = body['pass_no']
            suffix = json.loads(body['input'])
            assert ('SOURCE_SENTENCES' in suffix) == (n in (2, 4))
            assert ('SEMANTIC_AUDIT' in suffix) == (n in (3, 4))
            assert ('CORRECTION_LEDGER' in suffix) == (n == 5)
            assert len(body['injected_items']) == (3 if n in (4, 5) else 2)
            if n in (4, 5):
                ctx = messages.CodecContext.from_dict(body['codec_context'])
                draft = json.loads(body['injected_items'][2]['content'][0]['text'])['POLISH_DRAFT']
                assert messages.decode_output(draft, 3, ctx) == RESULTS[3]
    finally: store.close()


def test_message_plan_encodes_accepted_repaired_upstream_artifact(project):
    accepted = copy.deepcopy(RESULTS[2])
    accepted['issues'] = [{'sid':'S1', 'source_span':'Żuraw … waits', 'type':'idiom', 'meaning':'Meaning',
                           'constraint':'Keep', 'confidence':'high'}]
    from bookpipe.engine import conservative_repair
    accepted, repairs = conservative_repair(2, accepted, inputs_for(2))
    assert repairs and accepted['issues'][0]['source_span'] == 'Żuraw waits'
    validate_result(2, accepted, inputs_for(2))
    inputs = inputs_for(3)
    inputs['SEMANTIC_AUDIT'] = accepted
    layout, ctx = messages.encode_input(inputs, 3)
    assert messages.decode_input(layout, ctx)['SEMANTIC_AUDIT'] == accepted


@pytest.mark.parametrize('wire', ['canonical', 'compact-v1', 'cache-v1', 'cache-shared-v1'])
def test_old_p1_attempt_recoverable_into_messages_without_call(project, wire):
    from test_codex_cache import SyntheticCodex
    class PreviousP1(SyntheticCodex):
        def generate(self, body, directory, recorder):
            if body['wire_format'] != 'compact-v1':
                return super().generate(body, directory, recorder)
            self.calls.append(body)
            compact = old.encode_output(RESULTS[1], 1, old.context_for(inputs_for(1), 1))
            raw = encode_value({'t':compact['a'], 'o':compact['o']})
            recorder.transport_request(body, body['output_schema'])
            recorder.write_answer(raw)
            return raw, {'wire_format':wire, 'provider':'codex', 'finish_reason':'stop', 'usage_status':'reported'}
    store = Store(project)
    try:
        prior = OfflineShared(project) if wire == old.WIRE_FORMAT else PreviousP1(project, wire=wire)
        accepted = Runner(store, prior, SETTINGS, Display(True)).run(1, 'pass1/unit', inputs_for(1))
        with store.db:
            store.db.execute('DELETE FROM jobs'); store.db.execute('DELETE FROM kv')
        current = OfflineMessages(project)
        assert Runner(store, current, SETTINGS, Display(True)).run(1, 'pass1/unit', inputs_for(1)) == accepted
        assert not current.calls
    finally: store.close()


def test_effort_override_is_unchanged_with_messages_and_independent_roots(project):
    from test_codex_effort import inference, write_fake_codex
    executable = project/'effort-fake'
    write_fake_codex(executable)
    text = executable.read_text()
    text = text.replace("    elif method == 'turn/start':", """    elif method == 'thread/inject_items':
        assert params['threadId'] == thread_id
        for item in params['items']:
            assert item['type']=='message' and item['role']=='user'
            event('response_item', item)
        send({'id':rid,'result':{}})
    elif method == 'turn/start':""")
    executable.write_text(text)
    rows = [inference(project, n, effort, executable, wire=messages.WIRE_FORMAT)
            for n, effort in zip(range(1, 6), ('high','low','medium','low','medium'))]
    assert len({meta['thread_id'] for _,meta,_,_ in rows}) == 5
    for body, meta, plan, _ in rows:
        assert plan['thread']['config'] == {'model_reasoning_effort':'medium', 'features.reasoning_effort_override':True}
        assert plan['turn']['effort'] == meta['requested_effort'] == meta['reported_effort'] == body['effort']
        assert meta['request_effort_baseline'] == 'medium'
        assert meta['reasoning_effort_update_mode'] == 'configuration_update'
        assert meta['configuration_update_observed'] == (body['effort'] != 'medium')
        assert meta['provider_request_effort'] is None
        assert all(x['params']['threadId'] == meta['thread_id'] for x in plan['injections'])
