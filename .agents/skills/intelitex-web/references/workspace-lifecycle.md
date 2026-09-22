# Workspace lifecycle and operations

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

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
If a start response is lost, query active jobs before retransmitting. Job/import payloads accept `request_key`; the runtime registry persists matching payload receipts. GET `/api/requests/{key}` resolves unknown job acknowledgements. Keys cannot be reused for a different payload. Mutations are not automatically retried.

Publication is automatic inside final translation. Explicit publish retries an
eligible output build without retranslating or contacting a model. Publication
readiness/currentness, not job success alone, determines completion.

## Implemented web lifecycle

GET `/api/library` returns confined sources and existing workspace links. POST
`/api/workspaces` accepts source_id and optional request_key, atomically resolving one
persisted draft/source link. D05 creates no project directory, manifest, checkpoint or
model request. POST `/api/workspaces/{id}/prepare` performs real supervised import.
An incomplete failed import is not automatically deleted or overwritten.

PATCH sections/settings use current config revision and explicit model-change
confirmation. D02 rules are documented in the decision register and enforced in the
application. GET section preview returns bounded escaped-text pages.

POST archive/restore take lifecycle revision. Archive is reversible metadata, rejects
active imports as well as other active/cleanup ownership, and supports persisted drafts
without creating a project directory. Draft archive state and revision live in the
root-owned catalog; prepared projects retain `web.lifecycle.json`. Archive preserves
all project evidence. Restore does not Run. Reader includes archived books. Drafts
are not automatically imported by opening them.

D04 is resolved: automatic publication displays disabled Publishing…; no Pause and
no duplicate publish. Terminal publication failure enables the backend’s publication-only
retry. Primary actions never infer complete publication from job success.
