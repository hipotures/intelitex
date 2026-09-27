"""Real HTTP parity and post-migration edits, on disposable offline workspaces."""
import shutil
import sqlite3

from bookpipe.infrastructure.state_migration import migrate_state
from bookpipe.state_files import state_path
from bookpipe.store import Store
from test_server_api import api, analyze, approve, translate
from test_runtime import request


def test_http_parity_and_mutation_with_retired_database_forbidden(api, monkeypatch):
    app, root, service, server = api
    analyze(root)
    approve(service)
    translate(root)
    chapter = app.web.book(root)['chapters'][0]['id']
    paths = ['pipeline', 'summary', 'review', 'reader',
             f'reader/chapters/{chapter}', 'review/terms/T000001/evidence', 'publication/selection']
    before = {path: request(server, 'GET', '/api/workspaces/book/' + path) for path in paths}
    assert all(code == 200 for code, _ in before.values())
    current = Store(root)
    legacy = sqlite3.connect(root / 'state.sqlite3')
    current.db.backup(legacy)
    legacy.close(); current.close()
    shutil.rmtree(root / 'state')  # test fixture only: emulate the pre-migration format
    migrate_state(root)
    legacy_bytes = (root / 'state.sqlite3').read_bytes()
    original = sqlite3.connect
    def forbidden(database, *args, **kwargs):
        assert 'state.sqlite3' not in str(database), 'retired database opened by runtime'
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    after = {path: request(server, 'GET', '/api/workspaces/book/' + path) for path in paths}
    assert after == before
    revision = after['review'][1]['_revision']
    code, edited = request(server, 'PATCH', '/api/workspaces/book/review/terms/T000001',
                           {'revision': revision, 'custom': 'Adela'})
    assert code == 200
    code, reviewed = request(server, 'POST', '/api/workspaces/book/review/bulk-review',
                            {'revision': edited['revision'], 'term_ids': ['T000001']})
    assert code == 200
    code, approved = request(server, 'POST', '/api/workspaces/book/review/confirm-and-approve',
                            {'revision': reviewed['revision']})
    assert code == 200
    assert (root / 'state.sqlite3').read_bytes() == legacy_bytes
    assert state_path(root).is_file()
    (root / 'state.sqlite3').unlink()
    value = request(server, 'GET', '/api/workspaces/book/pipeline')
    assert value[0] == 200
    assert all(unit['passes']['5']['checkpoint_state'] == 'stale' for unit in value[1]['units'])
