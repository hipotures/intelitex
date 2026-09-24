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

    def metadata(self, root, *, book=None):
        from .workspace_setup import read_workspace_setup
        book = book if book is not None else self.book(root)
        metadata = book.get('metadata', {})
        sections = self.sections(book)
        setup = read_workspace_setup(root)
        return {'title': metadata.get('title') or root.name,
                'creators': metadata.get('creators', []), 'language': metadata.get('language'),
                'label': setup.get('label'), 'source_language': setup.get('source_language'),
                'target_language': setup.get('target_language'),
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
                'reading_order': metadata.get('order_method'), 'source_id': Path(book.get('source_archive', book['source_root'])).name}

    @staticmethod
    def sections(book):
        return sorted([*book['chapters'], *book.get('non_narrative_sections', [])],
                      key=lambda s: min((b['order'] for b in s.get('blocks', [])), default=0))

    def section_summaries(self, root, pipeline, *, book=None, config=None):
        from ..processing import processing
        book = book if book is not None else self.book(root)
        config = config if config is not None else self.config(root)
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

    def translation_pass_preview(self, root, chunk_id, pass_no, page=0, status=None):
        """Bounded, validated saved pass output for one eligible translation chunk."""
        from ..processing import effective_book
        from .sessions import ProjectReadScope
        if pass_no not in (2, 3, 4, 5):
            raise ValueError('Invalid translation pass.')
        if type(page) is not int or page < 0 or page > 10000:
            raise ValueError('Invalid preview page.')
        allowed_statuses = {2: {'low', 'medium', 'high', 'attention'}, 4: {'ok', 'needs_correction'}}
        if status is not None and status not in allowed_statuses.get(pass_no, set()):
            raise ValueError('Invalid preview status.')
        book = effective_book(self.book(root), root)
        chunk = next((item for item in book['chunks'] if item['id'] == chunk_id), None)
        if chunk is None:
            raise KeyError(chunk_id)
        key = f'pass{pass_no}/{chunk_id}'
        with ProjectReadScope(self.dependencies, root) as scope:
            store = scope.store
            selected = store.get('selected_pass:' + key)
            final_path = store.chunk(chunk_id)['final_path'] if pass_no == 5 else None
            saved = (store.job_for_path(key, final_path) if final_path else None)
            if not saved and isinstance(selected, dict) and isinstance(selected.get('fingerprint'), str):
                saved = store.job(key, selected['fingerprint'])
            if not saved:
                saved = store.latest_job(key)
            current = pass_no == 5 and store.chunk(chunk_id)['status'] == 'done' and saved is not None and store.chunk(chunk_id)['final_path'] == saved['path']
            value = saved['value'] if saved else {}
            matching_ids = ({item['sid'] for item in value.get('checks', [])
                             if (item['risk'] in {'medium', 'high'} if status == 'attention'
                                 else item['risk' if pass_no == 2 else 'status'] == status)}
                            if status is not None else None)
            all_sentences = ([item for item in chunk['sentences'] if item['id'] in matching_ids]
                             if matching_ids is not None else chunk['sentences'])
            matching_blocks = {item['block_id'] for item in all_sentences}
            all_blocks = ([item for item in chunk['blocks'] if item['id'] in matching_blocks]
                          if status is not None else chunk['blocks'])
            page_size = 5
            start = page * page_size
            blocks = all_blocks[start:start + page_size]
            block_ids = {item['id'] for item in blocks}
            sentences = [item for item in all_sentences if item['block_id'] in block_ids]
            sentence_ids = {item['id'] for item in sentences}
            next_page = page + 1 if start + page_size < len(all_blocks) else None
            text_limit = 20000
            source = [{'id': item['id'], 'text': item['text'][:text_limit]} for item in blocks]
            source_truncated = any(len(item['text']) > text_limit for item in blocks)
            sentence_preview = [{'id': item['id'], 'block_id': item['block_id'], 'text': item['text'][:text_limit]}
                                for item in sentences]
            sentence_truncated = any(len(item['text']) > text_limit for item in sentences)
            if not saved:
                return {'chunk_id': chunk_id, 'pass_no': pass_no, 'page': page, 'next_page': next_page,
                        'available': False, 'current': False, 'source': source, 'sentences': sentence_preview,
                        'translations': [], 'checks': [], 'findings': [],
                        'truncated': source_truncated or sentence_truncated}
            raw_translations = [item for item in value.get('translations', []) if item['id'] in block_ids]
            translations = [{'id': item['id'], 'text': item['text'][:text_limit]}
                            for item in raw_translations]
            raw_checks = [item for item in value.get('checks', []) if item['sid'] in sentence_ids]
            raw_findings = [item for item in (value.get('issues', []) if pass_no == 2 else value.get('corrections', []))
                            if item['sid'] in sentence_ids]
            def bounded(rows):
                return [{field: item[:text_limit] if isinstance(item, str) else item
                         for field, item in row.items()} for row in rows]
            checks, findings = bounded(raw_checks), bounded(raw_findings)
            return {'chunk_id': chunk_id, 'pass_no': pass_no, 'page': page, 'next_page': next_page,
                    'available': True, 'current': current, 'source': source, 'sentences': sentence_preview, 'translations': translations,
                    'checks': checks, 'findings': findings,
                    'truncated': source_truncated or sentence_truncated
                    or any(len(item['text']) > text_limit for item in raw_translations)
                    or any(len(item) > text_limit for row in [*raw_checks, *raw_findings]
                           for item in row.values() if isinstance(item, str))}

    def config(self, root, *, config=None):
        from ..processing import configuration
        value = config if config is not None else configuration(root)
        return {**value, 'revision': digest(value)}

    def configure(self, root, payload, section_id=None):
        from ..processing import AnalysisMembershipLocked, ConfigConflict, ModelChangeRequired, effective_book, processing, reconcile_membership
        from ..profiles import resolve_profile
        from .sessions import OperationScope
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
                    attempted = scope.store.has_p1_attempt()
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
