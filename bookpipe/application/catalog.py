"""Root-owned source/draft linkage. Creating a draft never creates a project."""
import threading
import uuid
import re
from datetime import datetime, timezone

from ..util import atomic_json, digest, file_lock, project_lock, read_json
from .imports import confined_source, RequestConflict
from ..util import PipelineError
from .epub_sources import packed_epub_metadata
from .source_preflight import source_signature, _folder_metadata
from .imports import workspace_destination
from ..profiles import resolve_profile, with_profiles
import zipfile


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
        if not source.is_dir() and not (source.is_file() and source.suffix.lower() == '.epub' and zipfile.is_zipfile(source)):
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

    def save_setup(self, payload, settings):
        """Commit one configured workspace. A request key identifies one Save, not one source."""
        if self.imports.root is None:
            from .imports import ImportDisabled
            raise ImportDisabled('Import disabled.')
        expected = {'source_id', 'source_fingerprint', 'source_language', 'target_language', 'label', 'pass_profiles', 'request_key'}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError('Invalid workspace setup.')
        source_id, key = payload['source_id'], payload['request_key']
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,128}', key):
            raise ValueError('Invalid request key.')
        label = payload['label']
        if label is not None and (not isinstance(label, str) or len(label.strip()) > 64 or not label.strip() or any(ord(c) < 32 for c in label)):
            raise ValueError('Invalid workspace label.')
        for field in ('source_language', 'target_language'):
            value = payload[field]
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', value):
                raise ValueError('Invalid language.')
        profiles = payload['pass_profiles']
        if not isinstance(profiles, dict) or set(profiles) != set('12345'):
            raise ValueError('Select all five pass profiles.')
        configured, _ = with_profiles(settings)
        for number, name in profiles.items():
            if not isinstance(name, str):
                raise ValueError('Invalid profile.')
            resolve_profile(configured, int(number), command_profile=name)
        setup_digest = digest(payload)
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            state = self.read()
            requests = state.setdefault('setup_requests', {})
            prior = requests.get(key)
            if prior is None:
                from .workspace_setup import read_workspace_setup
                for folder in self.root.iterdir():
                    manifest = folder / 'workspace.json'
                    if folder.is_dir() and manifest.is_file() and not manifest.is_symlink():
                        try:
                            value = read_workspace_setup(folder)
                        except (OSError, ValueError, PipelineError):
                            continue
                        if value.get('request_key') == key:
                            prior = {'digest': value.get('setup_digest'), 'workspace_id': folder.name}
                            break
            if prior is not None:
                if prior['digest'] != setup_digest:
                    raise RequestConflict('Request key reused for different setup.')
                if key not in requests:
                    state.setdefault('drafts', {})[prior['workspace_id']] = {
                        'workspace_id': prior['workspace_id'], 'source_id': source_id, 'archived': False}
                    requests[key] = prior
                    atomic_json(self.path, state)
                return {'workspace_id': prior['workspace_id'], 'source_id': source_id}
            # Revalidate the selected source at Save; Prepare verifies it again.
            fingerprint = source_signature(self.imports.root, source_id)
            if payload['source_fingerprint'] != fingerprint:
                raise RequestConflict('Source changed since setup preflight.')
            ident = 'w-' + uuid.uuid4().hex
            root = workspace_destination(self.root, ident)
            root.mkdir(mode=0o700)
            try:
                configured['pass_profiles'] = dict(profiles)
                atomic_json(root / 'settings.json', configured)
                metadata = {'format_version': 1, 'source_id': source_id, 'source_fingerprint': fingerprint,
                            'label': label.strip() if label else None,
                            'source_language': payload['source_language'].lower(),
                            'target_language': payload['target_language'].lower(),
                            'created_at': datetime.now(timezone.utc).isoformat(),
                            'request_key': key, 'setup_digest': setup_digest}
                atomic_json(root / 'workspace.json', metadata)
                entry = {'workspace_id': ident, 'source_id': source_id, 'archived': False}
                state.setdefault('drafts', {})[ident] = entry
                requests[key] = {'digest': setup_digest, 'workspace_id': ident}
                atomic_json(self.path, state)
            except BaseException:
                # Only remove our exact unpublished setup files; never touch another project.
                for name in ('workspace.json', 'settings.json'):
                    (root / name).unlink(missing_ok=True)
                root.rmdir()
                raise
            return {'workspace_id': ident, 'source_id': source_id}

    def configure_draft_profiles(self, workspace_id, revision, updates, allow_model_change=False):
        """Change saved pass assignments before import, with a settings revision."""
        from ..processing import ConfigConflict, ModelChangeRequired
        from .web import WorkspaceArchived
        from .workspace_setup import validate_draft_destination

        if not isinstance(updates, dict) or not updates or set(updates) - set('12345'):
            raise ValueError('Invalid pass profiles.')
        if any(not isinstance(name, str) for name in updates.values()):
            raise ValueError('Invalid profile.')
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            if self.draft_lifecycle(workspace_id)['archived']:
                raise WorkspaceArchived('Restore this draft before editing.')
            source_id = self.source(workspace_id)
            root = workspace_destination(self.root, workspace_id)
            with project_lock(root):
                setup = validate_draft_destination(root, source_id)
                settings_path = root / 'settings.json'
                raw = read_json(settings_path)
                if revision != digest(raw):
                    raise ConfigConflict('Configuration changed.')
                configured, _ = with_profiles(raw)
                assignments = dict(configured.get('pass_profiles', {}))
                changed = any(assignments.get(number) != name for number, name in updates.items())
                if changed and allow_model_change is not True:
                    raise ModelChangeRequired('Confirm model changes for future work.')
                assignments.update(updates)
                configured['pass_profiles'] = assignments
                for number in range(1, 6):
                    name, profile, _ = resolve_profile(configured, number, project=root)
                    for field, language in (('source_languages', setup['source_language']),
                                            ('target_languages', setup['target_language'])):
                        support = profile.get(field)
                        if support is not None and support != 'all' and language.lower() not in {v.lower() for v in support}:
                            raise ValueError(f'Pass {number}: {name} does not support {language}.')
                if changed:
                    atomic_json(settings_path, configured)
                return {'revision': digest(configured if changed else raw), 'sections': {},
                        'pass_profiles': dict(assignments)}

    def source_metadata(self, source_id):
        if self.imports.root is None:
            return {'title': source_id, 'creators': [], 'language': None, 'word_count': None}
        try:
            source = confined_source(self.imports.root, source_id)
            if source.is_file() and source.suffix.lower() == '.epub':
                return packed_epub_metadata(source)
            metadata = _folder_metadata(source)
            title = metadata.get('title') or source.name
        except (OSError, ValueError, PipelineError):
            metadata, title = {}, source_id
        return {'title': title,
                'creators': metadata.get('creators', []), 'language': metadata.get('language'), 'word_count': None}

    def setup_metadata(self, workspace_id):
        root = workspace_destination(self.root, workspace_id)
        path = root / 'workspace.json'
        if path.is_symlink():
            raise ValueError('Unsafe workspace metadata.')
        if not path.is_file():
            return {}
        value = read_json(path)
        if not isinstance(value, dict) or value.get('format_version') != 1 or value.get('source_id') != self.source(workspace_id):
            raise ValueError('Invalid workspace metadata.')
        return value

    def entries(self):
        state = self.read()
        result = [*state['sources'].values(), *state.get('drafts', {}).values()]
        known = {entry['workspace_id'] for entry in result}
        from .workspace_setup import read_workspace_setup
        for folder in self.root.iterdir():
            if folder.name in known or folder.is_symlink() or not folder.is_dir() or not (folder / 'workspace.json').is_file():
                continue
            try:
                setup = read_workspace_setup(folder)
                if setup:
                    result.append({'workspace_id': folder.name, 'source_id': setup['source_id'], 'archived': False})
            except (OSError, ValueError, PipelineError):
                continue
        return result

    def draft_lifecycle(self, workspace_id):
        entry = next((e for e in self.entries() if e['workspace_id'] == workspace_id), None)
        if entry is None:
            raise KeyError(workspace_id)
        return {'archived': entry.get('archived', False), 'revision': digest(entry)}

    def archive_draft(self, workspace_id, archived, revision):
        from .web import LifecycleConflict
        with self.lock, file_lock(self.root, '.intelitex-web.lock', 'Catalog busy.'):
            state = self.read()
            entry = next((e for e in [*state['sources'].values(), *state.get('drafts', {}).values()]
                          if e['workspace_id'] == workspace_id), None)
            if entry is None:
                from .workspace_setup import read_workspace_setup
                root = workspace_destination(self.root, workspace_id)
                setup = read_workspace_setup(root)
                if not setup:
                    raise KeyError(workspace_id)
                entry = {'workspace_id': workspace_id, 'source_id': setup['source_id'], 'archived': False}
                state.setdefault('drafts', {})[workspace_id] = entry
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

    def library_page(self, after, limit):
        if self.imports.root is None:
            return [], None
        state = self.read()
        items, next_cursor = self.imports.sources_page(after, limit)
        result = []
        for item in items:
            source = confined_source(self.imports.root, item['source_id'])
            entry = state['sources'].get(digest(str(source)))
            result.append({'source_id': item['source_id'], **self.source_metadata(item['source_id']),
                           'workspace_id': entry['workspace_id'] if entry else None})
        return result, next_cursor
