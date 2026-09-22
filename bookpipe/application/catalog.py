"""Root-owned source/draft linkage. Creating a draft never creates a project."""
import threading
import uuid

from ..util import atomic_json, digest, file_lock, read_json
from .imports import confined_source, validate_source_tree, RequestConflict
from ..importer import reading_order
from ..util import PipelineError


class WebCatalog:
    def __init__(self, root, imports):
        self.root, self.imports = root, imports
        self.path = root / '.intelitex-web.json'
        self.lock = threading.RLock()
        with self.lock, file_lock(root, '.intelitex-web.lock', 'Catalog busy.'):
            if not self.path.exists():
                atomic_json(self.path, {'scope_id': uuid.uuid4().hex, 'sources': {}})

    def read(self):
        if self.path.is_symlink():
            raise ValueError('Unsafe catalog.')
        return read_json(self.path)

    def draft(self, source_id, imported=(), request_key=None):
        if self.imports.root is None:
            from .imports import ImportDisabled
            raise ImportDisabled('Import disabled.')
        source = confined_source(self.imports.root, source_id)
        if not source.is_dir():
            raise KeyError(source_id)
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            state = self.read()
            requests = state.setdefault('requests', {})
            if request_key in requests and requests[request_key]['source_id'] != source_id:
                raise RequestConflict('Request key reused for a different source.')
            identity = digest(str(source))
            entry = state['sources'].get(identity)
            if entry is None:
                existing = next((ident for ident, canonical in imported if canonical == source), None)
                entry = {'workspace_id': existing or 'w-' + uuid.uuid4().hex, 'source_id': source_id,
                         'archived': False}
                state['sources'][identity] = entry
            if request_key:
                requests[request_key] = {'source_id': source_id, 'workspace_id': entry['workspace_id']}
            atomic_json(self.path, state)
            return dict(entry)

    def register_profiles(self, names):
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            state = self.read()
            colors = state.setdefault('profile_colors', {})
            changed = False
            for name in names:
                if name not in colors:
                    colors[name] = len(colors)
                    changed = True
            if changed:
                atomic_json(self.path, state)

    def source_metadata(self, source_id):
        if self.imports.root is None:
            return {'title': source_id, 'creators': [], 'language': None, 'word_count': None}
        try:
            source = confined_source(self.imports.root, source_id)
            validate_source_tree(source)
            _, metadata, _ = reading_order(source)
            title = metadata.get('title') or source.name
        except (OSError, ValueError, PipelineError):
            metadata, title = {}, source_id
        return {'title': title,
                'creators': metadata.get('creators', []), 'language': metadata.get('language'), 'word_count': None}

    def entries(self):
        return list(self.read()['sources'].values())

    def draft_lifecycle(self, workspace_id):
        entry = next((e for e in self.entries() if e['workspace_id'] == workspace_id), None)
        if entry is None:
            raise KeyError(workspace_id)
        return {'archived': entry.get('archived', False), 'revision': digest(entry)}

    def archive_draft(self, workspace_id, archived, revision):
        from .web import LifecycleConflict
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            state = self.read()
            entry = next((e for e in state['sources'].values() if e['workspace_id'] == workspace_id), None)
            if entry is None:
                raise KeyError(workspace_id)
            if revision != digest(entry):
                raise LifecycleConflict('Draft archive state changed.')
            if entry.get('archived', False) != archived:
                entry['archived'] = archived
                atomic_json(self.path, state)
            return {'archived': entry.get('archived', False), 'revision': digest(entry)}

    def source(self, workspace_id):
        entry = next((e for e in self.entries() if e['workspace_id'] == workspace_id), None)
        if entry is None:
            raise KeyError(workspace_id)
        return entry['source_id']

    def library(self):
        state = self.read()
        result = []
        if self.imports.root is None:
            return result
        for item in self.imports.sources():
            source = confined_source(self.imports.root, item['source_id'])
            entry = state['sources'].get(digest(str(source)))
            result.append({'source_id': item['source_id'], **self.source_metadata(item['source_id']),
                           'workspace_id': entry['workspace_id'] if entry else None})
        return result
