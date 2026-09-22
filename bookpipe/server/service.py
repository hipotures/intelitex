"""HTTP-independent control/query adapter; no pipeline execution in this process."""
from ..application.commands import ApproveCommand, UsageByUnitCommand
from ..application.catalog import WebCatalog
from ..application.web import WorkspaceArchived
from ..runtime.supervisor import JobConflict
from ..util import digest
from pathlib import Path
from contextlib import contextmanager
import re
from ..application.imports import ImportDisabled, ImportQueries, workspace_destination
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
        return next((j.public() for j in reversed(self.supervisor.list()) if j.workspace_id == ident), None)

    def capabilities(self):
        return {'import_enabled': self.imports.root is not None, 'review': True, 'reader': True,
                'sse': True, 'multi_workspace': True, 'scope_id': self.catalog.read()['scope_id'],
                'drafts': True, 'archive': True, 'section_configuration': True, 'diagnostics': False}

    def workspace(self, workspace_id: str) -> dict:
        workspace = self.workspaces.status(workspace_id)
        active = self.supervisor.active_for_project(workspace.status.project)
        return {'workspace_id': workspace_id, 'metadata': self.application.web.metadata(workspace.status.project), 'status': dto.status(workspace.status),
                'active_job': active.public() if active else None}

    def list_workspaces(self) -> list[dict]:
        result = []
        imported = self.workspaces.list()
        for ident in imported:
            root = self.workspaces.resolve(ident)
            active = self.supervisor.active_for_project(root)
            result.append({'workspace_id': ident, 'prepared': True,
                           'metadata': self.application.web.metadata(root),
                           'active_job': active.public() if active else None, 'last_job': self.last_job(ident)})
        for entry in self.catalog.entries():
            if entry['workspace_id'] not in imported:
                active = self.supervisor.active_for_project(self.workspaces.root / entry['workspace_id'])
                result.append({'workspace_id': entry['workspace_id'], 'source_id': entry['source_id'],
                               'prepared': False, 'metadata': {**self.catalog.source_metadata(entry['source_id']),
                               'lifecycle': self.catalog.draft_lifecycle(entry['workspace_id'])},
                               'progress': {'percent': 0, 'basis': 'Workflow progress — not an ETA',
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
        with self.mutable(ident) as root:
            return self.application.web.configure(root, payload, section_id)

    def create_draft(self, payload):
        fields(payload, {'source_id', 'request_key'}, {'source_id'})
        key = payload.get('request_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,128}', key)):
            raise ValueError('Invalid request key.')
        # Existing imported projects are linked by canonical source identity, not title.
        imported = [(ident, Path((book := self.application.web.book(self.workspaces.resolve(ident))).get('source_archive', book['source_root'])).resolve())
                    for ident in self.workspaces.list()]
        return self.catalog.draft(payload['source_id'], imported, request_key=key)

    def library(self):
        sources = self.catalog.library()
        imported = {str(Path((book := self.application.web.book(self.workspaces.resolve(i))).get('source_archive', book['source_root'])).resolve()): i for i in self.workspaces.list()}
        if self.imports.root:
            for source in sources:
                source['workspace_id'] = source['workspace_id'] or imported.get(str((self.imports.root / source['source_id']).resolve()))
        return {'sources': sources, 'configured': self.imports.root is not None}

    def library_page(self, query):
        if set(query) - {'after', 'limit'}:
            raise ValueError('Invalid Library query.')
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
        imported = {str(Path((book := self.application.web.book(self.workspaces.resolve(i))).get('source_archive', book['source_root'])).resolve()): i for i in self.workspaces.list()}
        if self.imports.root:
            for source in sources:
                source['workspace_id'] = source['workspace_id'] or imported.get(str((self.imports.root / source['source_id']).resolve()))
        return {'sources': sources, 'configured': self.imports.root is not None, 'next_cursor': next_cursor}

    def prepare(self, ident, payload):
        fields(payload, {'request_key', 'profile', 'pass_profiles'})
        if self.catalog.draft_lifecycle(ident)['archived']:
            raise WorkspaceArchived('Restore draft before preparing.')
        return self.import_book({'workspace_id': ident, 'source_id': self.catalog.source(ident), **payload})

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
        spec = ImportJobSpec(workspace_root=str(self.workspaces.root), project=str(project),
                             import_root=str(self.imports.root), **payload)
        return self.supervisor.start(spec, request_key=key, request_fingerprint=fingerprint).public()

    def pipeline(self, workspace_id):
        root = self.workspaces.resolve(workspace_id)
        active = self.supervisor.active_for_project(root)
        busy = active is not None or self.supervisor.owns_project(root)
        result = self.application.workflow.pipeline(root, busy=busy)
        result['publication'] = dto.publication(result['publication'])
        result['sections'] = self.application.web.section_summaries(root, result)
        usage = self.usage(workspace_id)
        colors = self.catalog.read().get('profile_colors', {})
        for section in result['sections']:
            for number, cell in section['passes'].items():
                actual = [p for unit in usage['units'] if unit['chapter_id'] == section['id']
                          for p in unit['passes'] if p['pass_no'] == int(number)]
                provenance = {(p['profile'], p['provider'], p['reported_model'] or p['requested_model']) for p in actual}
                cell['provenance'] = [{'profile': profile, 'provider': provider, 'model': model,
                        'stable_palette_index': colors.get(profile)} for profile, provider, model in sorted(provenance, key=str)]
        result['config'] = self.application.web.config(root)
        result['metadata'] = self.application.web.metadata(root)
        result['artifacts'] = {name: self.application.web.dependencies.files.is_file(root / filename) for name, filename in [('terminology', 'terms.review.json'), ('book_memory', 'book_memory.json')]}
        book = self.application.web.book(root)
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

    def settings(self, workspace_id):
        return self.profiles(self.workspaces.resolve(workspace_id))

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
