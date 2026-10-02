"""Confined source-session homes and offline, recoverable v1 -> v2 migration.

The session manifest commits ownership. A verified private copy is published
before switching it; the old runtime is removed only after that commit. No
provider, app-server or model is started by this module.
"""
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat

from .util import PipelineError, atomic_json, atomic_text, digest, file_lock, read_json

LAYOUT_VERSION = 2
MARKER = '.intelitex-layout-migration.json'
IDENTITY = '.intelitex-runtime-identity.json'


def no_links(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise PipelineError('Unsafe source-session runtime path.')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise PipelineError('Source-session runtime paths must not contain symlinks.')
    return path


def identifier(value, *, hashed=False):
    pattern = r'[a-f0-9]{64}' if hashed else r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}'
    if not isinstance(value, str) or not re.fullmatch(pattern, value) or '..' in value:
        raise PipelineError('Unsafe source-session runtime identity.')
    return value


def runtime_path(project, chapter, scope, slot):
    root = no_links(Path(project).absolute())
    return no_links(root / 'artifacts' / identifier(chapter) / 'codex-home' /
                    identifier(scope, hashed=True)[:12] / identifier(slot, hashed=True)[:12])


def bind_directory(destination, identity, *, verified_copy=False):
    """Short filenames never weaken the complete persisted identity binding."""
    path = no_links(destination / IDENTITY)
    value = dict(zip(('project_uuid', 'chapter_id', 'scope_id', 'slot_id'), identity))
    if path.exists():
        verify_directory(destination, identity)
    else:
        if not verified_copy:
            # Only empty directories created by acquire may be adopted. A
            # directory containing unknown native state must never gain a new ID.
            for item in destination.iterdir():
                if item.name == 'owner.lock' and item.is_file():
                    continue
                if item.name in ('home', 'sqlite', 'work') and item.is_dir() and not any(item.iterdir()):
                    continue
                raise PipelineError('Unbound native runtime contents; preserve them for inspection.')
        _save(path, value)


def verify_directory(destination, identity):
    path = no_links(destination / IDENTITY)
    expected = dict(zip(('project_uuid', 'chapter_id', 'scope_id', 'slot_id'), identity))
    if not path.is_file() or read_json(path) != expected:
        raise PipelineError('Short runtime name collision or missing binding; preserve both session identities.')


def binding(store, manifest, record):
    """Validate all authoritative identities before trusting a persisted path."""
    try:
        root = no_links(store.root.absolute())
        manifest = no_links(Path(manifest).absolute())
        parts = manifest.relative_to(root).parts
        if len(parts) != 7 or parts[0] != 'artifacts' or parts[2] != 'sources' or parts[4] != 'sessions' or parts[6] != 'manifest.json':
            raise ValueError('manifest layout')
        chapter, scope, slot = parts[1], parts[3], parts[5]
        expected = runtime_path(root, chapter, scope, slot)
        project = identifier(store.get('source_project_uuid'))
        compatibility = record['compatibility']
        if (record['project_uuid'] != project or record['scope']['scope_id'] != scope
                or record['scope'] != compatibility['source'] or record['slot_id'] != slot
                or record['compatibility_id'] != digest(compatibility)
                or type(record['generation']) is not int or record['generation'] < 1
                or slot != digest({'compatibility': record['compatibility_id'], 'generation': record['generation']})):
            raise ValueError('identity mismatch')
        inventory = store.get('source_scope_inventory', {}).get('scopes', {}).get(scope)
        if not inventory or inventory.get('chapter_id') != chapter or any(inventory.get(k) != v for k, v in record['scope'].items()):
            raise ValueError('source inventory mismatch')
        persisted = no_links(Path(record['runtime']))
        if record.get('thread_path'):
            thread = no_links(Path(record['thread_path']))
            if not thread.is_relative_to(persisted / 'home'):
                raise ValueError('native thread path escapes its bound home')
        return expected, (project, chapter, scope, slot)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PipelineError('Source-session runtime binding is invalid; no files moved.') from exc


def private_directory(path):
    no_links(path)
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    os.chmod(path, 0o700)


def protect_git(project, chapter):
    # This also protects projects created inside a repository other than Intelitex.
    folder = no_links(Path(project).absolute() / 'artifacts' / chapter)
    private_directory(folder)
    path = no_links(folder / '.gitignore')
    current = path.read_text() if path.exists() else ''
    rule = '/codex-home/'
    if rule not in current.splitlines():
        atomic_text(path, current + ('\n' if current and not current.endswith('\n') else '') + rule + '\n')


def auth_target(value):
    """Only explicitly configured private regular credentials may be shared."""
    path = no_links(Path(value).expanduser().absolute())
    if (not path.is_file() or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077 or path.stat().st_nlink != 1):
        raise PipelineError('Configured shared Codex authentication must be an available owner-private regular file (0600).')
    return path


def verify_auth_link(path, source):
    target = auth_target(source)
    no_links(path.parent)
    if not path.is_symlink() or os.readlink(path) != str(target):
        raise PipelineError('Codex authentication link does not match the configured authorized source.')
    return target


def install_auth_link(home, source):
    """Called only under the slot lease, after proving its native owner exited.

    A single narrowly authorized symlink is permitted; native directories and
    every other private file still reject links. Preserve old refreshed copies
    before switching, and never overwrite the user's shared credentials.
    """
    no_links(home)
    path = home / 'auth.json'
    if not source:
        no_links(path)
        return
    target = auth_target(source)
    if target.is_relative_to(home) or 'codex-home' in target.parts:
        raise PipelineError('Shared authentication must not point into a native session home.')
    if path.is_symlink():
        verify_auth_link(path, target)
        return
    if path.exists():
        if not path.is_file() or path.stat().st_nlink != 1:
            raise PipelineError('Unsafe private Codex authentication copy; preserve it.')
        backup = no_links(home / '.auth-before-shared-link.json')
        if backup.exists():
            raise PipelineError('Previous authentication backup exists; preserve both copies for inspection.')
        os.chmod(path, 0o600)
        _sync(path)
        os.replace(path, backup)
        _sync(home)
    temporary = home / '.auth-link.tmp'
    if temporary.is_symlink():
        verify_auth_link(temporary, target)
        temporary.unlink()
    elif temporary.exists():
        raise PipelineError('Unowned authentication link temporary file; preserve it.')
    temporary.symlink_to(target)
    os.replace(temporary, path)
    _sync(home)


def original_profile(store, record):
    attempt = record.get('p0_attempt') or (record.get('active') or {}).get('attempt')
    if not attempt:
        return {}
    semantic = no_links(store.root.absolute() / attempt / 'request.semantic.json')
    if not semantic.is_relative_to(store.root.absolute()) or 'codex-home' in semantic.relative_to(store.root.absolute()).parts:
        raise PipelineError('Unsafe original runtime configuration evidence.')
    return read_json(semantic).get('resolved_profile', {})


def _idle(record):
    from .source_sessions import _birth
    owner = record.get('process_owner')
    if owner:
        if not isinstance(owner, dict) or type(owner.get('pid')) is not int or owner['pid'] <= 0 or not owner.get('birth'):
            raise PipelineError('Unverifiable source-session process owner; migration refused.')
        birth = _birth(owner['pid'])
        if birth == owner['birth']:
            raise PipelineError('Source session has a live app-server owner; migration refused.')
        if birth is None:
            try:
                os.kill(owner['pid'], 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            raise PipelineError('Cannot prove the recorded app-server owner has exited; migration refused.')


def _sync(path):
    fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0) | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def tree_snapshot(root, *, auth_source=None, allow_auth_temporary=False):
    """Hash every file, WAL/SHM included, without traversing native symlinks."""
    no_links(root)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if relative == MARKER:
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                result[relative] = ['directory', stat.S_IMODE(mode)]
            elif stat.S_ISLNK(mode) and auth_source and (relative == 'home/auth.json' or
                    allow_auth_temporary and relative == 'home/.auth-link.tmp'):
                # Hash the authorized link binding, never read/copy its mutable
                # external credentials. Refreshes must remain shared.
                result[relative] = ['auth-link', str(verify_auth_link(path, auth_source))]
            elif stat.S_ISREG(mode) and path.stat().st_nlink == 1:
                with path.open('rb') as handle:
                    hashed = hashlib.file_digest(handle, 'sha256').hexdigest()
                result[relative] = ['file', stat.S_IMODE(mode), path.stat().st_size, hashed]
            else:
                raise PipelineError('Unsafe native runtime file type or link; migration refused.')
    return result


def _save(path, record):
    atomic_json(path, record)
    os.chmod(path, 0o600)


def _checkpoint(name):
    """Fault-injection boundary; no production behavior."""


def _old_path(store, record, identity, value, legacy_root=None):
    old = no_links(Path(value))
    full_local = no_links(store.root.absolute() / 'artifacts' / identity[1] / 'codex-home' / identity[2] / identity[3])
    if record.get('runtime_layout_version') == 2 and old == full_local:
        return old
    if tuple(old.parts[-4:]) != identity or old.is_relative_to(store.root.resolve()):
        raise PipelineError('Unsafe old runtime binding; no files moved.')
    configured = original_profile(store, record).get('runtime_root')
    if configured:
        legacy_root = Path(configured).expanduser().absolute()
    roots = [Path(legacy_root).expanduser().absolute()] if legacy_root is not None else [
        Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'intelitex/codex',
        Path.home() / '.local/state/intelitex/codex']
    if not any(old == no_links(root.absolute()).joinpath(*identity) for root in roots):
        raise PipelineError('Old runtime disagrees with its original configured root.')
    return old


def _relocate_native_index(copied, old, destination, record):
    """Codex 0.160.0 paginated rollout selection is authoritative in state_5.

    Rebase only operational paths, never history, turns, IDs or model evidence.
    Preserve the original DB/WAL/SHM byte-for-byte in a private migration backup.
    Unknown storage versions fail closed instead of guessing their schema.
    """
    if not record.get('thread_id'):
        return
    version = record.get('compatibility', {}).get('protocol', {}).get('cli_version')
    if version != 'codex-cli 0.160.0':
        raise PipelineError('Native runtime relocation requires audited Codex 0.160.0 storage; preserve the old runtime.')
    database = copied / 'sqlite' / 'state_5.sqlite'
    if not database.is_file():
        raise PipelineError('Native state database is missing; migration refused.')
    backup = copied / '.layout-v1-index'
    if backup.exists():
        backup = copied / ('.layout-v1-index-' + digest(str(old))[:12])
        if backup.exists():
            raise PipelineError('Native index migration backup already exists; preserve it.')
    private_directory(backup)
    for source in (copied / 'sqlite').glob('state_5.sqlite*'):
        shutil.copy2(source, backup / source.name)
    db = sqlite3.connect(database)
    try:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise PipelineError('Native state database failed integrity verification.')
        rows = db.execute('SELECT id, rollout_path, cwd FROM threads').fetchall()
        if len(rows) != 1 or rows[0][0] != record['thread_id']:
            raise PipelineError('Native thread identity differs from the source-session manifest.')
        _, rollout, cwd = rows[0]
        if cwd != str(old / 'work'):
            raise PipelineError('Native cwd disagrees with its manifest binding.')
        # R1 can leave the manifest pointing at the previous immutable rollout
        # after a lost revert acknowledgement. Keep that evidence; rebase the
        # database's actual selection without deciding recovery here.
        relative = no_links(Path(rollout)).relative_to(old / 'home')
        if not (copied / 'home' / relative).is_file():
            raise PipelineError('Selected native rollout is missing from the runtime copy.')
        with db:
            db.execute('UPDATE threads SET rollout_path=?, cwd=? WHERE id=?',
                       (str(destination / 'home' / relative), str(destination / 'work'), record['thread_id']))
            # Project roots are operational navigation metadata, not model history.
            db.execute('UPDATE project_roots SET path=? WHERE path=?', (str(destination / 'work'), str(old / 'work')))
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise PipelineError('Relocated native state database failed verification.')
    finally:
        db.close()


def migrate_runtime(store, manifest, *, legacy_root=None, auth_source=None):
    """Migrate one existing slot offline. Retired slots stay explicit maintenance."""
    manifest = no_links(Path(manifest).absolute())
    initial = read_json(manifest)
    destination, identity = binding(store, manifest, initial)
    no_links(manifest.parent / 'runtime-layout.lock')
    with file_lock(manifest.parent, 'runtime-layout.lock', 'Source runtime migration is already owned.'):
        record = read_json(manifest)
        binding(store, manifest, record)
        auth_source = auth_source or original_profile(store, record).get('options', {}).get('auth_source')
        version = record.get('runtime_layout_version', 1)
        journal = record.get('runtime_migration')
        committed = version == LAYOUT_VERSION and record.get('runtime') == str(destination)
        full_local = store.root.absolute() / 'artifacts' / identity[1] / 'codex-home' / identity[2] / identity[3]
        if version == LAYOUT_VERSION and not committed and record.get('runtime') != str(full_local):
            raise PipelineError('Workspace-local runtime binding changed; no external fallback allowed.')
        if committed:
            if not journal or journal.get('complete'):
                if destination.exists():
                    verify_directory(destination, identity)
                    if auth_source:
                        no_links(destination / 'owner.lock')
                        with file_lock(destination, 'owner.lock', 'Source session is busy; authentication maintenance refused.'):
                            _idle(record)
                            install_auth_link(destination / 'home', auth_source)
                return record
        elif version not in (1, LAYOUT_VERSION):
            raise PipelineError('Unsupported source-session runtime layout version.')
        elif record.get('retired'):
            return record
        if not committed and journal and journal.get('complete'):
            # A previous external -> full-length local migration is finished.
            # Start an independent, verifiable local shortening transaction.
            journal = None
        old = _old_path(store, record, identity, journal['source'] if journal else record['runtime'], legacy_root)
        with ExitStack() as leases:
            if old.exists():
                no_links(old / 'owner.lock')
                leases.enter_context(file_lock(old, 'owner.lock', 'Source session is busy; migration refused.'))
            elif not committed:
                raise PipelineError('Old native runtime is missing; no new session or P0 will be created.')
            _idle(record)
            for directory in ('home', 'sqlite', 'work'):
                if old.exists() and not no_links(old / directory).is_dir():
                    raise PipelineError('Incomplete old native runtime; migration refused.')
            if not journal:
                snapshot = tree_snapshot(old, auth_source=auth_source)
                journal = {'source': str(old), 'destination': str(destination), 'source_digest': digest(snapshot),
                           'source_identity': [old.stat().st_dev, old.stat().st_ino],
                           'identity': list(identity), 'complete': False}
                journal['id'] = digest(journal)
                record['runtime_migration'] = journal
                _save(manifest, record)
            if (journal.get('destination') != str(destination) or journal.get('identity') != list(identity)
                    or not isinstance(journal.get('id'), str)):
                raise PipelineError('Runtime migration journal binding changed.')
            if old.exists() and digest(tree_snapshot(old, auth_source=auth_source)) != journal['source_digest']:
                raise PipelineError('Old runtime changed during migration; preserve both copies for inspection.')
            _checkpoint('before_copy')
            protect_git(store.root, identity[1])
            private_directory(destination.parent)
            temporary = no_links(destination.with_name('.' + destination.name + '.migrating'))
            if not destination.exists():
                if committed:
                    raise PipelineError('Committed native runtime is missing; no fallback allowed.')
                if temporary.exists():
                    marker = temporary / MARKER
                    if any(temporary.iterdir()) and (not marker.is_file() or read_json(no_links(marker)).get('id') != journal['id']):
                        raise PipelineError('Unowned runtime migration temporary directory.')
                    tree_snapshot(temporary, auth_source=auth_source, allow_auth_temporary=True)
                    shutil.rmtree(temporary)
                private_directory(temporary)
                _save(temporary / MARKER, {'id': journal['id']})
                for entry in sorted(old.iterdir()):
                    if entry.name == MARKER:
                        continue  # An earlier layout transaction cannot replace this copy's recovery marker.
                    if entry.is_dir():
                        shutil.copytree(entry, temporary / entry.name, copy_function=shutil.copy2, symlinks=True)
                    else:
                        shutil.copy2(entry, temporary / entry.name)
                    _checkpoint('during_copy')
                if digest(tree_snapshot(temporary, auth_source=auth_source)) != journal['source_digest']:
                    raise PipelineError('Copied native runtime failed verification; old state retained.')
                _relocate_native_index(temporary, old, destination, record)
                bind_directory(temporary, identity, verified_copy=True)
                install_auth_link(temporary / 'home', auth_source)
                final_digest = digest(tree_snapshot(temporary, auth_source=auth_source))
                _save(temporary / MARKER, {'id': journal['id'], 'digest': final_digest})
                for directory, _, files in os.walk(temporary, topdown=False):
                    for name in files:
                        path = Path(directory) / name
                        if not path.is_symlink():  # verified shared auth is external and not copied
                            _sync(path)
                    _sync(Path(directory))
                os.replace(temporary, destination)  # always same-device atomic publication
                _sync(destination.parent)
                _checkpoint('destination_complete')
            marker = read_json(no_links(destination / MARKER))
            if marker.get('id') != journal['id'] or marker.get('digest') != digest(tree_snapshot(destination, auth_source=auth_source)):
                raise PipelineError('Completed runtime copy cannot be verified; no source removal.')
            no_links(destination / 'owner.lock')
            leases.enter_context(file_lock(destination, 'owner.lock', 'Destination source session is already owned.'))
            if not committed:
                if record.get('thread_path'):
                    record['thread_path'] = str(destination / Path(record['thread_path']).relative_to(old))
                record.update(runtime=str(destination), runtime_layout_version=LAYOUT_VERSION, runtime_path_encoding='short-12')
                _save(manifest, record)
            _checkpoint('manifest_committed')
            retired = no_links(old.with_name(old.name + '.migrated-v2'))
            if old.exists():
                if [old.stat().st_dev, old.stat().st_ino] != journal['source_identity']:
                    raise PipelineError('Original runtime directory identity changed; no move performed.')
                if retired.exists():
                    raise PipelineError('Duplicate retired runtime directory; no deletion performed.')
                os.replace(old, retired)
                _sync(old.parent)
            if retired.exists():
                if [retired.stat().st_dev, retired.stat().st_ino] != journal['source_identity']:
                    raise PipelineError('Retired runtime identity changed; no deletion performed.')
                tree_snapshot(retired, auth_source=auth_source)  # partial deletion never follows shared auth
                _checkpoint('old_runtime_retired')
                shutil.rmtree(retired)
                _sync(retired.parent)
            journal['complete'] = True
            _save(manifest, record)
            return record


def migrate_project(store, slot_id=None):
    if slot_id is not None:
        identifier(slot_id, hashed=True)
    result = []
    for path in sorted(store.root.glob('artifacts/*/sources/*/sessions/*/manifest.json')):
        before = read_json(no_links(path.absolute()))
        if slot_id is not None and before.get('slot_id') != slot_id or before.get('retired'):
            continue
        after = migrate_runtime(store, path)
        result.append({'slot_id': after['slot_id'], 'runtime_layout_version': after.get('runtime_layout_version', 1),
                       'migrated': before.get('runtime') != after.get('runtime'),
                       'thread_id': after.get('thread_id'), 'session_id': after.get('session_id'),
                       'p0_turn_id': after.get('p0_turn_id')})
    if slot_id is not None and not result:
        raise PipelineError('No non-retired source-session slot matches the requested ID.')
    return {'sessions': result, 'migrated_count': sum(r['migrated'] for r in result), 'model_turn': False}
