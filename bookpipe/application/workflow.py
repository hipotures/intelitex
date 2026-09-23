"""Detached workflow queries derived from project truth; never execute providers."""
from pathlib import Path

from ..profiles import resolve_profile, with_builtin_profiles, with_profiles
from ..util import PipelineError, digest
from ..usage import usage_by_unit_report
from .projects import load_valid_book
from .review import review_summary, approval_current
from ..processing import effective_book, configuration
from .sessions import ProjectReadScope


class WorkflowQueries:
    def __init__(self, dependencies, publishing):
        self.dependencies, self.publishing = dependencies, publishing

    def settings(self, root: Path | None = None) -> dict:
        files = self.dependencies.files
        local = root is not None and files.is_file(root / 'settings.json')
        # Read-only legacy adaptation: effective_settings would migrate on disk.
        raw = files.read_json(root / 'settings.json' if local else self.dependencies.bundle / 'settings.default.json')
        settings, _ = with_profiles(raw)
        config = configuration(root) if root else {'pass_profiles': {}, 'sections': {}}
        settings['pass_profiles'] = {**settings.get('pass_profiles', {}),
                                     **{k: v for k, v in config.get('pass_profiles', {}).items() if v}}
        effective = with_builtin_profiles(settings)
        fields = ('provider', 'model', 'enabled', 'context_size', 'reasoning_effort',
                  'planning_output_reserve', 'max_output_tokens')
        def profile(value):
            result = {key: value.get(key) for key in fields}
            result['enabled'] = value.get('enabled', True)
            result['thinking'] = value.get('options', {}).get('thinking')
            result['source_languages'] = value.get('source_languages')
            result['target_languages'] = value.get('target_languages')
            return result
        resolved = {}
        for number in range(1, 6):
            name, value, provenance = resolve_profile(settings, number, project=root)
            resolved[str(number)] = {'name': name, 'provenance': provenance['profile'], **profile(value)}
        # A configured draft has no web.config.json or book yet. Its saved settings
        # are the versioned authority for pre-Prepare profile changes.
        draft = local and not files.is_file(root / 'book.json')
        return {'source': 'project' if local else 'defaults', 'revision': digest(raw) if draft else digest(config),
                'assignments': {**settings.get('pass_profiles', {}), **config.get('pass_profiles', {})},
                'default_profile': settings['default_profile'],
                'pass_profiles': dict(settings.get('pass_profiles', {})),
                'profiles': [{'name': name, 'source': 'configured' if name in settings['profiles'] else 'builtin',
                              **profile(value)} for name, value in effective['profiles'].items()],
                'resolved_passes': resolved,
                'whole_section_char_limit': settings.get('whole_section_char_limit'),
                'memory_tokens': settings.get('memory_tokens')}

    def pipeline(self, root: Path, *, busy: bool = False) -> dict:
        return self.pipeline_with_evidence(root, busy=busy)[0]

    def pipeline_with_evidence(self, root: Path, *, busy: bool = False,
                               include_usage: bool = True,
                               include_publication_readiness: bool = True) -> tuple[dict, dict, object, dict]:
        """Build a checkpoint-validated snapshot; omit attempt history for compact summaries."""
        files = self.dependencies.files
        with ProjectReadScope(self.dependencies, root) as scope:
            store = scope.store
            source_book = load_valid_book(root, self.dependencies.plan_fingerprint, files)
            config = configuration(root)
            book = effective_book(source_book, root, config=config)
            analyzed, approved = bool(store.get('analysis_done')), approval_current(store, files)
            plan = files.read_json(root / 'analysis_plan.json') if files.is_file(root / 'analysis_plan.json') else []
            usage_report = usage_by_unit_report(root, book=source_book, plan=plan) if include_usage else None
            usage = {unit.unit_id: unit for unit in usage_report.units} if usage_report else {}
            analysis = []
            for unit in plan:
                receipt = store.get('analysis:' + unit['id'])
                state = 'pending'
                if receipt:
                    try:
                        state = 'completed' if store.job(receipt['key'], receipt['fingerprint']) else 'error'
                    except (PipelineError, OSError, ValueError):
                        state = 'error'
                attempt = next((p for p in usage[unit['id']].passes if p.pass_no == 1), None) if unit['id'] in usage else None
                analysis.append({'id': unit['id'], 'chapter_id': unit['chapter_id'], 'state': state,
                                 'attempt_result': attempt.result_status if attempt else None,
                                 'failed_attempt_count': attempt.failed_attempt_count if attempt else 0})
            checkpoints = store.checkpoint_inventory()
            chunks = []
            for chunk in book['chunks']:
                saved = store.chunk(chunk['id'])
                passes = {}
                historical = {p.pass_no: p for p in usage[chunk['id']].passes} if chunk['id'] in usage else {}
                for number in range(2, 6):
                    retained = checkpoints.get(f"pass{number}/{chunk['id']}", [])
                    # A retained checkpoint may have a prior input fingerprint.
                    # Only P5's registered final path authoritatively identifies current output.
                    state = 'retained' if retained else 'pending'
                    if number == 5 and saved['final_path']:
                        try:
                            store.checked_result(saved['final_path'])
                            state = 'completed' if saved['status'] == 'done' else 'stale'
                        except (PipelineError, OSError, ValueError):
                            state = 'error'
                    attempt = historical.get(number)
                    passes[str(number)] = {'checkpoint_state': state, 'retained_count': len(retained),
                                           'attempt_result': attempt.result_status if attempt else None,
                                           'failed_attempt_count': attempt.failed_attempt_count if attempt else 0}
                chunks.append({'id': chunk['id'], 'chapter_id': chunk['chapter_id'],
                               'status': saved['status'], 'passes': passes})
            review = files.read_json(root / 'terms.review.json') if files.is_file(root / 'terms.review.json') else None
            review_current = bool(review is not None and review.get('book_fingerprint') == book['source_fingerprint']
                                  and review.get('analysis_revision') == digest(store.terms()))
            confirmed = bool(review_current and review.get('confirmed') is True)
            complete = bool(chunks) and all(c['passes']['5']['checkpoint_state'] == 'completed' for c in chunks)
            publication = (self.publishing.query_snapshot(root, book, store, projected=True)
                           if include_publication_readiness else self.publishing.card_snapshot(
                               root, book, store, translation_complete=complete))
            stage = ('analysis' if not analyzed else 'review' if not approved else
                     'translation' if not complete else 'complete' if publication.current else 'publication')
            def action(reason=None):
                reason = 'workspace_busy' if busy else reason
                return {'allowed': reason is None, 'reason': reason}
            actions = {
                'analyze': action('analysis_complete' if analyzed else None),
                'prepare_review': action(None if analyzed else 'analysis_required'),
                'review': action(None if review_current else 'review_preparation_required'),
                'approve': action('analysis_required' if not analyzed else 'review_stale' if review and not review_current
                                  else 'review_not_confirmed' if not confirmed else None),
                'translate': action('analysis_required' if not analyzed else 'approval_required' if not approved
                                    else 'translation_complete' if complete else None),
                'publish': action('translation_required' if not complete else 'publication_current' if publication.current
                                  else 'publication_check_required' if publication.state == 'unchecked' and approved
                                  else 'publication_not_ready' if publication.state == 'not_ready' or not approved else None),
            }
            analysis_done = sum(u['state'] == 'completed' for u in analysis)
            translated = sum(all(p['checkpoint_state'] == 'completed' for n, p in u['passes'].items() if n == '5') for u in chunks)
            percent = (100 if publication.current else 32 if analyzed and not approved else 98 if complete else
                       int(35 + 58 * translated / len(chunks) + .5) if approved and chunks else
                       32 if analyzed else int(5 + 25 * analysis_done / len(analysis) + .5) if analysis else None)
            result = {'stage': stage, 'progress': {'percent': percent, 'basis': 'Workflow progress — not an ETA',
                        'analysis': {'completed': analysis_done, 'required': len(analysis),
                                     'denominator': 'required P1 analysis units'},
                        'translation': {'completed': translated, 'required': len(chunks),
                                        'denominator': 'required P5 translation units'}},
                    'analysis': {'complete': analyzed, 'planned': bool(plan), 'units': analysis,
                                 'membership_locked': any(k.startswith('pass1/') for k in checkpoints) or
                                 any(p.pass_no == 1 for u in usage.values() for p in u.passes)},
                    'review': {'prepared': review is not None, 'current': review_current,
                               'revision': digest(review) if review is not None else None,
                               'summary': review_summary(review) if review is not None else None},
                    'approved': approved, 'translation_complete': complete, 'units': chunks,
                    'chapters': [{'id': c['id'], 'title': c['title'], 'unit_ids': list(c['chunk_ids'])} for c in book['chapters']],
                    'excluded_sections': [{'id': c['id'], 'title': c.get('title'), 'role': c.get('role')}
                                          for c in book.get('non_narrative_sections', [])],
                    'publication': publication, 'actions': actions}
            return result, source_book, usage_report, config
