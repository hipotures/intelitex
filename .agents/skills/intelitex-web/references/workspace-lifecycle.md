# Workspace lifecycle and operations

B: `server/service.py`, `application/imports.py`, `runtime/models.py`,
`runtime/worker.py`, `docs/server-api.md`. R: v33 Work/Library/Prepare/Archive controls.

## Existing import

GET /api/import-sources lists confined folder IDs. It is not a generic filesystem
browser, EPUB upload route or rich cover/title library. Import is optional and disabled
without the server's explicit import root. Existing imported projects still work.
POST /api/imports receives workspace_id and source_id plus allowlisted options.
It schedules a validated ImportJobSpec through the existing supervisor.

Sources are HTML/XHTML directories, including extracted EPUB directories. Do not
claim raw .epub archive upload support. Destinations must not preexist; worker creation
is atomic. `book.json` is the completed-import marker; the job can be followed by ID/SSE
before workspace discovery includes it. Never create a destination in React first.
A failed/cancelled import can leave a partial directory; no auto-delete/overwrite.

No client-supplied root paths, ports/endpoints, shell commands or credentials. Source
IDs reject traversal/escaping symlinks. Previous volume is another workspace ID;
OPF is relative to the confined source. `sidecar_txt` explicitly allows source writes,
so do not enable it by default. Import may perform existing model discovery/token
counting, but that is not a generated prose request.

## Run, Stop, Retry

POST /api/workspaces/{id}/jobs with operation analyze or translate invokes existing
use cases. For normal whole-remaining-book translation set chunk_limit=0. Omission
also means all in this API; the CLI's default five is not the web default.

A 202 means accepted. Capture the job ID; observe it and refresh pipeline/usage.
Do not auto-start another phase when the HTTP call succeeds. Retry is a new allowed
job using saved checkpoints; it is not a resume API endpoint. There is no Pause.
POST /api/jobs/{job_id}/stop with `{}` targets one owned job. Wait for stopping and
terminal/reconciled state; never remove activity or successful checkpoints on click.

Multiple workspaces can run independently. Stop, errors and browser navigation in
A must not change B. Busy in one workspace does not disable every page globally.
If a start response is lost, query active jobs before retransmitting. Existing API
has no persisted idempotency-key contract; adding one is a backend target, not an
unsupported extra JSON field. Mutations are not automatically retried.

Publication is automatic inside final translation. Explicit publish retries an
eligible output build without retranslating or contacting a model. Publication
readiness/currentness, not job success alone, determines completion.

## Target UI lifecycle not fully backed by current routes

v33 needs a rich source-card library, open/create draft before Prepare, source preview,
processing modes, model overrides, archive/restore, safe artifact download. These are
not already provided by import/jobs/capabilities. See API gaps and D02-D07.

Archive means reversible membership, not deleting or moving a book directory. Its
target must reject active writers, retain checkpoints/settings/markers/publication,
and restore the same workspace ID. No archived-query parameter or archive endpoint
exists at the recorded baseline. Do not simulate archive in localStorage or submit
a made-up POST. Mark this milestone incomplete until authorized backend work lands.

Primary actions use backend gates and the source's Prepare/Run/Review/Stop/Retry/
Publish/Finished/Open labels. UI availability must never widen a false server gate.
When API facts are missing, show an explicit unavailable/unknown state. Don't silently
choose the contradictory publishing/progress/mode policies in the earlier drafts.
