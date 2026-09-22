# Server runtime and workspace API

Start from the repository using the existing environment:

```bash
uv run translate.py serve \
  --workspace-root /home/user/translations \
  --bind 127.0.0.1 --port 8780
```

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

The worker dispatch currently supports `analyze`, `translate`, and `publish`.
Import/preparation remains a CLI operation in this version. Adding it requires a
validated operation input and worker dispatch branch, without changing process
supervision. No browser-supplied source or arbitrary output paths are accepted.

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
before retrying. Graceful shutdown stops all workers owned by this server.

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

Responses are JSON except SSE. Errors use HTTP status codes and a safe `error`
message. No provider exception body or credential-bearing file is served.

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/health` | Health status |
| GET | `/api/workspaces` | Imported workspace IDs and active jobs |
| GET | `/api/workspaces/{id}` | Checkpoint-derived status, publication state, active job |
| GET | `/api/workspaces/{id}/usage` | Structured retained usage by unit |
| GET | `/api/jobs` | Jobs and global event cursor |
| GET | `/api/jobs/{job_id}` | Job state, PID, timestamps, exit code, last event and error |
| POST | `/api/workspaces/{id}/jobs` | Start one job; `202 Accepted` |
| POST | `/api/jobs/{job_id}/stop` | Request cancellation; `202 Accepted`, body `{}` |
| GET | `/api/events` | Current job snapshot followed by SSE events |

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
messages and provider error bodies; extend it explicitly when adding new fields.

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

The main server is the target host for future Review/Reader routes and the final
web UI. This change provides the runtime/API only. Existing `review` and `reader`
CLI commands remain standalone compatibility servers; the supervisor never
launches or manages extra HTTP-server jobs. Review now acquires the project lock
for initial draft preparation and individual draft operations, not for the lifetime
of an open page/session. Reader retains its separate marker-file lock. Human review
and `approve` remain available through the existing workflow.

Tests use real subprocesses with injected offline workers and the existing mock
provider, covering supervision, isolation, escalation, restart recovery, WAL
queries during writes, protocol privacy, HTTP security, SSE replay/disconnection,
and real worker checkpoint resumption plus automatic EPUB publication.
