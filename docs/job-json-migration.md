# Portable job records in JSON

Jobs, request receipts and retained recent activity now live under
`<workspace-root>/.runtime/`. SQLite is not used by the runtime registry, even as
an in-memory query engine. Checkpoints and translation artifacts retain their
separate existing JSON persistence.

`registry.json` stores format, stable scope ID, event allocator floor and the
committed job-ID → record-hash mapping. `jobs/<hash>.json` contains a job, its
request receipts and at most 120 recent events. A write fsyncs the new record,
then atomically replaces/fsyncs the manifest. Only the manifest commits it.
Unreferenced candidates after interruption are never adopted. Superseded record
versions are removed after commit; cleanup failure cannot undo a receipt. A
missing/corrupt referenced file prevents startup rather than losing history.
After a write failure, further mutations require restart/recovery.

The same commit reserves a job and the request key that identifies its launch.
After a lost HTTP response, retry retrieves the original job; conflicting reuse
of a key remains an error. Existing request fingerprints are preserved. New
default supervisor fingerprints exclude host-specific root/project locations.
API request fingerprints already derive from operation and payload.

Active process ownership stays in the supervisor's memory. Saved active states
become `abandoned` at startup and their PID is cleared; no historical PID is
adopted or signalled. Records preserve scope/workspace/job IDs, not operational
absolute project paths. Moving the root reconstructs those paths from the new
`--workspace-root`. Historical migration evidence retains the old values solely
for audit. This does **not** yet migrate the separate source-location references
in `book.json` or the Library metadata catalog.

The registry reconstructs indexes in memory once at startup. Active/latest-job
lookups and the bounded activity query do not scan/decode global history.
`GET /api/jobs` and SSE snapshots contain active jobs plus the latest job of each
workspace. Explicit job/receipt lookups still retrieve old records. The frontend
removes jobs omitted by a newer snapshot, while preserving live evidence that
arrived after an older HTTP snapshot was taken.

SSE replay is bounded globally to 1,000 events in memory. Each job persists up to
120 recent events; workspace activity returns up to 120. Snapshots reconcile
current state when older events are no longer retained. Detailed model attempts
and token accounting remain in workspace artifacts. No history-pruning policy
for job records/request keys is introduced by this migration.

## Explicit migration

For a stopped server:

```sh
intelitex migrate-jobs --workspace-root /new/workspaces --source /old/state/jobs.sqlite3
```

When the workspace root itself has already moved, specify its old identity with
`--previous-root /old/workspaces`. That is a one-time legacy migration parameter,
not a runtime path alias. New JSON registries need only the current root.

To switch the existing server without launching a second server:

```sh
intelitex migrate-jobs --workspace-root /data/workspaces --on-reload
intelitex reload --workspace-root /data/workspaces
```

The first command performs a read-only rehearsal and records explicit migration
intent. After the old server drains and releases its registry lock, the new
process takes a fresh locked snapshot and completes the migration before opening
the JSON registry. This is an explicit one-time migration, not an automatic
SQLite fallback. A startup with an existing legacy database and no JSON registry
or explicit migration intent fails with instructions.

Migration validates the exact schema and integrity, including committed WAL
content. It exports all jobs, request keys and retained events belonging to the
selected old root into `history/legacy-export.json` inside `.runtime`. This full
export preserves older events beyond the new UI buffer. The entire staged
registry is published with one directory rename. Original DB/WAL/SHM files remain;
other scopes in a shared legacy database are reported, not silently discarded.
Read locks may change SQLite SHM read marks during migration; the audit baseline
is taken after snapshot reads. Subsequent runtime never opens those files.

```sh
intelitex migrate-jobs --workspace-root /data/workspaces --verify
```

Verification checks the export checksum, committed record checksums/structure
and retired DB/WAL/SHM hashes. After moving to another host the historical old
paths may be unavailable: this audit can report that fact without making them
dependencies of the active registry.

## Verification

`tests/test_job_migration.py` covers WAL, complete export, request conflicts,
bounded snapshots/replay, root/HOME/CWD changes, removal of legacy SQLite,
forbidden runtime SQLite access, real process death before/after record commit
and migration publication, stale PIDs, write failure, missing/corrupt records,
unknown legacy schemas, and exclusive ownership during reload migration.
Runtime, HTTP and CLI suites cover worker lifecycle, SSE, cancellation, graceful
shutdown, receipt recovery and reload. Frontend tests cover bounded snapshot
replacement and delayed HTTP responses arriving after newer live events.

## Applied migration — 2026-09-27

The registry for `/home/user/translations` was rehearsed on an independent
SQLite backup, then migrated through the existing server's reload. The server
returned on the same PID and port; no second production server was started.

- 53 jobs and all 52 request receipts migrated; all 10,898 retained legacy events
  preserved in the JSON migration export.
- Every individual job API response and every request receipt matched the old
  registry. Workspace listings and salvation-02 activity were unchanged.
- All 28,313 pre-existing protected workspace files retained their SHA-256 hashes.
  No translation/model invocation or publication was performed on live books.
- Retired DB/WAL/SHM verification passed after API reads; the server had no open
  `jobs.sqlite3` descriptors.
- The normal jobs response now returns 8 latest jobs instead of 53 historical
  jobs: 4,704 bytes instead of 31,899 bytes. A small 12-request local sample had
  median response time 1.32 ms after versus 3.67 ms before; this is not a full-page
  performance benchmark.
- Offline browser tests covered targeted translation, saved-pass status after
  reload, stream bursts and reconnect. Read-only production checks covered Live
  activity at 1440×1000 and 390×844, with no console errors or horizontal overflow.
