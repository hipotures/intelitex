"""Read-only P0 projection and a narrow, checkpoint-first preload command.

Projected targets are source/configuration identities, never native slot IDs.
Only the command constructs a provider or session manager. Native compatibility
and availability are checked on use; queries inspect workspace evidence only.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
from uuid import uuid4
import jsonschema

from .. import source_session_codec as codec
from ..engine import calculate_analysis_plan, source_blocks
from ..evidence import EvidenceError, AttemptRecorder, utc_now
from ..processing import configuration, effective_book, processing, ConfigConflict
from ..profiles import resolve_profile, with_profiles
from ..source_sessions import MODE, _submission_binding, validate_execution
from ..codex_effort import verify_reported_selection
from ..util import PipelineError, digest, read_json
from ..usage import usage_by_unit_report
from .checkpoints import translation_checkpoints
from .projects import load_valid_book
from .sessions import OperationScope, ProjectReadScope

TARGET = re.compile(r"pt_[0-9a-f]{64}")
REASONS = {
    'planning_unresolved': 'Exact analysis boundaries require unavailable local planning facts.',
    'legacy_mode': 'This workspace uses legacy execution; P0 is not required.',
    'not_applicable': 'No eligible Codex consumer requires source preload.',
    'saved_output': 'Eligible consumers are satisfied by their saved output.',
    'evidence_unverifiable': 'Saved P0 evidence is incomplete or inconsistent; inspect it locally.',
    'recovery_required': 'Session evidence requires local inspection before execution.',
    'preload_failed': 'An earlier P0 attempt failed; inspect its evidence before another request.',
    'accepted': 'Accepted P0 will be reused without a new model turn.',
}


def confined(root, path):
    """Reject symlinks in every evidence component, including in-workspace ones."""
    root = root.resolve()
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    if '..' in path.parts or not path.is_relative_to(root):
        raise EvidenceError('Unsafe workspace evidence binding.')
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise EvidenceError('Unsafe workspace evidence binding.')
    return path


class LocalCodexCounter:
    """Exactly the execution planner's local counter, without a client."""
    def __init__(self, profile):
        self.context = int(profile['context_size'])
        self.tokenizer_identity = {'provider': 'codex', 'model': profile['model'],
                                   'method': 'utf8_byte_upper_bound'}

    @staticmethod
    def count(text):
        return len(text.encode('utf-8'))


def compact_summary(targets, unresolved=0, *, retained=0, attempts=0):
    required = [t for t in targets if t['relevance'] == 'current']
    counts = {state: sum(t['baseline_state'] == state for t in required)
              for state in ('accepted', 'pending', 'running', 'failed', 'unverifiable')}
    state = ('running' if counts['running'] else 'error' if counts['failed'] else
             'unknown' if counts['unverifiable'] else
             'completed' if required and counts['accepted'] == len(required) and not unresolved else
             'partial' if counts['accepted'] else 'unknown' if unresolved else
             'pending' if required else 'not_applicable')
    return {'state': state, 'source_scopes': len({t['scope_id'] for t in required}),
            'required': len(required), 'accepted': counts['accepted'],
            'needing_execution': len(required) - counts['accepted'], **counts,
            'not_applicable': sum(t['relevance'] == 'satisfied' for t in targets),
            'unresolved': unresolved, 'retained': retained, 'physical_attempts': attempts,
            'session_warnings': sum(t['session_state'] not in ('not_created', 'clean') for t in required),
            'denominator': 'current compatible P0 session targets; unresolved planning groups counted separately'}


def source_size(blocks):
    return {'source_words': sum(len(b['text'].split()) for b in blocks),
            'source_utf8_bytes': sum(len(b['text'].encode('utf-8')) for b in blocks),
            'source_blocks': len(blocks)}


def saved_acceptance(root, store, record, scope):
    """Inspect R2's durable binding; never manufacture a verification receipt."""
    directory = confined(root, record['p0_attempt'])
    for name in ('attempt.json', 'request.semantic.json', 'request.transport.json', 'answer.txt',
                 'selection.verification.json', 'response_meta.json', 'native.history.json', 'usage.json'):
        confined(root, directory / name)
    owned = [s for s in record['submissions'] if s.get('attempt') == record['p0_attempt'] and s.get('pass_no') == 0]
    if len(owned) != 1:
        raise EvidenceError('Missing owned P0 intent.')
    binding = _submission_binding(root, record, owned[0])
    binding['answer_sha256'] = digest((directory / 'answer.txt').read_bytes())
    receipt = read_json(directory / 'selection.verification.json')
    if not isinstance(receipt, dict) or receipt.get('version') != 1 or receipt.get('status') != 'verified' or receipt.get('binding') != binding:
        raise EvidenceError('Unverified P0 selection.')
    verify_reported_selection(binding['requested_model'], binding['requested_effort'],
                              receipt.get('reported_model'), receipt.get('reported_effort'))
    if not receipt.get('reported_model') or binding['requested_effort'] is not None and receipt.get('reported_effort') is None:
        raise EvidenceError('Missing P0 selection evidence.')
    manifest = read_json(directory / 'attempt.json')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('lifecycle'), dict):
        raise EvidenceError('Invalid attempt lifecycle.')
    lifecycle = manifest['lifecycle']
    if (manifest.get('evidence_complete') is not True or lifecycle.get('generation') != 'completed'
            or lifecycle.get('validation') != 'passed' or lifecycle.get('acceptance') != 'checkpointed'):
        raise EvidenceError('P0 outcome is not checkpointed.')
    result_path = confined(root, directory.parent / 'result.json')
    saved = store.job('pass0/' + scope.scope_id, record['slot_id'])
    if not saved or root / saved['path'] != result_path:
        raise EvidenceError('P0 checkpoint binding unavailable.')
    result = saved['value']
    if (not isinstance(result, dict) or any(result.get(k) != v for k, v in scope.reference.items()) or result.get('ready') != codec.READY
            or result.get('thread_id') != record['thread_id'] or result.get('p0_turn_id') != record['p0_turn_id']
            or result.get('baseline_digest') != record['baseline_digest']):
        raise EvidenceError('P0 result disagrees with source/session.')
    meta = read_json(directory / 'response_meta.json')
    if (not isinstance(meta, dict) or meta.get('selection_verification') != receipt or meta.get('finish_reason') != 'stop'
            or any(meta.get(k) != receipt.get(k) for k in ('reported_model', 'reported_effort'))
            or any(meta.get(k) != record.get(k) for k in ('thread_id', 'session_id', 'slot_id'))
            or meta.get('turn_id') != record['p0_turn_id']):
        raise EvidenceError('P0 metadata disagrees with verified receipt.')
    history = read_json(directory / 'native.history.json')
    from ..source_sessions import PersistentSourceSessionManager as Manager
    text = codec.readiness_message(scope, confined(root, root / 'prompts/pass0.txt').read_text(encoding='utf-8'))
    if (not isinstance(history, list) or len(history) != 1 or not isinstance(history[0], dict)
            or history[0].get('id') != record['p0_turn_id'] or history[0].get('status') != 'completed'
            or Manager.user_text(history[0]) != [text]
            or codec.decode_output(json.loads(Manager.final_text(history[0]) or 'null'), 0) != codec.READY
            or digest({'turn_id': record['p0_turn_id'], 'source': text, 'ready': codec.READY}) != record['baseline_digest']):
        raise EvidenceError('Saved native baseline cannot prove P0.')
    return {'accepted_at': lifecycle.get('accepted_at'), 'selection_verified_at': receipt.get('verified_at'),
            'reported_model': receipt['reported_model'], 'reported_effort': receipt.get('reported_effort'),
            'acknowledgement': 'READY'}


class SourcePreloadQueries:
    def __init__(self, dependencies):
        self.dependencies = dependencies

    def settings(self, root):
        raw = read_json(confined(root, root / 'settings.json')) if (root / 'settings.json').is_file() else self.dependencies.files.read_json(self.dependencies.bundle / 'settings.default.json')
        return with_profiles(raw)[0]

    def inventory(self, root, **kwargs):
        with ProjectReadScope(self.dependencies, root) as scope:
            return self.build(root, scope.store, **kwargs)[0]

    def build(self, root, store, *, book=None, plan=None, usage=None):
        # The execution writer keeps ownership, while projection uses the same
        # read Store API as HTTP (latest/checkpoint readers are intentionally absent
        # from the mutable Store). No provider or second writer is opened.
        if not hasattr(store, 'latest_job'):
            with ProjectReadScope(self.dependencies, root) as reader:
                return self.build(root, reader.store, book=book, plan=plan, usage=usage)
        root = root.resolve()
        book = book if book is not None else load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files, readonly=True)
        config = configuration(root)
        settings = self.settings(root)
        applicable = validate_execution(settings)
        prompt_path = confined(root, root / 'prompts/pass0.txt')
        prompt = prompt_path.read_text(encoding='utf-8') if prompt_path.is_file() else None
        effective = effective_book(book, root, config=config)
        analysis_book = effective_book(book, root, phase='analysis', config=config)
        plan_path = confined(root, root / 'analysis_plan.json')
        planning = 'saved' if plan_path.is_file() else 'prospective'
        unresolved = []
        if plan is None:
            plan = read_json(plan_path) if plan_path.is_file() else None
        if plan is None:
            _, planner, _ = resolve_profile(settings, 1, command_pass_profiles={int(k): v for k, v in config['pass_profiles'].items() if v}, project=root)
            if planner['provider'] == 'codex':
                try:
                    plan = calculate_analysis_plan(analysis_book, LocalCodexCounter(planner), settings)
                except (PipelineError, ValueError, KeyError):
                    plan = None
            if plan is None:
                planning, plan = 'unresolved', []
                unresolved = [c for c in analysis_book['chapters'] if applicable and resolve_profile(
                    settings, 1, command_pass_profiles={**{int(k): v for k, v in config['pass_profiles'].items() if v},
                    **{int(k): v for k, v in config['sections'].get(c['id'], {}).get('profiles', {}).items() if v}},
                    project=root)[1]['provider'] == 'codex']
        usage = usage if usage is not None else usage_by_unit_report(root, book=book, plan=plan)
        recorded = []
        for path in sorted(root.glob('artifacts/*/sources/*/sessions/*/manifest.json')):
            try:
                record = read_json(confined(root, path))
                if (not isinstance(record, dict) or not isinstance(record.get('compatibility'), dict)
                        or not isinstance(record.get('scope'), dict) or not isinstance(record.get('submissions'), list)
                        or not all(isinstance(s, dict) for s in record['submissions'])
                        or record.get('active') is not None and not isinstance(record['active'], dict)):
                    raise ValueError('Invalid record')
                recorded.append((path, record))
            except (PipelineError, OSError, ValueError, TypeError):
                recorded.append((path, {'corrupt': True}))
        bindings, targets, matched = {}, {}, set()
        sections = sorted([*book['chapters'], *book.get('non_narrative_sections', [])],
                          key=lambda s: min((b['order'] for b in s.get('blocks', [])), default=0))
        chapter_scopes = {c['id']: codec.resolve_scope(source_blocks(c['blocks']), c['id'], book['source_fingerprint']).scope_id
                          for c in sections if c.get('blocks')}
        chunk_parts = {}
        for chapter in sections:
            chunks = [c for c in effective['chunks'] if c['chapter_id'] == chapter['id']]
            chunk_parts.update({c['id']: (n, len(chunks)) for n, c in enumerate(chunks, 1)})
        choices = {t['id']: t['choice'] for t in store.terms()}
        checkpoints = {c['id']: translation_checkpoints(root, store, c, choices) for c in effective['chunks']}
        defaults = []
        for number in range(1, 6):
            name, profile, _ = resolve_profile(settings, number, command_pass_profiles={int(k): v for k, v in config['pass_profiles'].items() if v}, project=root)
            defaults.append({'pass_no': number, 'profile': name, 'provider': profile['provider'],
                             'model': profile.get('model'), 'effort': profile.get('reasoning_effort')})

        def add(unit, number, saved):
            chapter = unit['chapter_id']
            overrides = {**{int(k): v for k, v in config['pass_profiles'].items() if v},
                         **{int(k): v for k, v in config['sections'].get(chapter, {}).get('profiles', {}).items() if v}}
            name, profile, _ = resolve_profile(settings, number, command_pass_profiles=overrides, project=root)
            if not applicable or profile['provider'] != 'codex':
                return
            scope = codec.resolve_scope(source_blocks(unit['blocks']), chapter, book['source_fingerprint'])
            compatibility = {'source': scope.reference, 'model': profile['model'], 'effort': profile.get('reasoning_effort'),
                             'auth_binding': profile.get('options', {}).get('auth_source'),
                             'base': digest(codec.BASE_INSTRUCTIONS), 'developer': digest(codec.DEVELOPER_INSTRUCTIONS),
                             'schema': digest(codec.TRANSPORT_SCHEMA), 'p0_prompt': digest(prompt) if prompt else None,
                             'sandbox': 'read-only', 'runtime_contract': 1}
            ident = 'pt_' + digest({'compatibility': compatibility,
                                    'executable_configuration': profile.get('executable') or 'codex',
                                    'runtime_configuration': profile.get('runtime_root')})
            if ident not in targets:
                part, parts = (unit.get('part', 1), unit.get('parts', 1)) if number == 1 else chunk_parts[unit['id']]
                label = 'Whole chapter' if chapter_scopes.get(chapter) == scope.scope_id else f'Part {part} of {parts}'
                row = {'target_id': ident, 'chapter_id': chapter, 'scope_id': scope.scope_id,
                       'source_sha256': scope.source_sha256, 'source_map_sha256': scope.reference['source_map_sha256'],
                       'label': label, **source_size(scope.canonical_blocks), 'planning_state': 'resolved', 'profiles': [], 'consumers': [],
                       'provider': 'codex', 'model': profile['model'], 'effort': profile.get('reasoning_effort'),
                       'baseline_state': 'pending', 'session_state': 'not_created', 'relevance': 'current',
                       'slot_id': None, 'generation': None, 'thread_id': None,
                       'accepted_at': None, 'selection_verified_at': None, 'last_verified_at': None,
                       'reported_model': None, 'reported_effort': None, 'acknowledgement': None,
                       'verification_scope': 'saved_evidence', 'native_check': 'on_use',
                       'can_run': bool(prompt), 'reason': None if prompt else 'evidence_unverifiable'}
                candidates = []
                for path, record in recorded:
                    compat = record.get('compatibility', {})
                    if all(compat.get(k) == v for k, v in compatibility.items()) and not record.get('retired'):
                        # The original frozen profile supplies configuration facts
                        # without resolving PATH, probing binaries or reading homes.
                        attempt = record.get('p0_attempt') or (record.get('active') or {}).get('attempt')
                        if attempt:
                            try:
                                saved_profile = read_json(confined(root, root / attempt / 'request.semantic.json'))['resolved_profile']
                                if ((saved_profile.get('executable') or 'codex') != (profile.get('executable') or 'codex')
                                        or saved_profile.get('runtime_root') != profile.get('runtime_root')):
                                    continue
                            except (PipelineError, OSError, ValueError, KeyError, TypeError):
                                pass  # Keep the candidate localized as unverifiable below.
                        candidates.append((path, record))
                if candidates:
                    current_candidates = [(p, r) for p, r in candidates if
                        store.get('source_slot_generation:' + str(r.get('compatibility_id')), 1) == r.get('generation')]
                    path, record = max(current_candidates or candidates, key=lambda pair: pair[1].get('generation') if type(pair[1].get('generation')) is int else 0)
                    matched.add(path)
                    bindings[ident] = (scope, profile, unit, number, record)
                    row.update({k: record.get(k) for k in ('slot_id', 'generation', 'thread_id', 'last_verified_at')})
                    active = record.get('active') or {}
                    row['session_state'] = ('recovery_required' if record.get('state') == 'recovery_required' else
                        'cleanup_pending' if record.get('cleanup_required') else
                        'consumer_active' if active.get('pass_no') in range(1, 6) else 'clean')
                    try:
                        compat = record['compatibility']
                        if (record.get('scope') != scope.reference or record.get('project_uuid') != store.get('source_project_uuid')
                                or record.get('compatibility_id') != digest(compat)
                                or record.get('slot_id') != digest({'compatibility': record['compatibility_id'], 'generation': record['generation']})
                                or store.get('source_slot_generation:' + record['compatibility_id'], 1) != record['generation']):
                            raise EvidenceError('Incompatible saved slot.')
                        source_root = path.parents[2]
                        if (read_json(confined(root, source_root / 'source.json')) != scope.package()
                                or read_json(confined(root, source_root / 'source-map.json')) != scope.source_map):
                            raise EvidenceError('Source binding changed.')
                        if record.get('p0_status') == 'ready':
                            row.update(saved_acceptance(root, store, record, scope), baseline_state='accepted')
                        elif active.get('pass_no') == 0:
                            row.update(baseline_state='unverifiable', can_run=False, reason='recovery_required')
                            # A completed, not-yet-accepted turn is recoverable only by the worker.
                            attempt = read_json(confined(root, root / active['attempt'] / 'attempt.json'))
                            if record.get('state') != 'recovery_required' and attempt.get('lifecycle', {}).get('generation') not in ('failed',):
                                row.update(can_run=True, reason=None)
                            else:
                                row.update(baseline_state='failed' if attempt.get('lifecycle', {}).get('generation') == 'failed' else 'unverifiable')
                            if active.get('terminal_status') in ('failed', 'interrupted'):
                                row.update(baseline_state='failed', can_run=False, reason='preload_failed')
                    except (PipelineError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, jsonschema.ValidationError):
                        row.update(baseline_state='unverifiable', can_run=False, reason='evidence_unverifiable')
                    if row['session_state'] in ('recovery_required', 'consumer_active', 'cleanup_pending'):
                        row.update(can_run=False, reason='recovery_required')
                    if len(current_candidates) > 1:
                        # Different recorded native contracts cannot be ranked by
                        # generation: each compatibility contract has its own counter.
                        matched.discard(path)
                        row.update(baseline_state='unverifiable', can_run=False, reason='evidence_unverifiable',
                                   slot_id=None, generation=None, thread_id=None, acknowledgement=None,
                                   reported_model=None, reported_effort=None, accepted_at=None,
                                   selection_verified_at=None)
                else:
                    bindings[ident] = (scope, profile, unit, number, None)
                    if any(record.get('corrupt') and path.parents[2].name == scope.scope_id for path, record in recorded):
                        row.update(baseline_state='unverifiable', can_run=False, reason='evidence_unverifiable')
                targets[ident] = row
            row = targets[ident]
            if name not in row['profiles']:
                row['profiles'].append(name)
            row['consumers'].append({'pass_no': number, 'unit_id': unit['id'], 'saved_output': saved})

        for unit in plan:
            if processing(book, config, unit['chapter_id']) != 'full':
                continue
            receipt = store.get('analysis:' + unit['id'])
            try:
                saved = bool(receipt and store.job(receipt['key'], receipt['fingerprint']))
            except (PipelineError, OSError, ValueError, KeyError, TypeError):
                saved = False
            add(unit, 1, saved)
        for chunk in effective['chunks']:
            for number in range(2, 6):
                add(chunk, number, checkpoints[chunk['id']][str(number)] == 'completed')
        rows = list(targets.values())
        for row in rows:
            if all(c['saved_output'] for c in row['consumers']) and row['baseline_state'] != 'accepted':
                row.update(relevance='satisfied', can_run=False, reason='saved_output')
            if row['baseline_state'] == 'accepted' and row['can_run']:
                row['reason'] = 'accepted'
        # Unresolved chapter previews never pretend to be exact executable scopes.
        for chapter in unresolved:
            ident = 'pt_' + digest({'unresolved': chapter['id'], 'source': book['source_fingerprint']})
            rows.append({'target_id': ident, 'chapter_id': chapter['id'], 'scope_id': None,
                         'source_sha256': None, 'source_map_sha256': None, 'label': 'Analysis planning unresolved',
                         **source_size(chapter['blocks']),
                         'planning_state': 'unresolved', 'profiles': [], 'consumers': [], 'provider': None,
                         'model': None, 'effort': None, 'baseline_state': 'unverifiable', 'session_state': 'not_created',
                         'relevance': 'unresolved', 'slot_id': None, 'generation': None, 'thread_id': None,
                         'accepted_at': None, 'selection_verified_at': None, 'last_verified_at': None,
                         'reported_model': None, 'reported_effort': None, 'acknowledgement': None,
                         'verification_scope': 'saved_evidence', 'native_check': 'on_use', 'can_run': False,
                         'reason': 'planning_unresolved'})
            bindings[ident] = (None, None, chapter, 1, None)
        history = []
        for path, record in recorded:
            if path in matched:
                continue
            history.append({'slot_id': record.get('slot_id'), 'scope_id': (record.get('scope') or {}).get('scope_id'),
                            'chapter_id': path.parts[-6], 'generation': record.get('generation'),
                            'model': record.get('compatibility', {}).get('model'),
                            'effort': record.get('compatibility', {}).get('effort'),
                            'state': 'unverifiable' if record.get('corrupt') else 'retired' if record.get('retired') else 'incompatible',
                            'reason': 'Retained evidence; excluded from current readiness.'})
        attempts = sum(p.physical_attempt_count for u in usage.units for p in u.passes if p.pass_no == 0)
        groups = []
        for chapter in sections:
            chapter_rows = [t for t in rows if t['chapter_id'] == chapter['id']]
            mode = processing(book, config, chapter['id'])
            reason = None if chapter_rows else (
                'Excluded from processing.' if mode == 'excluded' else
                'This workspace uses legacy execution without P0.' if not applicable else
                'No source content to preload.' if not chapter.get('blocks') else
                'The configured consumers do not use Codex source preload.')
            groups.append({'chapter_id': chapter['id'], 'title': chapter.get('title'),
                           'processing': mode, 'reason': reason, 'targets': chapter_rows,
                           'summary': compact_summary(chapter_rows, sum(t['relevance'] == 'unresolved' for t in chapter_rows))})
        # Usage values/timestamps do not change executable intent. Source/config,
        # membership, source contract and actual slot generation/hold do.
        intent = {'source': book['source_fingerprint'], 'config': config, 'settings': settings, 'plan': plan,
                  'prompt': digest(prompt) if prompt else None, 'contracts': [digest(codec.BASE_INSTRUCTIONS), digest(codec.DEVELOPER_INSTRUCTIONS), digest(codec.TRANSPORT_SCHEMA)],
                  'targets': [{k: t[k] for k in ('target_id', 'slot_id', 'generation', 'relevance', 'can_run')} for t in rows]}
        value = {'format_version': 1, 'execution_mode': MODE if applicable else 'legacy',
                 'applicable': applicable, 'planning_state': planning, 'prepared': True,
                 'observed_at': utc_now(), 'revision': digest(intent), 'intent_revision': digest(intent),
                 'reason': None if applicable else 'legacy_mode',
                 'summary': compact_summary(rows, len(unresolved), retained=len(history), attempts=attempts),
                 'chapters': groups, 'assignments': defaults, 'history': history[:100],
                 'history_truncated': len(history) > 100}
        return value, bindings

    def preview(self, root, target_id, page=0):
        if not isinstance(target_id, str) or not TARGET.fullmatch(target_id):
            raise ValueError('Invalid preload target.')
        if type(page) is not int or not 0 <= page <= 10000:
            raise ValueError('Invalid preview page.')
        with ProjectReadScope(self.dependencies, root) as scope:
            inventory, bindings = self.build(root, scope.store)
        row = next((t for c in inventory['chapters'] for t in c['targets'] if t['target_id'] == target_id), None)
        if not row:
            raise KeyError(target_id)
        source, _, unit, _, record = bindings[target_id]
        blocks = source.canonical_blocks if source else unit['blocks']
        kind, reason = 'planned', None
        if record:
            kind = 'recorded'
            try:
                path = root / 'artifacts' / row['chapter_id'] / 'sources' / row['scope_id'] / 'source.json'
                package = read_json(confined(root, path))
                if package != source.package():
                    raise EvidenceError('Invalid source')
                blocks = package['canonical_blocks']
            except (PipelineError, OSError, ValueError, KeyError, TypeError):
                blocks, reason = [], 'Source evidence unavailable; inspect the binding locally.'
        selected = blocks[page * 5:page * 5 + 5]
        return {'target_id': target_id, 'page': page, 'next_page': page + 1 if page * 5 + 5 < len(blocks) else None,
                'source_kind': kind, 'available': row['baseline_state'] == 'accepted', 'reason': reason,
                'source': [{'id': b['id'], 'text': b['text'][:20000]} for b in selected],
                'truncated': any(len(b['text']) > 20000 for b in selected), 'session': row}

    def run(self, command, progress):
        """One target, one possible P0 turn; no semantic consumer or plan writes."""
        from ..source_sessions import PersistentSourceSessionManager, scope_for
        from ..contracts import InferenceUnitContext
        with OperationScope(self.dependencies, command.project, progress) as scope:
            root, store = command.project.resolve(), scope.store
            lifecycle = root / 'web.lifecycle.json'
            if lifecycle.exists() and read_json(confined(root, lifecycle)).get('archived'):
                raise PipelineError('Restore workspace before preloading.')
            inventory, bindings = self.build(root, store)
            if command.expected_preload_revision != inventory['intent_revision']:
                raise ConfigConflict('Preload intent changed; refresh its source/configuration before running.')
            if command.preload_target_id not in bindings:
                raise KeyError(command.preload_target_id)
            row = next(t for c in inventory['chapters'] for t in c['targets'] if t['target_id'] == command.preload_target_id)
            if not row['can_run']:
                raise PipelineError(REASONS.get(row['reason'], 'Preload is unavailable for this target.'))
            source, profile, unit, number, recorded = bindings[command.preload_target_id]
            settings = self.settings(root)
            client = scope.providers(settings)
            if hasattr(client, 'select_section'):
                client.select_section(source.chapter_id)
            provider = client.for_pass(number)
            if provider.provider != 'codex' or provider.model != profile['model'] or provider.settings.get('reasoning_effort') != profile.get('reasoning_effort'):
                raise ConfigConflict('Resolved preload configuration changed.')
            source = scope_for(store, f'pass{number}/' + unit['id'], {'SOURCE_BLOCKS': source.canonical_blocks},
                               InferenceUnitContext(unit['id'], source.chapter_id))
            manager = PersistentSourceSessionManager(store, provider, source)
            provider.source_manager = manager
            if recorded and manager.slot_id != recorded['slot_id']:
                raise ConfigConflict('Native compatibility/generation changed; no preload submitted.')
            manager.consumer_pass = number
            manager.consumer_unit_id = unit['id']
            manager.acquire()
            try:
                recorder = AttemptRecorder(manager.directory / 'preload-controls' / uuid4().hex,
                                           {'operation': 'preload', 'model_turn': False})
                manager.connect(recorder)
                if manager.record.get('p0_status') == 'ready':
                    if manager.reconcile() == 'completed_unaccepted':
                        raise PipelineError('An unfinished consumer requires recovery before standalone preload.')
                else:
                    manager.ensure_p0()
                recorder.finish(generation='not_run', validation='not_applicable', metadata={'model_turn': False})
                from ..progress import ProgressEvent
                progress.emit(ProgressEvent(
                    kind='preload_target_completed', values=manager.preload_values()))
            finally:
                manager.close()
