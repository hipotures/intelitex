"""Small web read models and reversible membership; checkpoints stay in Store."""
from datetime import datetime, timezone
from pathlib import Path

from ..util import PipelineError, digest
from .projects import load_valid_book


class LifecycleConflict(PipelineError):
    pass


class WorkspaceArchived(PipelineError):
    pass


class WebWorkspaceService:
    def __init__(self, dependencies):
        self.dependencies = dependencies

    def lifecycle(self, root):
        path = root / 'web.lifecycle.json'
        files = self.dependencies.files
        if path.is_symlink():
            raise PipelineError('Unsafe lifecycle metadata.')
        raw = files.read_json(path) if files.is_file(path) else {'archived': False}
        if not isinstance(raw, dict) or type(raw.get('archived')) is not bool:
            raise PipelineError('Invalid lifecycle metadata.')
        value = {'archived': raw['archived']}
        if isinstance(raw.get('updated_at'), str):
            value['updated_at'] = raw['updated_at']
        return {**value, 'revision': digest(raw)}

    def archive(self, root, archived, revision):
        with self.dependencies.project_lock(root):
            current = self.lifecycle(root)
            if revision != current['revision']:
                raise LifecycleConflict('Workspace membership changed.')
            if current['archived'] == archived:
                return current
            self.dependencies.files.write_json(root / 'web.lifecycle.json', {
                'archived': archived, 'updated_at': datetime.now(timezone.utc).isoformat(),
            })
            return self.lifecycle(root)

    def book(self, root):
        return load_valid_book(root, self.dependencies.plan_fingerprint, self.dependencies.files)

    def metadata(self, root):
        book = self.book(root)
        metadata = book.get('metadata', {})
        sections = self.sections(book)
        return {'title': metadata.get('title') or root.name,
                'creators': metadata.get('creators', []), 'language': metadata.get('language'),
                'format': 'EPUB' if metadata.get('opf') else 'HTML',
                'word_count': sum(len(b['text'].split()) for s in sections for b in s.get('blocks', [])),
                'lifecycle': self.lifecycle(root)}

    def preparation(self, root):
        from .ports import PublicationSourceFile
        book = self.book(root)  # validates frozen manifest before reporting success
        checks = ['Frozen source/chunk manifest verified']
        metadata = book.get('metadata', {})
        unavailable = None
        if metadata.get('opf'):
            try:
                self.dependencies.publication_builder.inspect(Path(book['source_root']), metadata['opf'],
                    tuple(PublicationSourceFile(path=f['file'], sha256=f['sha256'], encoding=f['encoding']) for f in book['files']))
                checks.extend(['EPUB container and package readable', 'Imported source file fingerprints verified'])
            except (PipelineError, OSError, ValueError):
                unavailable = 'Source package validation is unavailable; inspect the source locally.'
        else:
            unavailable = 'EPUB-specific checks are not applicable to this HTML source.'
        return {'checks': checks, 'unavailable': unavailable,
                'reading_order': metadata.get('order_method'), 'source_id': Path(book['source_root']).name}

    @staticmethod
    def sections(book):
        return sorted([*book['chapters'], *book.get('non_narrative_sections', [])],
                      key=lambda s: min((b['order'] for b in s.get('blocks', [])), default=0))

    def section_summaries(self, root, pipeline):
        from ..processing import processing
        book = self.book(root)
        config = self.config(root)
        result = []
        for ordinal, section in enumerate(self.sections(book), 1):
            ident = section['id']
            mode = processing(book, config, ident)
            eligible = mode != 'excluded'
            passes = {}
            for number in range(1, 6):
                if number == 1:
                    states = [u['state'] for u in pipeline['analysis']['units'] if u['chapter_id'] == ident]
                else:
                    states = [u['passes'][str(number)]['checkpoint_state'] for u in pipeline['units']
                              if u['chapter_id'] == ident]
                completed = states.count('completed')
                state = ('not_applicable' if not eligible or (number == 1 and mode == 'translate') else 'unknown' if not states else
                         'completed' if completed == len(states) else 'error' if 'error' in states else
                         'stale' if 'stale' in states else 'partial' if completed else
                         'retained' if 'retained' in states else 'pending')
                passes[str(number)] = {'state': state, 'completed': completed, 'required': len(states),
                                       'retained': states.count('retained')}
            result.append({'id': ident, 'ordinal': ordinal, 'title': section.get('title'),
                           'fallback_excerpt': ' '.join(b['text'] for b in section.get('blocks', []))[:250],
                           'content_type': config['sections'].get(ident, {}).get('content_type', section.get('role', 'unclassified')),
                           'processing': mode, 'profiles': config['sections'].get(ident, {}).get('profiles', {}), 'passes': passes})
        return result

    def preview(self, root, section_id, page=0):
        if type(page) is not int or page < 0:
            raise ValueError('Invalid preview page.')
        section = next((s for s in self.sections(self.book(root)) if s['id'] == section_id), None)
        if section is None:
            raise KeyError(section_id)
        # Bound response size even for a single unusually large source paragraph.
        blocks = [{'id': b['id'], 'text': b['text'][offset:offset + 8000]}
                  for b in section.get('blocks', []) for offset in range(0, len(b['text']), 8000)]
        start = page * 20
        return {'id': section_id, 'blocks': blocks[start:start + 20],
                'next_page': page + 1 if start + 20 < len(blocks) else None}

    def config(self, root):
        from ..processing import configuration
        value = configuration(root)
        return {**value, 'revision': digest(value)}

    def configure(self, root, payload, section_id=None):
        from ..processing import AnalysisMembershipLocked, ConfigConflict, ModelChangeRequired, effective_book, processing, reconcile_membership
        from ..profiles import resolve_profile
        from .sessions import OperationScope
        from ..usage import usage_by_unit_report
        with OperationScope(self.dependencies, root) as scope:
            config = self.config(root)
            if payload.get('revision') != config.pop('revision'):
                raise ConfigConflict('Configuration changed.')
            book = self.book(root)
            before = effective_book(book, root)
            if section_id is not None and section_id not in {s['id'] for s in self.sections(book)}:
                raise KeyError(section_id)
            target = config['sections'].setdefault(section_id, {}) if section_id else config
            reset_analysis = False
            mode = payload.get('processing')
            if mode is not None:
                if section_id is None or mode not in {'full', 'translate', 'excluded'}:
                    raise ValueError('Invalid processing mode.')
                old = processing(book, config, section_id)
                if old != mode:
                    attempted = any(p.pass_no == 1 for u in usage_by_unit_report(root).units for p in u.passes)
                    from .sessions import ProjectReadScope
                    with ProjectReadScope(self.dependencies, root) as read:
                        attempted |= any(key.startswith('pass1/') for key in read.store.checkpoint_inventory())
                    if attempted and ('full' in {old, mode}):
                        raise AnalysisMembershipLocked('P1 membership is frozen after its first attempt.')
                    target['processing'] = mode
                    if 'full' in {old, mode}:
                        reset_analysis = True
            if 'content_type' in payload:
                if section_id is None or payload['content_type'] not in {
                    'narrative', 'contents', 'glossary', 'footnotes', 'front_matter', 'back_matter', 'advertisement', 'unclassified'}:
                    raise ValueError('Invalid content type.')
                target['content_type'] = payload['content_type']
            key = 'profiles' if section_id else 'pass_profiles'
            if key in payload:
                profiles = payload[key]
                if not isinstance(profiles, dict) or set(profiles) - set('12345'):
                    raise ValueError('Invalid assignments.')
                raw = self.dependencies.files.read_json(root / 'settings.json')
                changed = False
                for number, name in profiles.items():
                    if name is not None:
                        if not isinstance(name, str):
                            raise ValueError('Invalid profile.')
                        resolve_profile(raw, int(number), command_profile=name, project=root)
                    changed |= target.get(key, {}).get(number) != name
                if changed and payload.get('allow_model_change') is not True:
                    raise ModelChangeRequired('Confirm model changes for future work; completed results are retained.')
                target.setdefault(key, {}).update(profiles)
                if changed:
                    config['accepted_settings_digest'] = digest(raw)
            if reset_analysis:
                plan = root / 'analysis_plan.json'
                if self.dependencies.files.is_file(plan):
                    previous = self.dependencies.files.read_json(plan)
                    self.dependencies.files.write_json(root / 'history' / f'analysis_plan_{digest(previous)}.json', previous)
                    plan.unlink()
                scope.store.reset_unattempted_analysis()
            self.dependencies.files.write_json(root / 'web.config.json', config)
            scope.store.register_chunks(effective_book(book, root, include_dormant=True))
            if mode is not None:
                reconcile_membership(scope.store, before, effective_book(book, root))
            return self.config(root)
