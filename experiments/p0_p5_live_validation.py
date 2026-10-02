#!/usr/bin/env python3
"""Explicit, bounded production-runner verification in a fresh scratch workspace.

Never imported by pytest. Authentication is copied only with --authorize-auth.
The RPC submission counter is persisted before every physical turn, including P0.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bookpipe.bootstrap import create_application
from bookpipe.application.commands import ImportBookCommand, AnalyzeCommand, TranslateCommand, ApproveCommand
from bookpipe.application.pipeline import ReloadAtCheckpoint
from bookpipe import codex_transport, source_sessions
from bookpipe.util import atomic_json, read_json


class Sink:
    reload_requested = False
    def emit(self, event):
        if event.kind in ('source_preload_completed', 'pass_recovered', 'analysis_completed'):
            print(event.kind, dict(event.values), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--authorize-auth', action='store_true', required=True)
    parser.add_argument('--max-calls', type=int, default=16, choices=range(1, 17))
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    scratch.mkdir(mode=0o700, exist_ok=False)
    source, project, runtime = scratch/'source', scratch/'project', scratch/'runtime'
    source.mkdir()
    (source/'01.html').write_text('<html><body><p>' + 'Mara waited beside the silent ship. "The gate is open," she said.\n\nThe copper key felt cold in her hand.</p></body></html>')
    (source/'02.html').write_text('<html><body><p>' + 'Mara returned at dawn. She held the copper key against the light.\n\nNobody answered her question.</p></body></html>')
    (source/'03.html').write_text('<html><body><p>' + 'The river carried a thin sheet of ice. Mara followed the bank until she reached the old bridge.</p><hr/><p>Beyond the bridge, a watchman raised a lantern. "Keep the key," he said. The dark road led into the hills.</p></body></html>')
    sink = Sink()
    app = create_application(sink)
    app.projects.import_book(ImportBookCommand(project, source, local_token_estimate=True,
        profile='codex-sol-high', whole_section_limit=120))
    settings = read_json(project/'settings.json')
    settings['default_profile'] = 'codex-sol-high'
    settings['pipeline_execution'] = source_sessions.DEFAULT_EXECUTION.copy()
    settings['json_retries'] = 1
    from bookpipe.profiles import builtin_codex_profiles
    settings['profiles']['codex-sol-high'] = builtin_codex_profiles()['codex-sol-high']
    settings['profiles']['codex-sol-high']['runtime_root'] = str(runtime)
    settings['profiles']['codex-sol-high']['request_timeout'] = 180
    settings['profiles']['codex-sol-high']['options']['auth_source'] = '~/.codex/auth.json'
    atomic_json(project/'settings.json', settings)
    counter = {'max_calls': args.max_calls, 'calls': []}
    request = codex_transport._RpcSession.request
    def bounded(rpc, method, params, deadline):
        if method == 'turn/start':
            if len(counter['calls']) >= args.max_calls:
                raise RuntimeError('Explicit physical live-call budget exhausted; no further model inference.')
            envelope = json.loads(params['input'][0]['text'])
            counter['calls'].append({'pass': envelope['ACTIVE_PASS'], 'scope': envelope['SOURCE_REF'], 'thread_id': params['threadId']})
            atomic_json(scratch/'physical_calls.json', counter)
        return request(rpc, method, params, deadline)
    codex_transport._RpcSession.request = bounded
    # Stop after first accepted P0, then construct a fresh app/Store/ProviderPool.
    original = source_sessions.PersistentSourceSessionManager.ensure_p0
    stopped = False
    def stop_after_p0(manager):
        nonlocal stopped
        original(manager)
        if not stopped:
            stopped = True
            raise ReloadAtCheckpoint(0)
    source_sessions.PersistentSourceSessionManager.ensure_p0 = stop_after_p0
    try:
        app.pipeline.analyze(AnalyzeCommand(project, profile='codex-sol-high'))
    except ReloadAtCheckpoint:
        pass
    finally:
        source_sessions.PersistentSourceSessionManager.ensure_p0 = original
    assert len(counter['calls']) == 1
    app = create_application(sink)
    app.pipeline.analyze(AnalyzeCommand(project, profile='codex-sol-high'))
    try:
        app.pipeline.translate(TranslateCommand(project, chunk_limit=1, profile='codex-sol-high'))
    except Exception as exc:
        assert 'approve' in str(exc) or 'terminology' in str(exc)
    else:
        raise AssertionError('Review gate was bypassed')
    # Deliberate human-equivalent fixture approval, authorized solely for scratch data.
    app.review.approve(ApproveCommand(project, accept_defaults=True))
    book = read_json(project/'book.json')
    chunks = book['chunks']
    primary = chunks[0]['id']
    split = next(c['id'] for c in chunks if c['chapter_id'] == book['chapters'][2]['id'])
    # Accepted intermediate output survives a process/resource restart.
    app.pipeline.translate(TranslateCommand(project, profile='codex-sol-high', chunk_id=primary, pass_no=2))
    app = create_application(sink)
    # Fail validation once AFTER a real complete response, then exercise the
    # production bounded retry. The raw original answer remains in evidence.
    original_decode = __import__('bookpipe.engine', fromlist=['_decode_transport_result'])._decode_transport_result
    engine = __import__('bookpipe.engine', fromlist=['_decode_transport_result'])
    failed_once = False
    def validation_fault(value, wire_format, inputs, codec_context=None, *, expected_pass=None):
        nonlocal failed_once
        canonical = original_decode(value, wire_format, inputs, codec_context, expected_pass=expected_pass)
        if expected_pass == 3 and not failed_once:
            failed_once = True
            canonical = {'translations': []}
        return canonical
    engine._decode_transport_result = validation_fault
    try:
        app.pipeline.translate(TranslateCommand(project, profile='codex-sol-high', chunk_id=primary, pass_no=3))
    finally:
        engine._decode_transport_result = original_decode
    # Interruption of local finalization, after accepted P4, followed by cleanup-only resume.
    original_cleanup = source_sessions.PersistentSourceSessionManager.cleanup
    interrupted = False
    def interrupt_cleanup(manager):
        nonlocal interrupted
        if (manager.record.get('active') or {}).get('pass_no') == 4 and not interrupted:
            interrupted = True
            raise KeyboardInterrupt('Explicit scratch recovery injection after accepted P4')
        return original_cleanup(manager)
    source_sessions.PersistentSourceSessionManager.cleanup = interrupt_cleanup
    try:
        app.pipeline.translate(TranslateCommand(project, profile='codex-sol-high', chunk_id=primary, pass_no=4))
    except KeyboardInterrupt:
        pass
    finally:
        source_sessions.PersistentSourceSessionManager.cleanup = original_cleanup
    # A targeted P5 reads accepted prerequisites without inference. Its manager
    # reconciles accepted P4 and cleans the suffix before its own submission.
    app = create_application(sink)
    app.pipeline.translate(TranslateCommand(project, profile='codex-sol-high', chunk_id=primary, pass_no=5))
    for n in range(2, 6):
        app = create_application(sink)
        app.pipeline.translate(TranslateCommand(project, profile='codex-sol-high', chunk_id=split, pass_no=n))
    manifests = [read_json(p) for p in project.glob('artifacts/*/sources/*/sessions/*/manifest.json')]
    assert all(m['state'] == 'baseline_ready' and m['retained_active_turns'] == 1 for m in manifests)
    assert len(counter['calls']) <= args.max_calls
    inventory = read_json(project/'source_sessions.json')
    assert inventory['tasks']['pass1/'+book['chapters'][0]['id']+'_a001'] == inventory['tasks']['pass2/'+primary]
    assert inventory['tasks']['pass1/'+book['chapters'][2]['id']+'_a001'] != inventory['tasks']['pass2/'+split]
    records = []
    for p in sorted(project.glob('artifacts/**/attempt_*/usage.json')):
        meta = read_json(p.parent/'response_meta.json')
        attempt = read_json(p.parent/'attempt.json')
        records.append({'attempt': str(p.parent.relative_to(project)), 'pass': attempt['identity']['pass_no'],
                        'usage': read_json(p), 'elapsed_seconds': meta.get('elapsed_seconds'),
                        'source_bytes_added': meta.get('source_message_utf8_bytes_added'),
                        'retained_source_bytes': meta.get('retained_source_utf8_bytes'),
                        'dynamic_bytes': meta.get('dynamic_suffix_utf8_bytes'),
                        'full_context': read_json(p.parent/'context.json') if (p.parent/'context.json').is_file() else None,
                        'thread_id': meta.get('thread_id'), 'turn_id': meta.get('turn_id'),
                        'reported_model': meta.get('reported_model'), 'reported_effort': meta.get('reported_effort')})
    atomic_json(scratch/'summary.json', {'status': 'passed', 'calls': len(counter['calls']), 'records': records,
        'scopes': [{k: m.get(k) for k in ('scope', 'slot_id', 'thread_id', 'session_id', 'p0_turn_id', 'state', 'retained_active_turns')} for m in manifests],
        'profile_switch_live': 'not run within 16-call bound; covered offline',
        'interrupt_case': 'after accepted P4 before revert; canonical acceptance recovered without another P4 call'})
    print('Live validation passed:', scratch, 'physical calls:', len(counter['calls']), flush=True)


if __name__ == '__main__':
    main()
