"""Workspace-local runtime confinement and offline migration crash boundaries."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from bookpipe import source_runtime as runtime
from bookpipe.source_sessions import scope_for, _birth
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, digest, file_lock, read_json
from test_source_sessions import (project, native_mock, configured, inputs_for, run,
                                  manifest, reconcile_only, codex_transport)


@pytest.fixture
def old_slot(project, tmp_path):
    store = Store(project)
    scope = scope_for(store, 'pass1/unit', inputs_for(1))
    compatibility = {'source': scope.reference, 'protocol': {'cli_version': 'codex-cli 0.160.0'}}
    cid = digest(compatibility)
    slot = digest({'compatibility': cid, 'generation': 1})
    old_root = tmp_path / 'external'
    old = old_root / store.get('source_project_uuid') / 'unit' / scope.scope_id / slot
    for name in ('home/sessions', 'sqlite', 'work'):
        (old / name).mkdir(parents=True, mode=0o700)
    for name, data in {'home/auth.json': '{"test":"refreshed"}', 'home/sessions/selected.jsonl': 'native-turns',
                       'home/sessions/original.jsonl': 'original-turns', 'work/sentinel': 'stable cwd',
                       'owner.lock': 'ownership metadata', 'sqlite/queue_1.sqlite-wal': 'wal',
                       'sqlite/queue_1.sqlite-shm': 'shm'}.items():
        (old / name).write_text(data)
        (old / name).chmod(0o600)
    with sqlite3.connect(old / 'sqlite/state_5.sqlite') as db:
        db.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT, cwd TEXT)')
        db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('thread-existing', str(old / 'home/sessions/selected.jsonl'), str(old / 'work')))
        db.execute('CREATE TABLE project_roots (path TEXT)')
        db.execute('INSERT INTO project_roots VALUES (?)', (str(old / 'work'),))
    (old / 'sqlite/state_5.sqlite').chmod(0o600)
    path = project / 'artifacts/unit/sources' / scope.scope_id / 'sessions' / slot / 'manifest.json'
    record = {'version': 1, 'project_uuid': store.get('source_project_uuid'), 'scope': scope.reference,
              'compatibility': compatibility, 'compatibility_id': cid, 'slot_id': slot, 'generation': 1,
              'runtime': str(old), 'thread_id': 'thread-existing', 'session_id': 'session-existing',
              'p0_turn_id': 'p0-existing', 'thread_path': str(old / 'home/sessions/original.jsonl'),
              'state': 'cleanup_pending', 'p0_status': 'accepted', 'process_owner': None,
              'p0_attempt': 'artifacts/unit/pass0/attempt_001', 'submissions': [{'turn_id': 'saved-turn'}]}
    atomic_json(project / record['p0_attempt'] / 'request.semantic.json', {'resolved_profile': {'runtime_root': str(old_root)}})
    atomic_json(path, record)
    try:
        yield store, path, record, old
    finally:
        store.close()


def identities(record):
    return {key: record[key] for key in ('project_uuid', 'scope', 'slot_id', 'generation', 'compatibility',
            'compatibility_id', 'thread_id', 'session_id', 'p0_turn_id', 'state', 'p0_status', 'submissions')}


def assert_migrated(store, path, before, old, snapshot):
    after = read_json(path)
    target = runtime.runtime_path(store.root, 'unit', before['scope']['scope_id'], before['slot_id'])
    assert after['runtime'] == str(target) and after['runtime_layout_version'] == 2
    assert identities(after) == identities(before)
    assert not old.exists()
    assert not old.with_name(old.name + '.migrated-v2').exists()
    saved = runtime.tree_snapshot(target)
    for name, value in snapshot.items():
        if name == 'sqlite/state_5.sqlite':
            assert saved['.layout-v1-index/state_5.sqlite'] == value
        else:
            assert saved[name] == value
    with sqlite3.connect(target / 'sqlite/state_5.sqlite') as db:
        assert db.execute('SELECT * FROM threads').fetchone() == (
            before['thread_id'], str(target / 'home/sessions/selected.jsonl'), str(target / 'work'))
    assert after['thread_path'] == str(target / 'home/sessions/original.jsonl')
    assert (target / 'home/auth.json').stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o700
    return target


def test_old_layout_and_idempotent_copy(old_slot, monkeypatch):
    store, path, before, old = old_slot
    snapshot = runtime.tree_snapshot(old)
    # Source -> destination rename is forbidden here, as on different devices.
    replace = os.replace
    def same_device_only(src, dst):
        assert not (Path(src) == old and Path(dst).is_relative_to(store.root))
        return replace(src, dst)
    monkeypatch.setattr(os, 'replace', same_device_only)
    runtime.migrate_runtime(store, path)
    target = assert_migrated(store, path, before, old, snapshot)
    committed = path.read_bytes(), runtime.tree_snapshot(target)
    runtime.migrate_runtime(store, path)
    assert (path.read_bytes(), runtime.tree_snapshot(target)) == committed
    subprocess.run(['git', 'init', '-q', str(store.root)], check=True)
    result = subprocess.run(['git', '-C', str(store.root), 'check-ignore', str(target / 'home/auth.json')], capture_output=True)
    assert result.returncode == 0


@pytest.mark.parametrize('boundary', ['before_copy', 'during_copy', 'destination_complete', 'manifest_committed', 'old_runtime_retired'])
def test_crash_recovery(old_slot, monkeypatch, boundary):
    store, path, before, old = old_slot
    snapshot = runtime.tree_snapshot(old)
    def crash(name):
        if name == boundary:
            raise SystemExit('injected process loss')
    monkeypatch.setattr(runtime, '_checkpoint', crash)
    with pytest.raises(SystemExit):
        runtime.migrate_runtime(store, path)
    if boundary in ('before_copy', 'during_copy', 'destination_complete'):
        assert old.exists() and read_json(path)['runtime'] == str(old)
    else:
        assert read_json(path)['runtime_layout_version'] == 2
    monkeypatch.setattr(runtime, '_checkpoint', lambda name: None)
    # Fresh Store and process: no migration memory can help reconciliation.
    result = subprocess.run([str(Path('.venv/bin/python').resolve()), '-c',
        'from pathlib import Path; from bookpipe.store import Store; from bookpipe.source_runtime import migrate_runtime; '
        'import sys; s=Store(Path(sys.argv[1])); migrate_runtime(s,Path(sys.argv[2])); s.close()',
        str(store.root), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert_migrated(store, path, before, old, snapshot)


@pytest.mark.parametrize('ownership', ['lock', 'process'])
def test_active_owner_refuses_without_copy(old_slot, ownership):
    store, path, before, old = old_slot
    if ownership == 'process':
        before['process_owner'] = {'pid': os.getpid(), 'birth': _birth(os.getpid())}
        atomic_json(path, before)
    original = path.read_bytes(), runtime.tree_snapshot(old)
    if ownership == 'lock':
        with file_lock(old, 'owner.lock', 'busy'), pytest.raises(PipelineError, match='busy'):
            runtime.migrate_runtime(store, path)
    else:
        with pytest.raises(PipelineError, match='live app-server'):
            runtime.migrate_runtime(store, path)
    assert (path.read_bytes(), runtime.tree_snapshot(old)) == original
    assert not (store.root / 'artifacts/unit/codex-home').exists()


@pytest.mark.parametrize('corruption', ['root', 'project', 'chapter', 'scope', 'slot', 'traversal', 'symlink', 'native_symlink', 'manifest_scope'])
def test_malicious_binding_never_moves(old_slot, tmp_path, corruption):
    store, path, before, old = old_slot
    if corruption == 'root':
        before['runtime'] = str(tmp_path / 'arbitrary' / Path(*old.parts[-4:]))
    elif corruption in ('project', 'chapter', 'scope', 'slot'):
        parts = list(old.parts); parts[-{'project': 4, 'chapter': 3, 'scope': 2, 'slot': 1}[corruption]] = 'wrong'
        before['runtime'] = str(Path(*parts))
    elif corruption == 'traversal':
        before['runtime'] = str(old.parent / '..' / old.parent.name / old.name)
    elif corruption == 'symlink':
        alias = tmp_path / 'alias'; alias.symlink_to(old.parents[3], target_is_directory=True)
        before['runtime'] = str(alias.joinpath(*old.parts[-4:]))
    elif corruption == 'native_symlink':
        (old / 'home/private-link').symlink_to(tmp_path / 'untouched')
    else:
        before['scope'] = {**before['scope'], 'scope_id': 'f' * 64}
    atomic_json(path, before)
    auth = (old / 'home/auth.json').read_bytes()
    with pytest.raises((PipelineError, ValueError)):
        runtime.migrate_runtime(store, path)
    assert old.exists() and (old / 'home/auth.json').read_bytes() == auth
    assert not (store.root / 'artifacts/unit/codex-home').exists()


@pytest.mark.parametrize('lost_ack', [False, True])
def test_native_migration_same_thread_cold_resume(project, tmp_path, native_mock, monkeypatch, lost_ack):
    # Generate a genuine old-layout native fixture using the unchanged manager
    # protocol with only the path constructor emulating v1. No cloud transport.
    with closing(Store(project)) as store:
        scope_for(store, 'pass1/unit', inputs_for(1))
        uuid = store.get('source_project_uuid')
    old_path = lambda root, chapter, scope, slot: tmp_path / 'private-native' / uuid / chapter / scope / slot
    original_request = codex_transport._RpcSession.request
    def lose(rpc, method, params, deadline):
        result = original_request(rpc, method, params, deadline)
        if method == 'thread/revert':
            raise PipelineError('lost revert acknowledgement')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'runtime_path', old_path)
        if lost_ack:
            patch.setattr(codex_transport._RpcSession, 'request', lose)
            with pytest.raises(PipelineError, match='lost revert'):
                run(project, tmp_path, 1)
        else:
            run(project, tmp_path, 1)
    path = next(project.glob('artifacts/*/sources/*/sessions/*/manifest.json'))
    before = read_json(path); before.pop('runtime_layout_version')
    atomic_json(path, before)
    calls = len(native_mock['calls'])
    with closing(Store(project)) as store:
        runtime.migrate_runtime(store, path)
    assert len(native_mock['calls']) == calls == 2
    assert identities(manifest(project)) == identities(before)
    reconcile_only(project, tmp_path, 1)
    assert len(native_mock['calls']) == calls
    run(project, tmp_path, 2)
    after = manifest(project)
    assert (after['thread_id'], after['session_id'], after['p0_turn_id']) == (before['thread_id'], before['session_id'], before['p0_turn_id'])
    assert len(native_mock['calls']) == 3
    assert sum(method == 'thread/start' for method, _ in native_mock['rpc']) == 1
    task = native_mock['calls'][-1]
    users = [json.loads(i['content'][0]['text']) for i in task['input'] if i.get('role') == 'user' and i['content'][0].get('text', '').startswith('{')]
    assert [u['ACTIVE_PASS'] for u in users] == [0, 2]
    assert 'SOURCE' not in users[-1]


def test_new_path_ignores_runtime_root_and_xdg(project, tmp_path, native_mock, monkeypatch):
    xdg = tmp_path / 'xdg'; monkeypatch.setenv('XDG_STATE_HOME', str(xdg))
    run(project, tmp_path, 1)
    before = manifest(project)
    target = runtime.runtime_path(project, 'unit', before['scope']['scope_id'], before['slot_id'])
    assert before['runtime'] == str(target) and before['runtime_layout_version'] == 2
    assert all((target / name).is_dir() for name in ('home', 'sqlite', 'work'))
    assert not (xdg / 'intelitex/codex').exists() and not (tmp_path / 'private-native').exists()
    run(project, tmp_path, 2)
    after = manifest(project)
    assert after['thread_id'] == before['thread_id'] and after['p0_turn_id'] == before['p0_turn_id']
    assert len(native_mock['calls']) == 3
    client, _ = configured(project, tmp_path)
    env = client._process_env(target / 'home', target / 'sqlite')
    assert env['CODEX_HOME'] == str(target / 'home') and env['CODEX_SQLITE_HOME'] == str(target / 'sqlite')
    assert {p['cwd'] for method, p in native_mock['rpc'] if method == 'thread/start'} == {str(target / 'work')}
    client.close()


def test_short_names_keep_full_binding(old_slot, monkeypatch):
    store, path, before, old = old_slot
    target = runtime.runtime_path(store.root, 'unit', before['scope']['scope_id'], before['slot_id'])
    assert len(target.name) == len(target.parent.name) == 12
    runtime.migrate_runtime(store, path)
    assert read_json(target / runtime.IDENTITY)['slot_id'] == before['slot_id']
    with pytest.raises(PipelineError, match='collision'):
        runtime.bind_directory(target, (before['project_uuid'], 'unit', before['scope']['scope_id'], 'f' * 64))


@pytest.mark.parametrize('retired', [False, True])
def test_maintenance_rejects_foreign_short_binding(old_slot, retired):
    from bookpipe.source_sessions import retire_session
    store, path, before, old = old_slot
    runtime.migrate_runtime(store, path)
    record = read_json(path)
    record['retired'] = retired
    atomic_json(path, record)
    target = Path(record['runtime'])
    private = read_json(target / runtime.IDENTITY)
    private['slot_id'] = 'f' * 64
    atomic_json(target / runtime.IDENTITY, private)
    with pytest.raises(PipelineError, match='collision'):
        retire_session(store, before['slot_id'], purge=retired)
    assert (target / 'home/auth.json').exists()


@pytest.mark.parametrize('interrupted', [False, True])
def test_full_local_layout_shortens_without_new_thread(old_slot, monkeypatch, interrupted):
    store, path, before, old = old_slot
    full = store.root / 'artifacts/unit/codex-home' / before['scope']['scope_id'] / before['slot_id']
    full.parent.mkdir(parents=True)
    os.replace(old, full)
    with sqlite3.connect(full / 'sqlite/state_5.sqlite') as db:
        db.execute('UPDATE threads SET rollout_path=?, cwd=?', (str(full / 'home/sessions/selected.jsonl'), str(full / 'work')))
        db.execute('UPDATE project_roots SET path=?', (str(full / 'work'),))
    before.update(runtime=str(full), runtime_layout_version=2, thread_path=str(full / 'home/sessions/original.jsonl'))
    atomic_json(full / runtime.MARKER, {'id': 'previous-completed-migration', 'digest': 'previous-digest'})
    before['runtime_migration'] = {'complete': True}
    atomic_json(path, before)
    snapshot = runtime.tree_snapshot(full)
    if interrupted:
        def crash(name):
            if name == 'during_copy':
                raise SystemExit('interrupted shortening')
        monkeypatch.setattr(runtime, '_checkpoint', crash)
        with pytest.raises(SystemExit):
            runtime.migrate_runtime(store, path)
        monkeypatch.setattr(runtime, '_checkpoint', lambda name: None)
    runtime.migrate_runtime(store, path)
    assert_migrated(store, path, before, full, snapshot)


def test_private_runtime_never_enumerated_as_attempt(project):
    from bookpipe.artifacts import attempt_manifests
    from bookpipe.application.source_preload import confined
    from bookpipe.evidence import EvidenceError
    secret = project / 'artifacts/ch0001/codex-home/scope/slot/home/attempt_001/attempt.json'
    public = project / 'artifacts/ch0001/pass0/unit/attempt_001/attempt.json'
    atomic_json(secret, {'password': 'private-sentinel'})
    atomic_json(public, {'pass': 0})
    assert attempt_manifests(project) == [public]
    with pytest.raises(EvidenceError, match='Unsafe'):
        confined(project, secret)


def test_copy_between_real_filesystems(old_slot):
    import shutil
    import tempfile
    store, path, before, old = old_slot
    with tempfile.TemporaryDirectory(prefix='intelitex-runtime-cross-device-', dir='/dev/shm') as scratch:
        external = Path(scratch)
        if external.stat().st_dev == store.root.stat().st_dev:
            pytest.skip('No separate filesystem available')
        source = external.joinpath(*old.parts[-4:])
        source.parent.mkdir(parents=True)
        shutil.copytree(old, source)
        with sqlite3.connect(source / 'sqlite/state_5.sqlite') as db:
            db.execute('UPDATE threads SET rollout_path=?, cwd=?', (str(source / 'home/sessions/selected.jsonl'), str(source / 'work')))
            db.execute('UPDATE project_roots SET path=?', (str(source / 'work'),))
        before.update(runtime=str(source), thread_path=str(source / 'home/sessions/original.jsonl'))
        atomic_json(path, before)
        atomic_json(store.root / before['p0_attempt'] / 'request.semantic.json', {'resolved_profile': {'runtime_root': str(external)}})
        snapshot = runtime.tree_snapshot(source)
        runtime.migrate_runtime(store, path)
        assert_migrated(store, path, before, source, snapshot)


def test_workspace_symlink_escape_rejected(old_slot, tmp_path):
    store, path, before, old = old_slot
    outside = tmp_path / 'outside'; outside.mkdir()
    (store.root / 'artifacts/unit/codex-home').symlink_to(outside, target_is_directory=True)
    with pytest.raises(PipelineError, match='symlinks'):
        runtime.migrate_runtime(store, path)
    assert not list(outside.iterdir()) and old.exists()


def test_existing_unbound_native_directory_cannot_be_adopted(tmp_path):
    runtime.private_directory(tmp_path / 'slot')
    (tmp_path / 'slot/native-state').write_text('must survive')
    with pytest.raises(PipelineError, match='Unbound'):
        runtime.bind_directory(tmp_path / 'slot', ('project','chapter','scope','slot'))
    assert (tmp_path / 'slot/native-state').read_text() == 'must survive'


def test_unreadable_live_owner_is_not_assumed_dead(old_slot, monkeypatch):
    store, path, before, old = old_slot
    before['process_owner'] = {'pid': os.getpid(), 'birth': _birth(os.getpid())}
    atomic_json(path, before)
    monkeypatch.setattr('bookpipe.source_sessions._birth', lambda pid: None)
    with pytest.raises(PipelineError, match='Cannot prove'):
        runtime.migrate_runtime(store, path)
    assert old.exists() and 'runtime_migration' not in read_json(path)


@pytest.mark.parametrize('already_linked', [False, True])
def test_migration_shared_auth_preserves_refresh_and_never_deletes_target(old_slot, tmp_path, already_linked):
    store, path, before, old = old_slot
    auth = tmp_path / 'shared-auth.json'
    auth.write_text('{"fake":"current-shared"}')
    auth.chmod(0o600)
    semantic = store.root / before['p0_attempt'] / 'request.semantic.json'
    profile = read_json(semantic)
    profile['resolved_profile']['options'] = {'auth_source': str(auth)}
    atomic_json(semantic, profile)
    if already_linked:
        (old / 'home/auth.json').unlink()
        (old / 'home/auth.json').symlink_to(auth)
    runtime.migrate_runtime(store, path)
    target = Path(read_json(path)['runtime'])
    assert identities(read_json(path)) == identities(before)
    assert not old.exists()
    assert (target / 'home/auth.json').is_symlink()
    assert (target / 'home/auth.json').readlink() == auth
    if not already_linked:
        assert (target / 'home/.auth-before-shared-link.json').read_text() == '{"test":"refreshed"}'
    auth.write_text('{"fake":"refreshed-shared"}')
    snapshot = path.read_bytes(), runtime.tree_snapshot(target, auth_source=auth)
    runtime.migrate_runtime(store, path)
    assert (path.read_bytes(), runtime.tree_snapshot(target, auth_source=auth)) == snapshot
    assert (target / 'home/auth.json').read_text() == auth.read_text() == '{"fake":"refreshed-shared"}'
    from bookpipe.source_sessions import retire_session
    retire_session(store, before['slot_id'])
    retire_session(store, before['slot_id'], purge=True)
    assert auth.read_text() == '{"fake":"refreshed-shared"}'


@pytest.mark.parametrize('failure', ['wrong_link', 'unconfigured_link', 'public_target', 'missing_target', 'target_symlink', 'hardlink', 'self_target'])
def test_shared_auth_fails_closed(tmp_path, failure):
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    target = tmp_path / 'shared-auth.json'; target.write_text('private dummy'); target.chmod(0o600)
    link = home / 'auth.json'
    source = target
    if failure == 'wrong_link':
        other = tmp_path / 'other'; other.write_text('untouched'); link.symlink_to(other)
    elif failure == 'unconfigured_link':
        link.symlink_to(target); source = None
    elif failure == 'public_target':
        target.chmod(0o644)
    elif failure == 'missing_target':
        target.unlink()
    elif failure == 'target_symlink':
        source = tmp_path / 'alias'; source.symlink_to(target)
    elif failure == 'self_target':
        link.write_text('private dummy'); link.chmod(0o600); source = link
    else:
        os.link(target, tmp_path / 'second-link')
    with pytest.raises(PipelineError):
        runtime.install_auth_link(home, source)
    if target.exists():
        assert target.read_text() == 'private dummy'


def test_interrupted_auth_link_installation_recovers(tmp_path, monkeypatch):
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    target = tmp_path / 'shared-auth.json'; target.write_text('common'); target.chmod(0o600)
    private = home / 'auth.json'; private.write_text('old refreshed'); private.chmod(0o600)
    replace = os.replace
    def crash(src, dst):
        if Path(src).name == '.auth-link.tmp':
            raise SystemExit('crash before link publication')
        return replace(src, dst)
    monkeypatch.setattr(os, 'replace', crash)
    with pytest.raises(SystemExit):
        runtime.install_auth_link(home, target)
    monkeypatch.setattr(os, 'replace', replace)
    runtime.install_auth_link(home, target)
    assert private.is_symlink() and private.read_text() == 'common'
    assert (home / '.auth-before-shared-link.json').read_text() == 'old refreshed'


def test_migration_recovers_interrupted_auth_link_publication(old_slot, tmp_path, monkeypatch):
    store, path, before, old = old_slot
    target = tmp_path / 'shared-auth.json'; target.write_text('shared dummy'); target.chmod(0o600)
    semantic = store.root / before['p0_attempt'] / 'request.semantic.json'
    profile = read_json(semantic); profile['resolved_profile']['options'] = {'auth_source': str(target)}
    atomic_json(semantic, profile)
    replace = os.replace
    def crash(src, dst):
        if Path(src).name == '.auth-link.tmp':
            raise SystemExit('crash during copied auth link publication')
        return replace(src, dst)
    monkeypatch.setattr(os, 'replace', crash)
    with pytest.raises(SystemExit):
        runtime.migrate_runtime(store, path)
    assert old.is_dir() and read_json(path)['runtime'] == str(old)
    monkeypatch.setattr(os, 'replace', replace)
    runtime.migrate_runtime(store, path)
    migrated = Path(read_json(path)['runtime'])
    assert not old.exists() and (migrated / 'home/auth.json').is_symlink()
    assert (migrated / 'home/.auth-before-shared-link.json').read_text() == '{"test":"refreshed"}'
    assert target.read_text() == 'shared dummy' and identities(read_json(path)) == identities(before)


def test_native_auth_link_read_write_without_inference(tmp_path):
    import shutil
    executable = shutil.which('codex')
    if not executable:
        pytest.skip('Installed Codex required for local credential storage probe')
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    target = tmp_path / 'shared-auth.json'; target.write_text('{}'); target.chmod(0o600)
    runtime.install_auth_link(home, target)
    env = codex_transport.CodexAppServerClient._process_env(home, tmp_path / 'sqlite')
    argv = [executable, '-c', 'cli_auth_credentials_store="file"']
    for n in range(2):
        key = f'sk-offline-auth-link-probe-not-real-OLD{n}'
        write = subprocess.run(argv + ['login', '--with-api-key'], input=key + '\n',
                               env=env, cwd=home, text=True, capture_output=True, timeout=20)
        assert write.returncode == 0
        assert (home / 'auth.json').is_symlink()
        assert read_json(target)['OPENAI_API_KEY'] == key
        check = subprocess.run(argv + ['login', 'status'], env=env, cwd=home,
                               text=True, capture_output=True, timeout=20)
        assert check.returncode == 0 and 'Logged in using an API key' in check.stderr + check.stdout
        replacement = tmp_path / 'relogged-auth.json'
        atomic_json(replacement, {'OPENAI_API_KEY': key + '-NEW' + str(n), 'auth_mode': 'apikey'})
        replacement.chmod(0o600); replacement.replace(target)
        assert (home / 'auth.json').read_text() == target.read_text()
        check = subprocess.run(argv + ['login', 'status'], env=env, cwd=home,
                               text=True, capture_output=True, timeout=20)
        assert check.returncode == 0 and 'NEW' + str(n) in check.stderr + check.stdout


def test_native_cold_resume_after_shared_auth_replacement(project, tmp_path, native_mock, monkeypatch):
    auth = tmp_path / 'shared-auth.json'
    atomic_json(auth, {'OPENAI_API_KEY': 'sk-offline-native-source-old', 'auth_mode': 'apikey'})
    auth.chmod(0o600)
    original = configured
    def with_shared(root, scratch):
        client, settings = original(root, scratch)
        client.settings['options']['auth_source'] = str(auth)
        client.resolved_profile.setdefault('options', {})['auth_source'] = str(auth)
        return client, settings
    monkeypatch.setattr('test_source_sessions.configured', with_shared)
    run(project, tmp_path, 1)
    before = manifest(project)
    link = Path(before['runtime']) / 'home/auth.json'
    assert link.is_symlink()
    replacement = tmp_path / 'new-login.json'
    atomic_json(replacement, {'OPENAI_API_KEY': 'sk-offline-native-source-new', 'auth_mode': 'apikey'})
    replacement.chmod(0o600); replacement.replace(auth)
    run(project, tmp_path, 2)
    after = manifest(project)
    assert (after['thread_id'], after['session_id'], after['p0_turn_id']) == (before['thread_id'], before['session_id'], before['p0_turn_id'])
    assert len(native_mock['calls']) == 3
    assert sum(method == 'thread/start' for method, _ in native_mock['rpc']) == 1
    users = [json.loads(i['content'][0]['text']) for i in native_mock['calls'][-1]['input']
             if i.get('role') == 'user' and i['content'][0].get('text', '').startswith('{')]
    assert [u['ACTIVE_PASS'] for u in users] == [0, 2] and 'SOURCE' not in users[-1]
    assert link.is_symlink() and read_json(link)['OPENAI_API_KEY'] == 'sk-offline-native-source-new'
