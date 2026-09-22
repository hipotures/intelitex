# Verified API contract and remaining UI gaps

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

B: `2bb9aa2df178355502a7529e460fca12804ef377`. Inspected actual Handler dispatch and WorkflowQueries, with the
repository's `docs/server-api.md` and `docs/server-runtime.md`. The current repository
document is the canonical detailed schema. This is a task-oriented index, not a
second automatically authoritative API. Inspect current serializers and tests.

## Real routes

Successful queries/short mutations return 200; job start/stop returns 202.
Non-event routes reject query parameters. IDs are validated identifiers, not paths.

| Method | Route | Body / result |
| --- | --- | --- |
| GET | /api/health | {status:"ok"} |
| GET | /api/capabilities | import_enabled, review, reader, sse, multi_workspace booleans |
| GET | /api/workspaces | {workspaces:[{workspace_id,active_job}]} |
| GET | /api/workspaces/{id} | workspace_id,status,active_job |
| GET | /api/workspaces/{id}/pipeline | workflow snapshot described below |
| GET | /api/workspaces/{id}/usage | retained usage by unit/pass/attempt |
| GET | /api/workspaces/{id}/profiles | sanitized effective settings |
| GET | /api/workspaces/{id}/settings | same schema as profiles; read-only |
| GET | /api/profiles | global configured/builtin defaults, no provider construction |
| GET | /api/import-sources | {sources:[{source_id}]} |
| POST | /api/imports | workspace_id,source_id, allowlisted import options -> job |
| POST | /api/workspaces/{id}/jobs | operation + permitted job options -> job |
| GET | /api/jobs | {jobs,cursor} |
| GET | /api/jobs/{job_id} | public job |
| POST | /api/jobs/{job_id}/stop | {} -> job |
| GET | /api/events | named snapshot and progress SSE |
| POST | /api/workspaces/{id}/review/prepare | {} -> draft |
| GET | /api/workspaces/{id}/review | draft with _revision |
| GET | /api/workspaces/{id}/review/terms/{term_id}/evidence | term evidence |
| PATCH | /api/workspaces/{id}/review/terms/{term_id} | revision + select/custom/reviewed/user_notes |
| POST | /api/workspaces/{id}/review/bulk-review | revision,term_ids -> changed terms/summary/revision |
| POST | /api/workspaces/{id}/review/confirmation | revision,confirmed -> summary/revision |
| POST | /api/workspaces/{id}/approve | revision -> approved_terms,stale_chunks,pipeline |
| GET | /api/workspaces/{id}/reader | book_fingerprint,title,chapters |
| GET | /api/workspaces/{id}/reader/progress | available-text word offsets, not bookmark |
| GET | /api/workspaces/{id}/reader/chapters/{chapter_id} | canonical verified blocks/formatting, complete/stale/warnings |
| POST | /api/workspaces/{id}/reader/context | chapter_id,block_id,position |
| GET | /api/workspaces/{id}/reader/markers | format_version,book_fingerprint,markers,_revision |
| POST | /api/workspaces/{id}/reader/markers | revision,chapter_id,block_id,start,end,text |
| DELETE | /api/workspaces/{id}/reader/markers/{marker_id} | revision -> deleted,revision |

Payloads use `revision`, not the Python command's `expected_revision` name.
JSON permits one Content-Length, no Transfer-Encoding, at most 16 KiB; no duplicate
keys, NaN/Infinity, non-object body or unknown mutation fields. Use identical guards
for PATCH/DELETE/POST. Do not split a supposedly atomic bulk mutation to circumvent
a size limit without a specified transaction policy.

### Job payloads

```json
{"operation":"translate","chunk_limit":0}
```

`operation`: analyze | translate | publish. `profile` only analyze/translate.
`chunk_limit`: nonnegative integer, only translate; zero/omitted means all remaining.
`target_language`: only publish, defaults to pl; publish rejects profile.
No per-section overrides, pass_profiles, allow_model_change, request key or arbitrary
model options in this job schema. Do not send them until a documented API addition.

### Import payload

Required workspace_id,source_id. Optional previous_volume(workspace ID), opf(relative),
input_encoding, chapter_mode(auto/file/headings), chapter_selector, include_glob,
sidecar_txt(false by default), whole_section_limit, profile, pass_profiles(keys 1-5),
model, context_size, thinking. Current API documentation calls thinking on/off;
verify its normalization to existing CLI/provider semantics before a UI binding.
Never accept browser-supplied endpoint/host/port/executable/auth directories.

### Pipeline/approval facts

Return fields: workspace_id,stage,active_job,analysis,review,approved,
translation_complete,chapters,excluded_sections,units,publication,actions.
Raw stages/states and their limitations are in pipeline-state-model.md.
Actions are {allowed,reason}, not a preexisting reason_code/message schema.
Reasons include workspace_busy, analysis_required, analysis_complete,
review_preparation_required, review_stale, review_not_confirmed, approval_required,
translation_complete, translation_required, publication_current, publication_not_ready.

### Configuration and publication

Settings return source,default_profile,pass_profiles,profiles,resolved_passes,
whole_section_char_limit,memory_tokens. Allowlisted profile fields expose identity,
provider/model, enabled/context/reasoning/thinking/planning/output settings and
provenance. Endpoints, credentials, executable paths and local model paths are not public.
Profile definitions remain read-only. Workspace/pass assignments use revision-controlled
PATCH routes below. Publication reports safe metadata, never an output filesystem path;
the dedicated download route revalidates currency and exact EPUB bytes.

### Error envelope

```json
{"error":{"code":"review_revision_conflict","message":"Review state changed.","details":{}}}
```

| HTTP | Code |
| --- | --- |
| 400 | invalid_request |
| 403 | origin_rejected, import_disabled |
| 404 | not_found |
| 409 | workspace_busy, destination_exists, review_revision_conflict, marker_revision_conflict |
| 413 | body_too_large |
| 422 | invalid_project_state |
| 500 | internal_error |

Do not replace these with invented validation_error/forbidden_origin codes without
an explicit compatibility mapping. Messages are safe static text; no raw traceback.

## Current production additions (audited working tree)

The source hashes/symbols and verification are maintained in `api-baseline.json` and
`docs/web/contract-map.md`. `server/asgi.py` is the production adapter; `routes.dispatch`
is shared with the compatibility HTTP adapter.

- GET `/api/library`: configured flag, safe source metadata and workspace linkage;
  optional `limit` (1–40) and `after` return a bounded page with `next_cursor`.
  Immediate packed EPUB files are sources; their metadata is read only on the requested page.
- POST `/api/workspaces`: source_id, optional request_key; currently resolves one
  legacy draft link per source. This is retained for compatibility, not used by
  the refined D05 setup flow.
- GET `/api/library/sources/{source_id}/preflight` and `/inspect`: selected-source
  read-only metadata, local language sample and fingerprint; Inspect adds bounded
  structural details and short excerpts from up to five distributed reading-order
  files. Its document count is not a chapter count, its sampled-word count is not a
  book total, and its detector score is not calibrated language certainty. Neither
  route starts P1 or writes project state.
- POST `/api/library/compatibility`: checks the selected language pair and P1–P5
  profile capabilities. Unknown capability metadata warns; the current application
  language pair remains English source and Polish target.
- POST `/api/workspaces/setup`: source ID/fingerprint, immutable language pair,
  optional label, five pass profiles, and request key. Same request/key resolves
  the same configured draft; a distinct Save may create another workspace from
  the same Library source. See `docs/server-api.md` for the full payload.
- POST `/api/workspaces/{id}/prepare`: optional request_key/profile/pass_profiles; supervised real import.
- PATCH `/api/workspaces/{id}/sections/{section}`: revision plus processing/content_type/profiles;
  allow_model_change:true only after explicit user confirmation.
- PATCH `/api/workspaces/{id}/settings`: revision, pass_profiles, allow_model_change.
- GET `/api/workspaces/{id}/sections/{section}/{page}`: bounded text-only preview.
- POST `/api/workspaces/{id}/archive` or `/restore`: lifecycle revision.
- POST `/api/workspaces/{id}/review/confirm-and-approve`: exact Review revision, atomic confirmation/approval.
- GET `/api/workspaces/{id}/preparation`: actual frozen-manifest and source-package checks.
- GET `/api/workspaces/{id}/activity`: bounded sanitized durable history.
- GET `/api/workspaces/{id}/publication/download`: verified current EPUB bytes only.
- GET `/api/requests/{key}`: accepted job receipt in this server scope.
- Job/import creation supports persisted request_key matching; mismatched reuse is 409.

Pipeline adds weighted progress/basis/counts, section aggregates, membership lock,
configuration revision, metadata, actual artifact availability, busy/cleanup and
publishing projections, latest job, and historical model provenance. Profiles add a
persistent palette index. Runtime projections remain separate from checkpoint truth.

New 409 codes: analysis_membership_locked, config_revision_conflict,
lifecycle_revision_conflict, model_change_confirmation_required, workspace_archived,
request_key_conflict. Existing codes and mutation revisions are retained.

P2–P4 retained receipts remain explicitly unverified; they are never relabeled current
complete. Profile-definition editing and model diagnostics have no public production
capability and remain unavailable. No arbitrary paths, uploads, credentials, source
HTML rendering or generic artifact browser were added. Non-event query parameters
remain rejected.

`check-api-contract.py` guards the inspected source files by Git blob SHA. It does
not verify every route/payload, running service, application symbol, or current CI.
Update its baseline only after re-auditing; do not auto-refresh hashes to get green.
