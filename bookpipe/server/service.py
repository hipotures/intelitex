"""HTTP-independent control/query adapter; no pipeline execution in this process."""
from ..application.commands import ApproveCommand, UsageByUnitCommand
from ..application.catalog import WebCatalog
from ..application.source_preflight import inspect_source
from ..application.web import WorkspaceArchived
from ..runtime.supervisor import JobConflict
from ..util import digest
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
import re
from ..application.imports import ImportDisabled, ImportQueries, confined_source, workspace_destination
from ..runtime.models import ImportJobSpec, JobSpec
from . import serialization as dto


def fields(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or set(required) - set(payload):
        raise ValueError('Invalid request fields.')


def revision(payload):
    value = payload.get('revision')
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a review/marker revision.')
    return value


class ServerService:
    def __init__(self, application, workspaces, supervisor, *, import_root=None):
        self.application = application
        self.workspaces = workspaces
        self.supervisor = supervisor
        self.imports = ImportQueries(import_root)
        self.catalog = WebCatalog(workspaces.root, self.imports)
        roots = [None, *(self.workspaces.resolve(i) for i in self.workspaces.list())]
        self.catalog.register_profiles(p['name'] for root in roots for p in self.application.workflow.settings(root)['profiles'])

    def last_job(self, ident):
        resets = self.workspaces.root / ident / 'history' / 'p1_resets'
        reset_time = max((item.stat().st_mtime for item in resets.glob('*/completed.json')), default=None)
        for job in reversed(self.supervisor.list()):
            if job.workspace_id != ident:
                continue
            if job.operation == 'analyze' and reset_time is not None and job.started_at:
                try:
                    if datetime.fromisoformat(job.started_at).timestamp() <= reset_time:
                        continue
                except ValueError:
                    pass
            return job.public()
        return None

    def capabilities(self):
        return {'import_enabled': self.imports.root is not None, 'review': True, 'reader': True,
                'sse': True, 'multi_workspace': True, 'scope_id': self.catalog.read()['scope_id'],
                'drafts': True, 'archive': True, 'section_configuration': True, 'diagnostics': False}

    def workspace(self, workspace_id: str) -> dict:
        workspace = self.workspaces.status(workspace_id)
        active = self.supervisor.active_for_project(workspace.status.project)
        return {'workspace_id': workspace_id, 'metadata': self.application.web.metadata(workspace.status.project), 'status': dto.status(workspace.status),
                'active_job': active.public() if active else None}

    def list_workspaces(self, archived: bool | None = None) -> list[dict]:
        result = []
        imported = self.workspaces.list()
        for ident in imported:
            root = self.workspaces.resolve(ident)
            if archived is not None and self.application.web.lifecycle(root)['archived'] != archived:
                continue
            active = self.supervisor.active_for_project(root)
            from ..application.workspace_setup import read_workspace_setup
            setup = read_workspace_setup(root)
            source_id = setup.get('source_id')
            book = None
            if source_id is None and self.imports.root is not None:
                book = self.application.web.book(root)
                path = Path(book.get('source_archive', book['source_root'])).resolve()
                if path.parent == self.imports.root:
                    source_id = path.name
            result.append({'workspace_id': ident, 'prepared': True,
                           **({'source_id': source_id} if source_id else {}),
                           'metadata': self.application.web.metadata(root, book=book),
                           'active_job': active.public() if active else None, 'last_job': self.last_job(ident)})
        for entry in self.catalog.entries():
            if entry['workspace_id'] not in imported:
                if archived is not None and entry.get('archived', False) != archived:
                    continue
                active = self.supervisor.active_for_project(self.workspaces.root / entry['workspace_id'])
                manifest = self.catalog.setup_metadata(entry['workspace_id'])
                result.append({'workspace_id': entry['workspace_id'], 'source_id': entry['source_id'],
                               'prepared': False, 'metadata': {**self.catalog.source_metadata(entry['source_id']),
                               'label': manifest.get('label'), 'source_language': manifest.get('source_language'),
                               'target_language': manifest.get('target_language'),
                               'lifecycle': self.catalog.draft_lifecycle(entry['workspace_id'])},
                               'progress': {'percent': None, 'basis': 'Workflow progress — not an ETA',
                                            'analysis': {'completed': 0, 'required': 0,
                                                         'denominator': 'required P1 analysis units'},
                                            'translation': {'completed': 0, 'required': 0,
                                                            'denominator': 'required P5 translation units'}},
                               'active_job': active.public() if active else None, 'last_job': self.last_job(entry['workspace_id'])})
        return result

    @contextmanager
    def mutable(self, ident):
        root = self.workspaces.resolve(ident)
        with self.supervisor.lock:
            if self.supervisor.owns_project(root) or self.supervisor.active_for_project(root):
                raise JobConflict('Workspace busy.')
            if self.application.web.lifecycle(root)['archived']:
                raise WorkspaceArchived('Restore before changing this workspace.')
            yield root

    def archive(self, ident, payload, archived=True):
        fields(payload, {'revision'}, {'revision'})
        with self.supervisor.lock:
            try:
                root = self.workspaces.resolve(ident)
            except KeyError:
                root = workspace_destination(self.workspaces.root, ident)
                if self.supervisor.owns_project(root) or self.supervisor.active_for_project(root):
                    raise JobConflict('Workspace busy.')
                return self.catalog.archive_draft(ident, archived, revision(payload))
            if self.supervisor.owns_project(root) or self.supervisor.active_for_project(root):
                raise JobConflict('Workspace busy.')
            return self.application.web.archive(root, archived, revision(payload))

    def configure(self, ident, payload, section_id=None):
        fields(payload, {'revision', 'processing', 'content_type', 'profiles', 'allow_model_change'} if section_id
               else {'revision', 'pass_profiles', 'allow_model_change'}, {'revision'})
        if 'allow_model_change' in payload and type(payload['allow_model_change']) is not bool:
            raise ValueError('Invalid model permission.')
        try:
            self.workspaces.resolve(ident)
        except KeyError:
            if section_id is not None or 'pass_profiles' not in payload:
                raise
            with self.supervisor.lock:
                root = workspace_destination(self.workspaces.root, ident)
                if self.supervisor.owns_project(root) or self.supervisor.active_for_project(root):
                    raise JobConflict('Workspace busy.')
                return self.catalog.configure_draft_profiles(
                    ident, revision(payload), payload['pass_profiles'], payload.get('allow_model_change', False))
        with self.mutable(ident) as root:
            return self.application.web.configure(root, payload, section_id)

    def analysis_reset_status(self, ident):
        root = self.workspaces.resolve(ident)
        result = self.application.analysis_reset.status(root)
        active = self.supervisor.active_for_project(root)
        if active or self.supervisor.owns_project(root):
            result.update(can_reset=False, reason='workspace_busy')
        elif self.application.web.lifecycle(root)['archived']:
            result.update(can_reset=False, reason='workspace_archived')
        return result

    def reset_analysis(self, ident, payload):
        fields(payload, {'revision'}, {'revision'})
        with self.mutable(ident) as root:
            return self.application.analysis_reset.reset(root, revision(payload))

    def create_draft(self, payload):
        fields(payload, {'source_id', 'request_key'}, {'source_id'})
        key = payload.get('request_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,128}', key)):
            raise ValueError('Invalid request key.')
        # Existing imported projects are linked by canonical source identity, not title.
        imported = [(ident, Path((book := self.application.web.book(self.workspaces.resolve(ident))).get('source_archive', book['source_root'])).resolve())
                    for ident in self.workspaces.list()]
        return self.catalog.draft(payload['source_id'], imported, request_key=key)

    def inspect_source(self, ident, *, detailed=False):
        if self.imports.root is None:
            raise ImportDisabled('Import disabled.')
        return inspect_source(self.imports.root, ident, detailed=detailed)

    def compatibility(self, payload):
        fields(payload, {'source_language', 'target_language', 'pass_profiles'},
               {'source_language', 'target_language', 'pass_profiles'})
        for language in (payload['source_language'], payload['target_language']):
            if not isinstance(language, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language):
                raise ValueError('Invalid language.')
        settings = self.application.workflow.settings()
        profiles = {p['name']: p for p in settings['profiles']}
        assignments = payload['pass_profiles']
        if not isinstance(assignments, dict) or set(assignments) != set('12345'):
            raise ValueError('Select all five pass profiles.')
        warnings = []
        warned_unknown = set()
        # Current prompts, Review schema and Reader accept English -> Polish only.
        # Profile capability metadata cannot expand the application's language pair.
        compatible = payload['source_language'].lower() == 'en' and payload['target_language'].lower() == 'pl'
        if payload['source_language'].lower() != 'en':
            warnings.append('This translation pipeline currently requires an English source.')
        if payload['target_language'].lower() != 'pl':
            warnings.append('This translation pipeline currently produces Polish output.')
        targets = {'pl'}
        for number, name in assignments.items():
            if not isinstance(name, str) or name not in profiles or not profiles[name]['enabled']:
                raise ValueError('Invalid profile.')
            profile = profiles[name]
            for field, language in (('source_languages', payload['source_language']),
                                    ('target_languages', payload['target_language'])):
                support = profile.get(field)
                if support is None:
                    if (name, field) not in warned_unknown:
                        warnings.append(f'{field.replace("_", " ").capitalize()} are not declared for {name}.')
                        warned_unknown.add((name, field))
                elif support != 'all' and language.lower() not in {v.lower() for v in support}:
                    warnings.append(f'Pass {number}: {name} does not declare support for {language}.')
                    compatible = False
            support = profile.get('target_languages')
            if isinstance(support, list):
                values = {v.lower() for v in support}
                targets &= values
        return {'compatible': compatible, 'warnings': warnings,
                'target_choices': sorted(targets)}

    def save_setup(self, payload):
        fields(payload, {'source_id', 'source_fingerprint', 'source_language', 'target_language',
                         'label', 'pass_profiles', 'request_key'},
               {'source_id', 'source_fingerprint', 'source_language', 'target_language',
                'label', 'pass_profiles', 'request_key'})
        result = self.compatibility({key: payload[key] for key in ('source_language', 'target_language', 'pass_profiles')})
        if not result['compatible']:
            raise ValueError('Selected model profiles do not support this language pair.')
        raw = self.application.web.dependencies.files.read_json(self.application.web.dependencies.bundle / 'settings.default.json')
        return self.catalog.save_setup(payload, raw)

    def library(self):
        sources = self.catalog.library()
        imported = {str(Path((book := self.application.web.book(self.workspaces.resolve(i))).get('source_archive', book['source_root'])).resolve()): i for i in self.workspaces.list()}
        if self.imports.root:
            for source in sources:
                source['workspace_id'] = source['workspace_id'] or imported.get(str((self.imports.root / source['source_id']).resolve()))
        return {'sources': sources, 'configured': self.imports.root is not None}

    def library_page(self, query):
        if set(query) - {'after', 'limit', 'links'}:
            raise ValueError('Invalid Library query.')
        links = query.get('links', 'true')
        if links not in {'true', 'false'}:
            raise ValueError('Invalid Library link mode.')
        raw_limit = query.get('limit', '24')
        if not isinstance(raw_limit, str) or not raw_limit.isdecimal():
            raise ValueError('Invalid Library page size.')
        limit = int(raw_limit)
        if not 1 <= limit <= 40:
            raise ValueError('Invalid Library page size.')
        after = query.get('after')
        if after is not None and (not isinstance(after, str) or not after or len(after) > 255
                                  or '/' in after or '\\' in after or '\x00' in after):
            raise ValueError('Invalid Library cursor.')
        sources, next_cursor = self.catalog.library_page(after, limit)
        imported = ({str(Path((book := self.application.web.book(self.workspaces.resolve(i))).get('source_archive', book['source_root'])).resolve()): i for i in self.workspaces.list()}
                    if links == 'true' else {})
        if self.imports.root and links == 'true':
            for source in sources:
                source['workspace_id'] = source['workspace_id'] or imported.get(str((self.imports.root / source['source_id']).resolve()))
        return {'sources': sources, 'configured': self.imports.root is not None, 'next_cursor': next_cursor}

    def prepare(self, ident, payload):
        fields(payload, {'request_key', 'profile', 'pass_profiles'})
        if self.catalog.draft_lifecycle(ident)['archived']:
            raise WorkspaceArchived('Restore draft before preparing.')
        manifest = self.catalog.setup_metadata(ident)
        if manifest:
            if payload.get('profile') or payload.get('pass_profiles'):
                raise ValueError('Configured draft profiles cannot be overridden at Prepare.')
            from ..application.source_preflight import source_signature
            if source_signature(self.imports.root, manifest['source_id']) != manifest['source_fingerprint']:
                raise ValueError('Source changed since setup; create a new workspace.')
        return self.import_book({'workspace_id': ident, 'source_id': self.catalog.source(ident), **payload})

    def reprepare(self, ident, payload):
        with self.supervisor.lock:
            payload, key, fingerprint, previous = self.receipt(payload, f'reprepare:{ident}')
            if previous is not None:
                return previous.public()
            fields(payload, {'revision'}, {'revision'})
            if self.imports.root is None:
                raise ImportDisabled('Web import disabled.')
            root = self.workspaces.resolve(ident)
            if self.application.web.lifecycle(root)['archived']:
                raise WorkspaceArchived('Restore before preparing again.')
            from ..application.workspace_setup import read_workspace_setup
            from ..processing import ConfigConflict
            setup = read_workspace_setup(root)
            if self.application.web.config(root)['revision'] != revision(payload):
                raise ConfigConflict('Configuration changed.')
            if setup:
                source_id = setup['source_id']
            else:
                book = self.application.web.book(root)
                recorded = Path(book.get('source_archive', book['source_root'])).resolve()
                if recorded.parent != self.imports.root:
                    raise ValueError('This workspace has no matching Library source.')
                source_id = recorded.name
            self.application.projects.check_reprepare(
                root, confined_source(self.imports.root, source_id), payload['revision'])
            spec = ImportJobSpec(workspace_root=str(self.workspaces.root), workspace_id=ident,
                                 project=str(root), import_root=str(self.imports.root),
                                 source_id=source_id, reprepare=True,
                                 expected_revision=payload['revision'])
            return self.supervisor.start(spec, request_key=key,
                                         request_fingerprint=fingerprint).public()

    def receipt(self, payload, operation):
        payload = dict(payload)
        key = payload.pop('request_key', None)
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,128}', key)):
            raise ValueError('Invalid request key.')
        fingerprint = digest({'operation': operation, 'payload': payload})
        previous = self.supervisor.registry.request(self.supervisor.workspace_root, key, fingerprint) if key else None
        return payload, key, fingerprint, previous

    def start(self, workspace_id: str, payload: dict):
        with self.supervisor.lock:
            return self._start(workspace_id, payload)

    def _start(self, workspace_id: str, payload: dict):
        payload, key, fingerprint, previous = self.receipt(payload, workspace_id)
        if previous is not None:
            return previous.public()
        fields(payload, {'operation', 'profile', 'chunk_limit', 'target_language'}, {'operation'})
        operation = payload['operation']
        if operation != 'translate' and 'chunk_limit' in payload:
            raise ValueError('chunk_limit only applies to translate.')
        if operation != 'publish' and 'target_language' in payload:
            raise ValueError('target_language only applies to publish.')
        if operation == 'publish' and 'profile' in payload:
            raise ValueError('profile does not apply to publish.')
        spec = JobSpec(str(self.workspaces.root), workspace_id,
                       str(self.workspaces.resolve(workspace_id)), **payload)
        with self.mutable(workspace_id):
            return self.supervisor.start(spec, request_key=key, request_fingerprint=fingerprint).public()

    def import_book(self, payload):
        with self.supervisor.lock:
            return self._import_book(payload)

    def _import_book(self, payload):
        payload, key, fingerprint, previous = self.receipt(payload, 'import')
        if previous is not None:
            return previous.public()
        if self.imports.root is None:
            raise ImportDisabled('Web import disabled.')
        fields(payload, {'workspace_id', 'source_id', 'previous_volume', 'opf', 'input_encoding',
                         'chapter_mode', 'chapter_selector', 'include_glob', 'sidecar_txt',
                         'whole_section_limit', 'profile', 'pass_profiles', 'model', 'context_size', 'thinking'},
               {'workspace_id', 'source_id'})
        project = workspace_destination(self.workspaces.root, payload['workspace_id'])
        if any(e['workspace_id'] == payload['workspace_id'] and e.get('archived', False)
               for e in self.catalog.entries()):
            raise WorkspaceArchived('Restore draft before importing.')
        setup = self.catalog.setup_metadata(payload['workspace_id'])
        if setup and (payload['source_id'] != setup['source_id'] or set(payload) != {'workspace_id', 'source_id'}):
            raise ValueError('Configured draft import must use its saved source and setup.')
        spec = ImportJobSpec(workspace_root=str(self.workspaces.root), project=str(project),
                             import_root=str(self.imports.root), **payload)
        return self.supervisor.start(spec, request_key=key, request_fingerprint=fingerprint).public()

    def pipeline(self, workspace_id):
        root = self.workspaces.resolve(workspace_id)
        active = self.supervisor.active_for_project(root)
        busy = active is not None or self.supervisor.owns_project(root)
        result, book, usage, config_snapshot = self.application.workflow.pipeline_with_evidence(root, busy=busy)
        result['publication'] = dto.publication(result['publication'])
        config = self.application.web.config(root, config=config_snapshot)
        result['sections'] = self.application.web.section_summaries(root, result, book=book, config=config)
        colors = self.catalog.read().get('profile_colors', {})
        by_section = {}
        for unit in usage.units:
            for attempt in unit.passes:
                by_section.setdefault((unit.chapter_id, attempt.pass_no), set()).add(
                    (attempt.profile, attempt.provider, attempt.reported_model or attempt.requested_model))
        for section in result['sections']:
            for number, cell in section['passes'].items():
                provenance = by_section.get((section['id'], int(number)), set())
                cell['provenance'] = [{'profile': profile, 'provider': provider, 'model': model,
                        'stable_palette_index': colors.get(profile)} for profile, provider, model in sorted(provenance, key=str)]
        result['config'] = config
        result['metadata'] = self.application.web.metadata(root, book=book)
        result['artifacts'] = {name: self.application.web.dependencies.files.is_file(root / filename) for name, filename in [('terminology', 'terms.review.json'), ('book_memory', 'book_memory.json')]}
        result['preparation'] = {'source_id': Path(book.get('source_archive', book['source_root'])).name, 'checks': ['Frozen source/chunk manifest verified']}
        if result['metadata']['lifecycle']['archived']:
            for value in result['actions'].values():
                value.update(allowed=False, reason='workspace_archived')
        events = self.supervisor.registry.recent_events(workspace_root=self.supervisor.workspace_root,
                    workspace_id=workspace_id, job_id=active.job_id, limit=120) if active else []
        # Runtime activity decorates cells without changing durable checkpoint counts.
        boundaries = [e['event'] for e in events if e['event']['kind'] in {
            'pass_started', 'analysis_progress', 'analysis_unit_progress', 'translation_progress', 'translation_unit_progress', 'publication_started'}]
        if active and active.state == 'running' and boundaries and boundaries[-1]['kind'] == 'pass_started':
            current = boundaries[-1]['values']
            section = next((s for s in result['sections'] if s['id'] == current.get('chapter_id')), None)
            if section and str(current.get('pass_no')) in section['passes']:
                section['passes'][str(current['pass_no'])]['runtime_state'] = 'running'
        publication_events = [e for e in events if e['event']['kind'].startswith('publication_')]
        publishing = bool(active and (active.operation == 'publish' or
                          publication_events and publication_events[-1]['event']['kind'] == 'publication_started'))
        return {'workspace_id': workspace_id, **result, 'publishing': publishing, 'busy': busy,
                'active_job': active.public() if active else None, 'last_job': self.last_job(workspace_id)}

    def pipeline_summary(self, workspace_id):
        """Work-card state without scanning physical attempt history or section details."""
        root = self.workspaces.resolve(workspace_id)
        active = self.supervisor.active_for_project(root)
        busy = active is not None or self.supervisor.owns_project(root)
        result, book, _, _ = self.application.workflow.pipeline_with_evidence(
            root, busy=busy, include_usage=False, include_publication_readiness=False)
        metadata = self.application.web.metadata(root, book=book)
        if metadata['lifecycle']['archived']:
            for value in result['actions'].values():
                value.update(allowed=False, reason='workspace_archived')
        events = self.supervisor.registry.recent_events(
            workspace_root=self.supervisor.workspace_root, workspace_id=workspace_id,
            job_id=active.job_id, limit=120) if active else []
        publication_events = [event for event in events if event['event']['kind'].startswith('publication_')]
        publishing = bool(active and (active.operation == 'publish' or
                          publication_events and publication_events[-1]['event']['kind'] == 'publication_started'))
        publication = dto.publication(result['publication'])
        return {'workspace_id': workspace_id, 'stage': result['stage'], 'progress': result['progress'],
                'analysis': {'complete': result['analysis']['complete']}, 'approved': result['approved'],
                'publication': {'current': publication['current'], 'last_failure': publication['last_failure']},
                'actions': result['actions'], 'metadata': {'lifecycle': metadata['lifecycle']},
                'publishing': publishing, 'active_job': active.public() if active else None,
                'last_job': self.last_job(workspace_id)}

    def settings(self, workspace_id):
        try:
            root = self.workspaces.resolve(workspace_id)
        except KeyError:
            self.catalog.source(workspace_id)
            candidate = workspace_destination(self.workspaces.root, workspace_id)
            root = candidate if (candidate / 'workspace.json').is_file() else None
        return self.profiles(root)

    def profiles(self, root=None):
        result = self.application.workflow.settings(root)
        colors = self.catalog.read().get('profile_colors', {})
        for value in [*result['profiles'], *result['resolved_passes'].values()]:
            value['stable_palette_index'] = colors.get(value['name'])
        return result

    def usage(self, workspace_id: str):
        return dto.usage(self.application.operations.usage_by_unit(
            UsageByUnitCommand(self.workspaces.resolve(workspace_id))))

    def review(self, workspace_id, operation='load', payload=None, term_id=None):
        if operation in {'load', 'evidence'}:
            return self._review(workspace_id, operation, payload, term_id)
        with self.mutable(workspace_id):
            return self._review(workspace_id, operation, payload, term_id)

    def _review(self, workspace_id, operation='load', payload=None, term_id=None):
        root = self.workspaces.resolve(workspace_id)
        service = self.application.review
        if operation == 'prepare':
            fields(payload, ())
            return dto.review(service.prepare(root))
        if operation == 'load':
            return dto.review(service.query(root))
        if operation == 'evidence':
            return dto.evidence(service.query(root, term_id))
        repository = service.repository(root)
        expected = revision(payload)
        if operation == 'patch':
            fields(payload, {'revision', 'select', 'custom', 'reviewed', 'user_notes'})
            value = repository.patch_term(term_id, {k: v for k, v in payload.items() if k != 'revision'}, expected)
        elif operation == 'bulk':
            fields(payload, {'revision', 'term_ids'}, {'term_ids'})
            value = repository.review_terms(payload['term_ids'], expected)
        elif operation == 'confirmation':
            fields(payload, {'revision', 'confirmed'}, {'confirmed'})
            value = repository.set_confirmed(payload['confirmed'], expected)
        else:
            raise ValueError('Unknown review operation.')
        return dto.review(value)

    def approve(self, workspace_id, payload, *, confirm_review=False):
        fields(payload, {'revision'}, {'revision'})
        with self.mutable(workspace_id) as root:
            result = self.application.review.approve(ApproveCommand(
                root, expected_revision=revision(payload)), confirm_review=confirm_review)
        return {'approved_terms': result.approved_terms, 'stale_chunks': result.stale_chunks,
                'pipeline': self.pipeline(workspace_id)}

    def reader(self, workspace_id, operation='metadata', payload=None, identifier=None):
        root = self.workspaces.resolve(workspace_id)
        service = self.application.reader
        if operation == 'markers':
            return dto.markers(service.query(root, 'markers'))
        if operation in {'create', 'delete'}:
            allowed = {'revision', 'chapter_id', 'block_id', 'start', 'end', 'text'} if operation == 'create' else {'revision'}
            fields(payload, allowed, allowed)
            value = {k: v for k, v in payload.items() if k != 'revision'} if operation == 'create' else identifier
            return dto.markers(service.mutate_marker(root, operation, value, revision(payload)))
        args = ()
        if operation == 'context':
            fields(payload, {'chapter_id', 'block_id', 'position'}, {'chapter_id', 'block_id', 'position'})
            args = (payload['chapter_id'], payload['block_id'], payload['position'])
        elif operation == 'chapter':
            args = (identifier,)
        return dto.reader(service.query(root, operation, *args), operation)

    def publication_resource(self, ident):
        return self.application.publishing.download(self.workspaces.resolve(ident))

    def activity(self, ident):
        self.workspaces.resolve(ident)
        events = self.supervisor.registry.recent_events(workspace_root=self.supervisor.workspace_root, workspace_id=ident)
        return {'events': events[-120:]}
