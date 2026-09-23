# Server runtime and workspace API

Start from the repository using the existing environment:

```bash
uv run intelitex serve \
  --workspace-root ./workspaces \
  --import-root ./sources \
  --bind 127.0.0.1 --port 8780
```

After changing Python code, request a full code reload without stopping the server by hand:

```bash
uv run intelitex reload --workspace-root ./workspaces
```

The command signals the locally registered `serve` process and returns immediately.
Sending `SIGHUP` directly to that process has the same effect.
The server rejects new jobs while draining. Each active translation worker finishes
its current P2, P3, P4, or P5 pass and commits its checkpoint before exiting.
Other active operations finish normally. The HTTP process then re-executes itself
on the same bind address and port and starts new translation jobs for the remaining
work. A P5 on the last chunk also finishes automatic publication before reload.
The browser's SSE connection reconnects to the restarted server. Stop and SIGTERM
retain their existing cancellation behavior. The first deployment of this feature
requires a normal server start, since an older process does not yet handle reload.

The root must already exist. Its immediate child directories are workspaces;
`book.json` marks an imported project. Workspace IDs are directory names, using
ASCII letters, digits, `_`, `-`, and single dots (maximum 128 characters, starting
with a letter or digit). Absolute paths, separators, `..`, and symlinks resolving
outside the root are rejected. Aliases resolving to the same project share the
same supervisor exclusion. This is a trusted local-filesystem application;
changing workspace directories/symlinks externally during operations is unsupported.

## Execution and ownership

```text
Browser -- HTTP / SSE --> one Intelitex server process
                           HTTP + application queries
                           one JobSupervisor + event broker
                              |          |          |
                           worker A   worker B   worker C
                           project A  project B  project C
```

There is no supervisor daemon or second HTTP process. Each worker is a separate
OS process running one existing synchronous application use case. Different
projects may run concurrently, including different provider types. The supervisor
allows at most one active mutating job per resolved project. The existing project
`.lock` remains the final exclusion boundary against other workers and legacy
CLI writers. Supervisor conflict checks hold a mutex only for lifecycle changes,
never for the duration of a pipeline operation. There is no provider/GPU scheduler.

Browser navigation, reloads, tab closure, and SSE disconnects have no effect on
workers. P1 remains whole-book analysis; human terminology approval still gates
translation. P2 → P3 → P4 → P5 remain sequential for each chunk. The default web
translation job processes all unfinished chunks (`chunk_limit: 0`); the existing
CLI default remains five. Automatic EPUB publication executes inside the same
translation worker when its final chunk completes. An automatic publication
failure retains completed translation checkpoints and remains independently
retryable with an explicit `publish` job, as in the existing application semantics.

The worker dispatch supports `analyze`, `translate`, `publish`, and a separate
validated `ImportJobSpec`. Review preparation, edits, confirmation and approval
are short application requests. No browser-supplied absolute source or arbitrary
output paths are accepted. See [the API contract](server-api.md) for every route
and input schema.

## Persistence and queries

Project truth stays in each project's checkpoints, `state.sqlite3`, atomic JSON
files, and immutable attempt artifacts. No successful pipeline checkpoint is
recomputed merely because a job is restarted. Job success is a supervision fact;
project and publication queries determine which domain work is actually complete.

`ProjectReadScope` uses `ReadStore`: SQLite `mode=ro`, `query_only=ON`, a request-local
read transaction, and no schema creation or migrations. WAL permits these queries
while another process holds the exclusive project writer lock. Combined project
and publication status uses one database snapshot and existing manifest/artifact
hash validation. A later writer commit does not invalidate that earlier snapshot.
Atomic publication JSON may reflect a nearby commit; the query still validates
it against its own checkpoint snapshot. No read snapshot or project lock is held
for the lifetime of SSE. Usage inspection reads existing artifact reports without
claiming the writer lock.

The separate runtime registry is `$XDG_STATE_HOME/intelitex/jobs.sqlite3`, falling
back to `~/.local/state/intelitex/jobs.sqlite3` when XDG state home is absent or not
absolute. It uses WAL and private file permissions. A lifetime registry lock
prevents a second server from concurrently owning the same registry or abandoning
its live jobs. It stores job metadata and the latest 1,000 events per job. Detailed
prompts, responses, credentials, auth files, and raw attempt evidence are excluded.
Job rows are retained across restarts; event history is bounded per job, not a
replacement for project evidence. A server exposes only records for its configured
workspace root.

On startup, earlier `starting`, `running`, and `stopping` records become
`abandoned`. Version 1 does **not** re-adopt workers or signal historical PIDs.
After an abrupt server crash an orphan may still finish or retain the project
lock; restarting a job cannot bypass that lock. Inspect such processes locally
before retrying. Graceful shutdown stops all workers owned by this server. Completed workers are
removed from the in-memory ownership map after their monitor has finished process
reaping and any stop/descendant-cleanup thread has returned. Job and event history
remains in the registry.

On Ctrl-C or SIGTERM, the CLI first rejects new supervised jobs and closes SSE
streams, then gives in-flight HTTP requests up to 30 seconds to finish before
Uvicorn's bounded cancellation fallback. The adapter waits for that drain instead
of cancelling a valid slow read after one second and printing an ASGI traceback.
It then requests cancellation of all owned workers through the same bounded Stop
procedure before closing the runtime registry. Repeated
interrupts do not skip cleanup. A normal shutdown prints one short status line and
returns 0 directly; a command runner may report 130 when it also receives Ctrl-C.

## Cancellation

Stop returns promptly after setting `stopping`; a separate supervisor thread:

1. Sends SIGINT to that worker's process group and allows 15 seconds for normal
   Python unwinding, attempt recording, resource cleanup, and Codex child reaping.
2. If still alive, sends SIGTERM and waits up to 5 seconds.
3. Uses SIGKILL if the worker still has not exited, then waits for it to be reaped.

A requested stop becomes `cancelled`, including forced termination. Unexpected
process exit, malformed protocol, or a structured worker exception becomes
`failed`. Checkpoints committed before interruption remain authoritative; interrupted
attempt evidence stays local. All owned workers receive shutdown interrupts
concurrently, so grace periods are not multiplied by the number of workspaces.

This runtime uses POSIX process groups, consistent with the existing `flock`
project locks. On Linux, escalation also captures descendant PIDs and start times
from `/proc`, including detached Codex app-server children, and signals only those
owned descendants. The server is a Linux child subreaper and gives captured orphan
children up to two additional seconds to be reaped after forced termination. It
never uses a global wait that could reap another worker. Normal cancellation
relies on existing provider cleanup; the
hard fallback cannot guarantee normal application cleanup. Codex's per-request
isolated HOME/CODEX_HOME/CODEX_SQLITE_HOME and app-server creation are unchanged.

## HTTP API

Responses are JSON except SSE. Errors have a stable envelope:
`{"error":{"code":"review_revision_conflict","message":"Review state changed.","details":{}}}`.
No provider exception body or credential-bearing file is served. The complete
[API contract](server-api.md) covers pipeline, Review, Reader, import, settings,
capabilities, jobs, usage, and errors.

Example requests (existing saved project profiles supply provider configuration):

```bash
curl -H 'Content-Type: application/json' \
  -d '{"operation":"translate","chunk_limit":0}' \
  http://127.0.0.1:8780/api/workspaces/book-a/jobs

curl -H 'Content-Type: application/json' \
  -d '{"operation":"analyze","profile":"my-profile"}' \
  http://127.0.0.1:8780/api/workspaces/book-b/jobs

curl -H 'Content-Type: application/json' \
  -d '{"operation":"publish","target_language":"pl"}' \
  http://127.0.0.1:8780/api/workspaces/book-c/jobs

curl -N http://127.0.0.1:8780/api/events
```

Unknown job fields/operations return 400. An active job for the same resolved
workspace returns 409. A malformed workspace ID or escaping symlink returns 400;
unknown workspaces/jobs return 404. Project query validation failures return 422.

Binding defaults to loopback. There is no authentication layer for untrusted
networks; explicitly binding elsewhere exposes local control to that network.
Host validation prevents arbitrary DNS-rebinding hosts. Mutation requests require
JSON, one Content-Length, a body of at most 16 KiB, and same-origin browser
Origin/Fetch Metadata headers when supplied. CLI requests without browser headers
are supported. No permissive CORS or arbitrary static-file endpoint is provided.

## Progress protocol and reconnection

Worker stdout is private JSONL carrying serialized `ProgressEvent` metadata and
one explicit terminal result, failure, or cancellation. Human rendering is never
parsed. The supervisor adds `job_id`, `workspace_id`, monotonic per-job `sequence`,
UTC `timestamp`, and a global persisted `id`. Current pass/task/chapter/chunk IDs,
profile/provider/model, waiting/preflight measurements, usage counters, and
publication events retain their meaning; UTF-8 bytes are never relabelled tokens.
The metadata allowlist in `runtime/protocol.py` deliberately excludes free-form
messages, provider error bodies, and filesystem path fields (`project`,
`output_path`, `recovery_path`, `review_path`). Job snapshots and event replay also
strip path fields from history written by earlier versions. Publication status
exposes identity and readiness without output paths. Extend the allowlist explicitly when adding new fields.

`/api/events` accepts `workspace_id`, `job_id`, and `after` query parameters.
Reconnects can send `Last-Event-ID` (which takes precedence over `after`). Every
connection first receives `event: snapshot` containing jobs and the current global
cursor. It then receives `event: progress`, an SSE `id`, and the event envelope.
With no requested cursor, live delivery starts after the snapshot cursor; with a
cursor, retained events after it are replayed. Clients should use per-job sequences
to avoid applying older replay events over a newer snapshot. Missing pruned
history is recovered through the snapshot and project status/usage queries, not
invented events. Ten-second keepalives help detect disconnected clients. Slow or
disconnected subscribers never cancel jobs or block worker event ingestion.

## Modules and compatibility

- `runtime/models.py`, `protocol.py`, `worker.py`: DTOs, metadata protocol, execution.
- `runtime/supervisor.py`, `processes.py`: lifecycle, exclusion, owned cancellation.
- `runtime/registry.py`, `events.py`: WAL job/event persistence and replay/wakeups.
- `application/workspaces.py`, `sessions.py`, `infrastructure/read_store.py`:
  confined workspace discovery and read-only project snapshots.
- `server/service.py`, `http.py`, `__init__.py`: control/query adaptation, HTTP/SSE,
  and server lifetime composition.

The main server exposes Review and Reader APIs without serving their old HTML or
JavaScript. Existing `review` and `reader` CLI commands remain standalone compatibility
servers; the supervisor never launches extra HTTP-server jobs. Review acquires the
project lock for preparation and individual operations. Main-server Reader reads
hold no writer or Reader lock; each marker mutation holds the same `.reader.lock`
used by the standalone Reader. A running standalone Reader can therefore temporarily
exclude API marker writes while its lifetime lock is held. Ordinary API reads still
work. There is no browser-lifetime Store, application session, or project lock.
All existing CLI commands remain supported. Production `serve` now uses the FastAPI/Uvicorn adapter in `bookpipe/server/asgi.py`, shares route dispatch with the compatibility HTTP adapter, and serves the built React interface from `web/dist`. There remains one supervisor and one ASGI worker. See `docs/web/contract-map.md`.

Tests use real subprocesses with injected offline workers and the existing mock
provider, covering supervision, isolation, escalation, restart recovery, WAL
queries during writes, protocol privacy, HTTP security, SSE replay/disconnection,
and real worker checkpoint resumption plus automatic EPUB publication.
