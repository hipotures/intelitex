"""HTTP-independent control/query adapter; no pipeline execution in this process."""
from ..application.commands import ApproveCommand, UsageByUnitCommand
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

    def capabilities(self):
        return {'import_enabled': self.imports.root is not None, 'review': True, 'reader': True,
                'sse': True, 'multi_workspace': True}

    def workspace(self, workspace_id: str) -> dict:
        workspace = self.workspaces.status(workspace_id)
        active = self.supervisor.active_for_project(workspace.status.project)
        return {'workspace_id': workspace_id, 'status': dto.status(workspace.status),
                'active_job': active.public() if active else None}

    def list_workspaces(self) -> list[dict]:
        return [{'workspace_id': ident, 'active_job': job.public() if job else None}
                for ident in self.workspaces.list()
                for job in [self.supervisor.active_for_project(self.workspaces.resolve(ident))]]

    def start(self, workspace_id: str, payload: dict):
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
        return self.supervisor.start(spec).public()

    def import_book(self, payload):
        if self.imports.root is None:
            raise ImportDisabled('Web import disabled.')
        fields(payload, {'workspace_id', 'source_id', 'previous_volume', 'opf', 'input_encoding',
                         'chapter_mode', 'chapter_selector', 'include_glob', 'sidecar_txt',
                         'whole_section_limit', 'profile', 'pass_profiles', 'model', 'context_size', 'thinking'},
               {'workspace_id', 'source_id'})
        project = workspace_destination(self.workspaces.root, payload['workspace_id'])
        spec = ImportJobSpec(workspace_root=str(self.workspaces.root), project=str(project),
                             import_root=str(self.imports.root), **payload)
        return self.supervisor.start(spec).public()

    def pipeline(self, workspace_id):
        root = self.workspaces.resolve(workspace_id)
        active = self.supervisor.active_for_project(root)
        result = self.application.workflow.pipeline(root, busy=active is not None)
        result['publication'] = dto.publication(result['publication'])
        return {'workspace_id': workspace_id, **result, 'active_job': active.public() if active else None}

    def settings(self, workspace_id):
        return self.application.workflow.settings(self.workspaces.resolve(workspace_id))

    def usage(self, workspace_id: str):
        return dto.usage(self.application.operations.usage_by_unit(
            UsageByUnitCommand(self.workspaces.resolve(workspace_id))))

    def review(self, workspace_id, operation='load', payload=None, term_id=None):
        root = self.workspaces.resolve(workspace_id)
        service = self.application.review
        if operation == 'prepare':
            fields(payload, ())
            return dto.review(service.prepare(root))
        repository = service.repository(root)
        if operation == 'load':
            return dto.review(repository.load())
        if operation == 'evidence':
            return dto.evidence(repository.evidence(term_id))
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

    def approve(self, workspace_id, payload):
        fields(payload, {'revision'}, {'revision'})
        result = self.application.review.approve(ApproveCommand(
            self.workspaces.resolve(workspace_id), expected_revision=revision(payload)))
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
