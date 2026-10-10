"""Export browser DTOs from fresh real application/native-loopback evidence.

No configured authentication or cloud provider is used. Native model turns are
bounded to two P0 calls through the same fixture as the recovery regressions.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from test_source_preload import make_preload_book
from test_source_sessions import native_mock
from bookpipe.application.commands import PreloadCommand
from bookpipe.application.source_preload import LocalCodexCounter
from bookpipe.application.workspaces import WorkspaceQueries
from bookpipe.engine import analysis_plan
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.supervisor import JobSupervisor
from bookpipe.server.service import ServerService
from bookpipe.store import Store
from bookpipe.util import atomic_json, read_json


def export(destination):
    with tempfile.TemporaryDirectory(prefix='intelitex-p0-browser-fixture-') as directory, pytest.MonkeyPatch.context() as patch:
        base = Path(directory)
        app, root, settings = make_preload_book(base, paragraphs=8)
        settings['profiles']['second-model'] = {**copy.deepcopy(settings['profiles']['preload-test']), 'model': 'gpt-6.1-sol'}
        settings['pass_profiles']['2'] = 'second-model'
        atomic_json(root / 'settings.json', settings)
        atomic_json(root / 'workspace.json', {'format_version': 1, 'source_id': 'source',
                    'source_fingerprint': read_json(root / 'book.json')['source_fingerprint'],
                    'source_language': 'pl', 'target_language': 'en'})
        mock = native_mock.__wrapped__(base, patch)
        state = next(mock)
        registry = JobRegistry(root.parent)
        supervisor = JobSupervisor(registry, root.parent)
        service = ServerService(app, WorkspaceQueries(app.projects, root.parent), supervisor)
        def snapshot():
            return {'inventory': service.source_preload('book'), 'pipeline': service.pipeline('book'),
                    'usage': service.usage('book'), 'profiles': service.settings('book'),
                    'workspaces': {'workspaces': service.list_workspaces()}}
        try:
            pending = snapshot()
            initial = pending['inventory']
            selected = initial['chapters'][0]['targets']
            assert len(selected) == 2
            for target in selected:
                current = app.source_preload.inventory(root)
                app.source_preload.run(PreloadCommand(root, target['target_id'], current['intent_revision']), app.pipeline.progress)
            assert len(state['calls']) == 2
            accepted = snapshot()
            assert accepted['inventory']['summary']['accepted'] == 2
            assert sum(p['physical_attempt_count'] for u in accepted['usage']['units'] for p in u['passes'] if p['pass_no'] == 0) == 2
            previews = {target['target_id']: [service.application.source_preload.preview(root, target['target_id'], page) for page in range(2)]
                        for chapter in accepted['inventory']['chapters'] for target in chapter['targets']}
            store = Store(root)
            analysis_plan(store, read_json(root / 'book.json'), LocalCodexCounter(settings['profiles']['preload-test']), settings)
            store.close()
            p1 = snapshot()
            p1_previews = {u['id']: app.web.analysis_unit_preview(root, u['id']) for u in p1['pipeline']['analysis']['units']}
            value = {'pending': pending, 'accepted': accepted, 'p1': p1, 'previews': previews, 'p1_previews': p1_previews,
                     'native_loopback_calls': len(state['calls']), 'cloud_calls': 0}
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False))
        finally:
            supervisor.shutdown(); registry.close(); mock.close()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--output':
        raise SystemExit('Use --output with a private fixture JSON path.')
    export(Path(sys.argv[2]))
