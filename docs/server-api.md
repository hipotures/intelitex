# Single-server API contract

Run `uv run intelitex serve --workspace-root ./workspaces` with an existing
workspace directory. Optionally supply `--import-root ./sources` (also existing).
There is one HTTP process and one supervisor. The production React interface is served on this origin; no old standalone UI
assets are served. See [runtime ownership and cancellation](server-runtime.md).
Non-loopback binding is **trusted-network operation**, with no authentication.

## Routes

All request/response bodies are JSON except `/api/events`. Successful queries and
short mutations return 200; job start/stop returns 202. `{id}` is a confined
workspace ID, not a path. Unlisted query parameters and mutation fields are rejected.

| Method | Route | Input → output |
| --- | --- | --- |
| GET | `/api/health` | `{status:"ok"}` |
| GET | `/api/capabilities` | `{import_enabled,review,reader,sse,multi_workspace}` booleans |
| GET | `/api/workspaces` | `{workspaces:[{workspace_id,active_job}]}` |
| GET | `/api/workspaces/{id}` | `{workspace_id,status,active_job}` |
| GET | `/api/workspaces/{id}/pipeline` | Pipeline snapshot below |
| GET | `/api/workspaces/{id}/usage` | Retained usage by unit/pass/physical attempt |
| GET | `/api/workspaces/{id}/profiles` | Sanitized effective settings (same as `/settings`) |
| GET | `/api/workspaces/{id}/settings` | Settings schema below |
| GET | `/api/profiles` | Sanitized installed defaults/profiles, including before the first import |
| GET | `/api/import-sources` | `{sources:[{source_id}]}` immediate source folders and packed EPUB files |
| GET | `/api/library` | Legacy complete `{configured,sources}` response |
| GET | `/api/library?limit=12&after={cursor}` | Bounded source page `{configured,sources,next_cursor}`; `limit` is 1–40; omit `after` for the first page |
| POST | `/api/imports` | Import input below → job |
| POST | `/api/workspaces/{id}/jobs` | Pipeline job input below → job |
| GET | `/api/jobs` | `{jobs:[job],cursor}` |
| GET | `/api/jobs/{job_id}` | Job |
| POST | `/api/jobs/{job_id}/stop` | `{}` → job |
| GET | `/api/events` | SSE snapshot/replay/live progress |
| POST | `/api/workspaces/{id}/review/prepare` | `{}` → review draft |
| GET | `/api/workspaces/{id}/review` | Review draft |
| GET | `/api/workspaces/{id}/review/terms/{term_id}/evidence` | Evidence below |
| PATCH | `/api/workspaces/{id}/review/terms/{term_id}` | `{revision,select?,custom?,reviewed?,user_notes?}` → `{term,summary,revision}` |
| POST | `/api/workspaces/{id}/review/bulk-review` | `{revision,term_ids:[string]}` → `{terms,changed_count,summary,revision}` |
| POST | `/api/workspaces/{id}/review/confirmation` | `{revision,confirmed:boolean}` → `{summary,revision}` |
| POST | `/api/workspaces/{id}/approve` | `{revision}` → `{approved_terms,stale_chunks,pipeline}` |
| GET | `/api/workspaces/{id}/reader` | `{book_fingerprint,title,chapters:[{id,title}]}` |
| GET | `/api/workspaces/{id}/reader/progress` | `{total_words,last_chapter,chapters:[{id,start,words,blocks:[{id,start,words}]}]}` |
| GET | `/api/workspaces/{id}/reader/chapters/{chapter_id}` | Chapter below |
| POST | `/api/workspaces/{id}/reader/context` | `{chapter_id,block_id,position}` → Context Helper result |
| GET | `/api/workspaces/{id}/reader/markers` | `{format_version,book_fingerprint,markers,_revision}` |
| POST | `/api/workspaces/{id}/reader/markers` | `{revision,chapter_id,block_id,start,end,text}` → `{marker,created,revision}` |
| DELETE | `/api/workspaces/{id}/reader/markers/{marker_id}` | `{revision}` → `{deleted,revision}` |

All mutations, including PATCH and DELETE, enforce the same Host, Origin and
Fetch Metadata policy. JSON requires one Content-Length, no Transfer-Encoding,
and at most 16 KiB. Duplicate object keys, non-finite numbers and non-object
bodies are rejected. There is no CORS grant, upload or arbitrary file endpoint.

## Jobs and current state

Job input is `{operation:"analyze"|"translate"|"publish",profile?,chunk_limit?,target_language?}`.
`profile` selects an existing profile for analyze/translate. `chunk_limit` is a
nonnegative integer, only for translation; omitted/zero processes all unfinished
units. `target_language` is only for publication, defaults to `pl`, and must be a
BCP-47-style language identifier. Publication does not accept `profile`.

A job contains `job_id`, `workspace_id`, `operation`, `state`, `pid`, `started_at`,
`finished_at`, `exit_code`, `sequence`, `last_event`, `error`. Nullable fields are
null until known. States are `starting`, `running`, `stopping`, `succeeded`,
`failed`, `cancelled`, `abandoned`. Private project/root paths are excluded.

SSE accepts optional `workspace_id`, `job_id`, `after`, and the `Last-Event-ID`
header. A workspace import can be followed before it appears in discovery. Every
connection begins with `snapshot` (jobs and global cursor), then `progress`
envelopes with persisted event IDs. Reconnect replay survives browser disconnects;
workers do not belong to subscribers. Refresh pipeline/review/Reader/usage queries
after relevant events. SSE is activity/history, not a replacement for project truth.

Workspace `status` contains title, series identity/volume, narrative section count,
chunk IDs/statuses, analysis/approval flags, term/candidate counts, chapter completion
counts, translation completion and publication status. Publication DTOs expose
`state`, `translation_complete`, `current`, `target_language`, fingerprint,
generation metadata, title/creators/source language, and generic nullable
`last_error`/`last_failure`. They never expose output paths or raw exceptions.

Usage contains `scope`, `warning`, and `units`. Each unit identifies chapter/chunk
or analysis unit, carries pass reports and aggregate token/timing coverage. Passes
contain provider/profile/model identities, physical/provider-call/retry/failure
counts, accepted/recovered result identity, preflight measurement, usage aggregates,
cost estimates and attempts. Attempts expose generation/validation/acceptance
status, checkpoint association, provider-contact status, reported usage/timing and
cost. Unknown usage stays null with explicit coverage; no raw provider reports,
request bodies, attempt paths or credential configuration are returned.

## Pipeline read model

`Application.workflow.pipeline` uses a short read-only checkpoint snapshot and
atomic manifests, draft, retained usage and publication records. It returns:

- `workspace_id`, `stage`, `active_job` (the latter attached by the server);
- `analysis:{complete,planned,units:[{id,chapter_id,state,attempt_result,failed_attempt_count}]}`;
- `review:{prepared,current,revision,summary}`, `approved`;
- `translation_complete`, `chapters:[{id,title,unit_ids}]`;
- `excluded_sections:[{id,title,role}]` from the imported non-narrative manifest;
- `units:[{id,chapter_id,status,passes:{"2":pass,"3":pass,"4":pass,"5":pass}}]`;
- `publication`, `actions`.

Analysis units are not guessed before planning. Their states are `pending`,
`completed` (receipt with verified checkpoint), or `error` (invalid receipt/checkpoint).
Translation unit status is the saved `pending`/`done`/`stale` value. Each pass has
`checkpoint_state`, `retained_count`, `attempt_result` (nullable existing usage
result status), and `failed_attempt_count`. P2–P4 show `pending` or `retained`:
retained checkpoints can have old input fingerprints, so retention alone does not
claim that a pass is current. P5 additionally shows `completed`, `stale`, or
`error` based on its registered, hash-verified final artifact. Failed historical
attempts do not erase successful checkpoints or turn a completed unit into an error.

Stage is derived: `analysis`, `review`, `translation`, `publication`, `complete`.
`approved` means committed terminology, not that every subsequent draft edit has
been approved. `review.current` compares the draft's source and analysis revision
with the snapshot. Review confirmation and terminology approval remain distinct.

Actions `analyze`, `prepare_review`, `review`, `approve`, `translate`, `publish`
contain `{allowed,reason}`. Reasons are null when allowed, otherwise
`workspace_busy`, `analysis_required`, `analysis_complete`,
`review_preparation_required`, `review_stale`, `review_not_confirmed`,
`approval_required`, `translation_complete`, `translation_required`,
`publication_current`, or `publication_not_ready`. Gating is computed in Python. Availability is advisory at
the snapshot instant: commands still enforce authoritative locking/validation.

## Review and approval concurrency

Preparation uses the existing ReviewSession to create/hydrate the persisted draft,
then closes it. A draft contains `_revision`, source/analysis fingerprints,
`confirmed` and `terms`. Terms expose IDs, source, aliases, category, candidates
(number/text/reasons/confidence/evidence), meaning notes, observations, source
evidence, selection/custom text, reviewed flag/method and user notes. The summary
contains total/reviewed/unreviewed/uncertain/noted/category counts and confirmation.

Evidence contains `term_id`, `entries`, `warnings`, `choice_pending_approval`.
Entries contain block/chapter/unit IDs, source text/kind, verified Polish text
(nullable), stage/status, and a generic message if a checkpoint cannot be read.
Raw filesystem exceptions are not returned.

Every API edit, bulk operation, confirmation and approval requires the loaded
revision. Use `_revision` from a load or `revision` from a mutation. Each operation
acquires the project lock, checks the digest, mutates persisted state atomically,
and releases the lock. Competing writes yield `workspace_busy` while locked or
`review_revision_conflict` after the digest changes. Reload before retrying.

`ApproveCommand.expected_revision` is checked **inside OperationScope before any
approval backup/commit**, not only in HTTP. Existing source/analysis revision,
selection and confirmation validation remains authoritative. Approval makes no
model call and preserves the existing dependent-chunk staleness rules. CLI
`--accept-defaults` remains available locally; it is not exposed as a web bypass.
No browser page owns a Store, project lock or application session.

## Reader

Main-server requests reuse ReaderContext and MarkerRepository through ReaderService.
Reads own only request-local contexts/read-only checkpoint connections. There is
no mutable Store or writer lock; translation in this or other workspaces may run.

A chapter returns `id`, `title`, `blocks:[{id,kind,text,formatting?}]`, `complete`,
`stale`, optional `unavailable:{after_blocks,reason}`, and optional `warning`.
Formatting spans contain `start`, `end`, `style`. Only checkpoint-verified P5 text
is readable; stale output remains readable with a warning. Chapter/block IDs must
belong to the manifest. There is no path-based chapter lookup.

Context positions and marker offsets are Unicode code-point offsets. Context
returns `{recognized:false}` or `recognized`, `matched_text`, `display_name`,
`title`, `attributes`, `statements`, `earlier_mentions`, `same_block_context`,
`range:{start,end}`. Existing evidence cutoff/spoiler behavior is preserved.
Progress describes available text and word offsets, not a persisted browser bookmark.

Markers contain `id`, `chapter_id`, `block_id`, `start`, `end`, `text`. Mutations
hold the existing `.reader.lock` only for the request, check the marker digest,
and validate the selected text against verified translation. Stale text/revisions
return `marker_revision_conflict`. Standalone Reader still holds this same lock
for its session, so API writes can return `workspace_busy` while it is open;
API reads remain available. Standalone Review/Reader commands and UIs are unchanged.

## Confined import

Without `--import-root`, capabilities report `import_enabled:false` and both
import routes return `import_disabled`. Discovery lists immediate folder and packed
EPUB identifiers. A supplied source ID may identify a nested folder, always under
import-root. The existing importer accepts HTML/XHTML folders, including extracted
EPUB folders. A packed EPUB is safely unpacked into the project at Prepare and then
uses the same importer; no archive upload endpoint is introduced.

Required input: `{workspace_id,source_id}`. Optional fields:

| Field | Meaning / validation |
| --- | --- |
| `previous_volume` | Existing workspace ID under workspace-root; reuses existing series handoff/config inheritance |
| `opf` | Relative existing OPF file inside the selected source folder; unavailable for packed EPUBs |
| `input_encoding` | Existing importer encoding override |
| `chapter_mode` | `auto` (default), `file`, `headings` |
| `chapter_selector` | Existing HTML chapter selector |
| `include_glob` | Relative include filter, no traversal/absolute path |
| `sidecar_txt` | Boolean, default false; explicitly permits existing importer sidecar text writes in the confined source |
| `whole_section_limit` | Positive integer |
| `profile` | Existing profile name |
| `pass_profiles` | Object with keys `"1"`–`"5"` and existing profile names |
| `model` | Optional model identifier, not a local filesystem path |
| `context_size` | Positive integer |
| `thinking` | `on` or `off` |

Credentials, endpoints, ports, executables, auth directories and arbitrary provider
objects cannot be supplied. Provider configuration comes from installed settings,
existing profiles and optional previous-volume configuration. Operators configure
credentials locally. Profile/model selection retains existing ImportBookCommand
semantics; model discovery/token counting may contact the configured provider.

Source IDs reject absolute paths, `..`, empty/dot segments and backslashes.
OPF paths are source-confined. Escaping source symlinks (including nested/sidecar
symlinks) are rejected. Destinations are exactly `workspace_root / workspace_id`,
with no destination symlinks or preexisting directories. A separate ImportJobSpec
is revalidated at launch and in the worker. The worker atomically creates the
new destination, then ImportBookCommand holds the normal project lock. The
supervisor prevents simultaneous jobs for the same resolved destination, and
atomic creation prevents overwriting a directory created externally in the interim.
Do not externally move/replace trusted source or workspace trees during operations.

Import shares job state, cancellation, persistent history and SSE. Browser/SSE
disconnects do not cancel it. `book.json` remains the final completed-import marker;
success appears immediately in workspace discovery. A failed/cancelled import may
leave a partial directory for local inspection. Retry with a new workspace ID or
have the operator inspect/remove that partial directory; the API never deletes or
overwrites it automatically. No second supervisor or import HTTP process exists.

## Configuration privacy

Global `/api/profiles` reads installed defaults; workspace settings resolve saved
project configuration. Settings/profiles return `source` (`project` or `defaults`), `default_profile`,
`pass_profiles`, `profiles`, `resolved_passes`, `whole_section_char_limit`, and
`memory_tokens`. Each profile has a name, source (`configured`/`builtin`), provider,
model, enabled flag, context size, reasoning effort, thinking setting, planning
reserve and maximum output tokens. Resolved passes additionally expose selection
provenance. Nullable fields represent unspecified settings. Legacy settings are
adapted in memory without migration or provider construction.

Only allowlisted fields are projected. Endpoint URLs, credential references or
values, headers, provider options, executable/runtime locations, HOME/CODEX_HOME,
auth databases and raw profile dumps are never returned. Local model paths are
redacted. Job profile overrides remain supported. Profile editing, diagnostics,
live smoke, credential administration and raw evidence access remain CLI/local
operations. Text export remains a CLI utility; Reader exposes verified content.

## Errors

All errors use `{error:{code,message,details:{}}}` with static safe messages.
Clients must branch on `code`, not exception text. No traceback is returned.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Unknown/missing fields, invalid types/IDs, malformed JSON/content type, traversal |
| 403 | `origin_rejected` | Host, Origin or Fetch Metadata rejected |
| 403 | `import_disabled` | Server has no import root |
| 404 | `not_found` | Unknown route/workspace/job/source |
| 409 | `workspace_busy` | Authoritative project/Reader lock, supervisor conflict or shutdown |
| 409 | `destination_exists` | Import destination already exists |
| 409 | `review_revision_conflict` | Loaded review digest no longer matches |
| 409 | `marker_revision_conflict` | Marker digest/text changed |
| 413 | `body_too_large` | JSON exceeds 16 KiB |
| 422 | `invalid_project_state` | Existing application validation rejects the operation, including invalid term/chapter/selection |
| 500 | `internal_error` | Unexpected failure; inspect locally |

## Implementation audit

Baseline audited on clean `main` at `912e297`: application services already owned
import, pipeline execution, Review repository/session and approval, Reader context
and markers, publishing, profiles and usage. Main serve exposed discovery/status,
usage, jobs and SSE only; remaining workflows were CLI/standalone-server features.
This change adds workflow/configuration queries and request-scoped service entry
points, extends approval with atomic revision checking, and adapts those existing
services to the main HTTP server. It does not duplicate translation, terminology,
Reader context, publishing or import algorithms.


## Production v33 additions

See [the audited web contract map](web/contract-map.md) for draft/linkage, section configuration, preparation validation, archive/restore, approval freshness, idempotency receipts, historical profile palette and vetted publication download contracts. Existing request fields/error codes remain compatible. Production uses FastAPI; both delivery adapters share explicit dispatch.
