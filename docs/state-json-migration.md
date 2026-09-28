# Checkpoint state in JSON — first migration stage

Workspace checkpoint state is now durable in `state/HEAD.json`, immutable JSON
commit records under `state/commits/`, and referenced table snapshots under
`state/objects/`. Store, read scopes, Reader and Review evidence all use this state.
The retired `state.sqlite3` is never opened by runtime readers or writers. Missing,
corrupt or unmigrated state is an error, with no fallback to that database.

This stage moves **all** checkpoint records: kv, jobs, merged receipts, terms,
facts, chunks, history, hidden row order and AUTOINCREMENT counters. JSON columns
are exported as native JSON values. Only noncanonical legacy JSON text is retained
as encoding metadata where necessary to preserve byte-sensitive revisions;
SQL NULL and JSON null remain distinct. Unknown tables/columns are rejected.

SQLite is used solely as an in-memory transactional query engine for the existing
Store interface. It is reconstructed from JSON, owns no durable state and opens
no workspace database file. The later persistent, indexed SQLite cache for all
book documents has **not** been introduced by this stage. Runtime supervision's
separate `jobs.sqlite3` was outside this checkpoint migration. The subsequent
[job registry migration](job-json-migration.md) moves its durable records to JSON too.

## Transaction boundary

A commit writes changed table snapshots and a commit record, fsyncs their directory
entries, then atomically replaces and fsyncs HEAD. HEAD is the commit point;
unreferenced candidate files are never adopted automatically. Table objects are
content-addressed and shared between revisions. Old revisions remain available.
An advisory commit lock and an expected-HEAD comparison prevent stale writers
from replacing newer state. Read connections retain one immutable snapshot.

Exceptions before HEAD preserve the previous revision. A process killed after
HEAD replacement sees the committed revision on restart. Closing a connection
without commit discards pending changes, as before. Application-level project
locks and artifact checks remain mandatory. Existing operations spanning other
workspace files retain their existing recovery semantics; this stage does not
claim atomic transactions over every file in a workspace.

Table snapshots are intentionally a first-stage persistence format. A change to
one term currently rewrites the terms snapshot, while unchanged table objects are
reused. Finer document partitioning and the indexed cache remain subsequent work.

## Explicit migration and verification

Drain/stop the server and worker processes before migrating the configured root.
Use the same code version for the server and CLI after the cutover; old executables
cannot be assumed to understand the new persistence contract.

```sh
intelitex migrate-state --workspace-root /home/user/translations
intelitex migrate-state --workspace-root /home/user/translations --verify
```

`--project PATH` limits either operation to one workspace. Batch mode includes
every immediate prepared workspace, including archived workspaces, and does not
descend into historical backup folders. It stops on a failed migration; completed
workspaces remain committed and a rerun validates them without replacing state.

Migration takes a read-only snapshot of the legacy database, including WAL,
validates integrity/schema, creates and verifies an independent SQLite backup in
`history/state_migration/`, reconstructs every value in a disposable database, and
publishes JSON only after equality checks. The original database is retained.
`retired-files.json` records checksums of the retired DB/WAL/SHM files after
migration. `--verify` validates the current JSON and detects changes to those
files by reading bytes, without opening them through SQLite. Drift returns a
nonzero exit code and is never imported into the current state automatically.

Backups made before approval, profile changes and P1 reset are now JSON snapshots.
Do not prune `state/objects` or `state/commits` manually: earlier commits may still
reference them. Back up the entire state directory and immutable book artifacts.

## Validation

`tests/test_state_migration.py` covers exact record/row order preservation,
AUTOINCREMENT gaps, native JSON/SQL-null distinctions, WAL migration, idempotent
batch migration, retired-database drift, refused fallback, corruption, rollback,
stale writers, stable readers and real process death before/after HEAD publication.

`tests/test_state_migration_api.py` compares real HTTP responses before/after a
legacy migration for both server adapters: pipeline, summary, Review, Reader,
chapter contents, terminology evidence and publication selection. It forbids
opening the retired database, edits/approves terminology, checks invalidation,
then deletes the retired database in the disposable fixture and reads again.

Run these alongside the existing pipeline, publication, series, runtime and HTTP
regression suite. No real provider or browser is required for these storage/API
tests. Browser load-to-interactive measurement is a separate UI validation.

## Migration verification on 2026-09-27

All 14 prepared workspaces under `/home/user/translations` were first rehearsed
on independent copies of their legacy databases, then migrated with the server
stopped and no active jobs. Every migrated database passed full record equality
and backup verification. The server was restarted with JSON-only checkpoint
persistence.

The 42 captured pipeline/summary/Review HTTP responses were identical before and
after migration, including expected missing-Review responses for unanalysed
projects. All 24,982 pre-existing protected files retained their SHA-256 hashes.
The retired DB/WAL/SHM checksums remained unchanged after runtime API reads, and
the server had no open retired database descriptors. No model was called.

The pre-existing `test_p2_p5_canonical_fingerprint_fixtures` mismatch remains a
separate issue: `response_schema` is unchanged from repository HEAD. Migration
preserves recorded fingerprint values exactly instead of updating these fixtures
or rewriting historical checkpoints.

Final backend run: `python -m pytest -q` — **984 passed, 1 failed** in 326.73 s;
the sole failure is that existing fingerprint fixture. All **17 migration tests**
passed. The cancellation fixture now allows 1.5 s for graceful subprocess shutdown
on a loaded host while retaining its exact exit-code and escalation assertions.
The migrated JSON represents **19,739 records** across the 14 workspaces.
