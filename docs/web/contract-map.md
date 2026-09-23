# Production web implementation and contract map

Audited 2026-09-23 against the working implementation built from
the repository HEAD recorded in `api-baseline.json`. The skill's original recorded backend
commit is historical. Exact audited source blob identities are recorded in
`.agents/skills/intelitex-web/references/api-baseline.json`; the delivery commit
contains those files. Hash matching is a drift guard, not runtime verification.

The original v33 HTML and supplied implementation contract remain byte-identical.
The task owner's explicit 2026-09-22 decisions resolve D02, D03, D04, D05 and D08.
D05 was later refined in [issue #1](https://github.com/hipotures/intelitex/issues/1):
the explicit Library details/setup flow now creates configured drafts on Save,
and a source may produce multiple workspaces. The historical one-draft-per-source
endpoint remains for compatibility but is no longer used by the Library card.
See the skill's decision register, [differences](differences.md), and
[executed verification](verification.md).

## Ownership and delivery

`uv run intelitex serve --workspace-root …` creates one `ASGIServer`, one supervisor,
one runtime registry and detached workers. FastAPI/Starlette/Uvicorn serve `web/dist`,
explicit API routes, vetted EPUB downloads and SSE on the same origin. The compatibility
HTTP adapter shares `server/routes.py::dispatch`; it remains covered by the same API tests.
The CLI owns SIGINT/SIGTERM shutdown: it closes SSE and new-job admission, drains
HTTP, then invokes the supervisor's bounded worker cancellation before closing the registry.
No Node production server, reload worker, upload endpoint or CLI-output parser is used.

Work home requests `GET /api/workspaces?archived=false`, so previously archived
projects are not opened or validated on every list refresh. Its Archive drawer
requests `?archived=true` only when opened. Prepared rows poll
`GET /api/workspaces/{id}/summary`: Python derives stage, weighted progress and action
gates from validated book/checkpoint state but omits physical-attempt history, section
details and full EPUB assembly for unpublished translations. A completed unpublished
card offers Open Publish; Publish detail computes readiness before exposing Publish.
Work requests `/api/library?links=false` to skip imported-project linkage scans;
its Library chips use the active workspace list. Opening a workspace still reads
the full `/pipeline` snapshot. The existing unfiltered list route remains
for other screens and compatibility. The two HTTP adapters share identical strict
filter parsing; successful mutations invalidate both filtered lists.

`state.sqlite3`, validated checkpoint receipts and atomic project JSON remain durable
pipeline truth. Runtime jobs/events are execution history, not completion truth. Web
metadata lives in `.intelitex-web.json` at the workspace root,
`workspace.json` and `settings.json` for configured drafts, and `web.config.json`
and `web.lifecycle.json` in prepared projects. No second application database was
introduced in this flow. Source linkage uses source ID and a selected-source
fingerprint, not title, and a process/file lock. Draft archive membership also
lives in the catalog with an optimistic revision; archiving preserves a configured
draft directory. Opening a source does not create a project directory. Save creates
the durable unprepared draft; Prepare submits the existing import
command to a supervised worker; only real import produces `book.json` and sections.
The web Prepare worker does not construct a provider or send source text to one.
It locally estimates source tokens as one per four Unicode characters rounded up,
records estimated provenance, and freezes the source plan. P1 later counts with
the selected provider/tokenizer to validate real context fitting. Direct CLI
import retains the provider-aware path. If an early import step fails, the
application lock leaves an empty `.lock` in the configured draft; the
draft validator accepts that one safe file on retry and still rejects unrelated
files, symlinks and partial project state. `workspace_setup.py::validate_draft_destination`
is the audited boundary (blob `701b025ac4a151d8db380d8f993b8d6211482562`),
with retry/unsafe-lock regression in `tests/test_web_production.py`.
Auto import now recognizes explicit numbered/Prologue/Epilogue `h3`–`h6` headings
even when the EPUB TOC supplies only a start link. It does not treat every `h4`
as a chapter. The same-workspace reprepare route is available only before any
persisted work. It stages a new plan, versions the previous plan and section
configuration, then installs new source sections under the project lock. Old
section-specific F/T/E choices are reset because their IDs can acquire different
meaning. The backend preserves the old plan on ordinary staging/commit failure.

`application/web.py::WebWorkspaceService` handles metadata, bounded source preview,
revision-controlled settings, section aggregates and archive lifecycle. `processing.py`
overlays eligibility on the frozen import plan, retains dormant evidence and validates
re-inclusion against checked results, input/schema/prompt receipts and continuity.
P1 membership freezes after persisted attempt/checkpoint evidence. Idle T↔E remains
available; F transitions then return `analysis_membership_locked`.
The F/T/E write checks P1 receipts and P1 attempt manifests directly, avoiding
the full historical model-usage report on each section change.

`WorkflowQueries.pipeline` calculates half-up weighted workflow progress from real P1/P5
counts and returns the numerator, denominator and its meaning for each phase. Unknown
denominators produce null. `ReviewService` enforces approval freshness
against the approved review digest; historical approval alone cannot authorize changed
decisions. Confirm-and-approve checks one exact revision under the application lock.
It never starts translation. `PublishingService` retains existing automatic publication
and explicit retry semantics; the download verifies currentness and exact response bytes.
The HTTP pipeline projection reuses one validated book and one usage report for
workflow, section aggregates, metadata and model provenance. The publisher receives
the already projected eligible book. This preserves one authoritative read snapshot
while avoiding repeated large `book.json` and attempt scans.

## Frontend structure and real bindings

React/strict TypeScript/Vite, Tailwind 4, source-owned button/dialog primitives,
TanStack Router/Query and Zod schemas live under `web/src`. Native dialogs supply
focus trapping, Escape, scrim close and focus return. CSS retains v33 tokens/layout.

| Screen/component | HTTP/application binding | Behavior |
| --- | --- | --- |
| Work, active list, Library | GET workspaces/library/pipeline; POST workspaces | Real source metadata, independent workspace rows, idempotent persisted drafts; visible Library loads bounded pages on scroll, while explicit Refresh restarts discovery and retains its last successful result on error |
| Workspace | GET pipeline/settings/activity; jobs/stop | Five-phase rail, seven-column sections table, counts, actual provenance, supervised Run/Stop |
| Prepare | GET workspaces/draft profiles; PATCH draft settings; POST workspace prepare/reprepare; GET preparation after import | Real provider-free web import with marked 4-character token estimates; saved pass profiles can change before Prepare; an untouched prepared plan can be rebuilt in place with its old version retained; failed draft import remains visible, empty-lock retry works |
| Analyse | GET pipeline/usage | Whole-book P1 units, live recorded usage, per-unit and running total cost estimates, and artifact availability; historical P1 attempts without a saved rate use current catalog rates at read time |
| Review | GET review/evidence; PATCH term; POST confirm-and-approve | Intersecting filters, candidates/custom/source, reviewed state, committed approval |
| Translate | GET pipeline/usage/activity | Existing P2→P3→P4→P5 chunk execution, actual attempt diagnostics and section aggregates |
| Publish | GET pipeline/activity; POST publish job; GET publication/download | Real validation/size, disabled automatic Publishing, explicit retry without retranslating |
| Section drawer | GET sections/id/page; PATCH sections/id | Escaped bounded source, F/T/E/content type; selecting a profile override saves immediately |
| Pipeline models | GET profiles; PATCH settings | Five configured pass assignments and inheritance; selecting a model saves immediately |
| Archive | POST archive/restore with lifecycle revision | Metadata-only archive for drafts and prepared projects; busy/cleanup rejection; Reader access retained |
| Reader | GET reader/chapters/markers; POST context/markers; DELETE marker | Verified P5, stale/unavailable explanation, canonical Unicode offsets and marker revisions |
| Settings | GET capabilities/profiles | Real read-only scope/identity; theme and UI preference reset; unavailable diagnostics explained |

Profile definitions/credentials are not edited in the browser. Persistent palette indices
identify actual recorded profile provenance; future assignments do not recolor history.
Mixed provenance stays explicit. P2–P4 legacy retained receipts are not misrepresented as
validated current completions. Runtime `running` is a separate cell decoration.

## State synchronization

Query keys include server scope and full workspace/resource path. The singleton
`realtime/coordinator.tsx` owns one EventSource per tab, independent of routes and workers.
Initial HTTP reads coexist with snapshot-first SSE. Snapshot has no event ID; replay
uses native Last-Event-ID on the same stream. Per-job sequence protects current state;
global event IDs deduplicate activity. Old replay may fill history without regressing jobs.
A lower stream-snapshot cursor clears obsolete history; delayed HTTP snapshots cannot
regress newer stream state. Gaps and terminal events immediately reconcile domain queries.

Transient rendering is coalesced at 100 ms, durable reconciliation at 900 ms. Activity
retains at most 120 entries/workspace. Visible healthy summaries refresh every 15 s;
Review refreshes every 5 s; unhealthy connections poll every 5 s. Hidden tabs pause
polling; focus/online reconcile. Offline/reconnecting mutations are disabled. Unmounting
or disconnecting closes only browser resources, never workers.

Mutations serialize by server/workspace. F/T/E applies the acknowledged section
response immediately and reconciles in the background; other mutations remain
pending through reconciliation.
No automatic mutation retry occurs. A session-scoped request key persists unknown start
outcomes and resolves through GET requests/key; backend receipts reject mismatched reuse.
Revision conflicts preserve buffered input and require explicit reapplication. Query refresh
does not replace focused drafts, filters, drawer selection or current Reader prose.

Review & next saves and reviews the current term, then wraps to the next unreviewed
term in the current category/search scope, skipping reviewed terms. The visible
Reviewed/Unreviewed toggle does not restrict that next-term search. Review remaining
in this view sends the current filtered unreviewed IDs to the revision-checked bulk
endpoint after confirmation. Previous/Next do not mark reviewed. Dirty navigation is
guarded. Reader chapters are excluded from automatic
text replacement; metadata/markers refresh independently. Location is scoped by server,
workspace, book fingerprint, chapter, block ID and Unicode code-point offset. LocalStorage
contains UI preferences/location only; sessionStorage contains pending request keys only.
Entering Reader without an explicit book reuses the last valid Reader route or checks
backend Reader progress for the first workspace with verified translated text.

The Work Library loads its first bounded page only when the section enters the
viewport. The request asks for at least 12 books, rounded up to complete rows at
the current grid width (14 books for seven columns). An intersection sentinel
requests the next page when scrolling reaches it. Each page reads metadata only for
its source slice, including packed EPUB files. The dedicated Refresh control
reloads page one; commands, SSE, focus/reconnect and route remounts do not rescan sources. The
successful pages remain in the tab's query cache. Refresh uses the shared floating
toast and button spinner without moving cards. TanStack Query retains the last
successful list when a request fails.
Library cards now open read-only source details. Inspect and setup preflight query
the selected source only. Inspect reads up to five distributed reading-order files,
shows bounded real text excerpts, and labels its word count and language detector
as sample-only; it does not infer chapter count from EPUB filenames. Save creates
a configured, persisted draft through the
application catalog, then navigates to its unprepared workspace. A source remains
in Library and may have multiple workspaces. Cancel creates no draft. Prepare is
the next explicit mutation and stops before P1. New source setup and existing
workspace/profile queries are reconciled without rescanning Library pages.
The setup form keeps profiles with undeclared language capabilities selectable and
explains that these are profile-metadata warnings, not a Save gate. While the
shared SSE connection is not live, Save names the waiting state and shows the
recovery reason instead of appearing to do nothing; an `en` → `pl` selection with
unknown profile capabilities can be saved once synchronization completes.
Setup and job commands generate 128-bit random request keys with
`crypto.getRandomValues`, which is available on plain-HTTP LAN origins where
`crypto.randomUUID` is absent. Key generation failures are surfaced in the UI
before any mutation is sent.
An unprepared draft presents one Prepare command in the dedicated card; the
workspace header starts showing its primary pipeline action after Prepare completes.
The draft rail shows Ready, Preparing or Failed from the workspace import job;
the card shows the saved P1 profile for later Analyse and states that Prepare uses
only a local token estimate. The Pipeline models panel is open by default on a
draft; changing P1–P5 uses a settings revision, backend profile/language checks,
and a confirmation dialog without a provider request.
The action first reads current workspace state before POST, while the backend keeps
the final concurrency and destination checks. A prepared workspace with its pipeline
query still loading shows a disabled loading action, not another Prepare command.
The prepared Prepare detail screen offers `Rebuild` behind a confirmation
that says section choices reset and the old plan is versioned. It is disabled after
persisted P1 work and while a job owns the workspace; the backend rechecks all
conditions. When disabled, selectable text immediately beside the Rebuild control
shows the snapshot reason, including an unlinked Library source, archived workspace,
busy operation or unavailable connection; it is not confined to a native tooltip.
The Prepare detail layout narrows Source structure and shows an on-demand
source preview beside it; the existing read-only section-preview API supplies the
text, of which the UI displays at most the first 1 KiB of UTF-8. Metadata and checks
remain below the preview. F/T/E choices are edited in this table after reading the
source; workspace overview shows only the saved F/T/E letter, with the full meaning
in its accessible label and tooltip. The source preview has a stable height and
scrolls internally, so choosing a short or long section does not move metadata or
checks below it. A selected section and its scroll position remain stable during
background reads and section mutations. A legacy
workspace with its original source directly inside the configured Library can also
rebuild; the application verifies the imported file fingerprint before switching
plans. An external or unlinked source remains ineligible.
Analyse is labeled P1 whole-book analysis; it is explicitly
Ready until an `analyze` job is active, avoiding an apparent running state. Before
the first running P1 job, saved plan or attempt, its phase tile is noninteractive
and says to start P1 with the workspace `Run` action; while P1 runs or after it has
saved details, the tile opens the diagnostic page.
Debug mode shows the current route/API workspace ID as selectable text in the
workspace header; the stable `WSP` panel identifier remains a separate UI code.
For F/T/E, the chosen mode appears immediately as pending local intent; no durable
pipeline counts or readiness are inferred from it. A failed mutation clears the
intent and shows the server error. The successful PATCH revision updates the
selected section in the query
cache immediately; old in-flight pipeline reads are cancelled and full reconciliation
runs in the background for that workspace only. The global workspace list is marked
stale, but not refetched until it is next used, so this avoids scanning every book
after each click. The backend remains authoritative for final counts and readiness.
Resource invalidation checks the full workspace ID boundary, so a similarly
prefixed workspace is never swept into this refresh.
The Analyse detail page has a dedicated no-plan state instead of empty usage cards.
GET `/api/workspaces/{id}/analysis-reset` supplies an independent revision and
eligibility; POST with that revision moves current P1 files and a SQLite backup into
`history/p1_resets/<version>` and clears active P1 database rows under the project
lock. The command rejects active/cleanup ownership and dependent P2–P5, approved or
series work. The browser confirms before sending and returns to the workspace after
success; an unknown outcome is never blindly resubmitted. A prior Analyse job remains
in runtime history but is not projected as the current last job after reset.
Reload shows a visible connection shell followed by a phase loading card while
backend queries complete; neither invents pipeline values. Missing author/language
metadata is labeled `Not recorded`, with the saved source language used when present.
The original v33 does not depict a persisted pre-import draft or provider-dependent
Prepare; these are intentional production differences from that mock.

## Acceptance traceability

`web/tests/bootstrap.test.mjs` is the real production/offline journey; `recovery.test.mjs`
uses test-only large-data/event fixtures; `development.test.mjs` exercises the real proxy.
The matrix records implementation and relevant evidence, not a claim that every row has
an independent browser test. Runtime/API/application assertions remain in Python.

| ID | Implementation | Evidence |
| --- | --- | --- |
| V33-01 | Home list/library, initials, responsive CSS | Work pixel comparison; component initials tests; browser journey |
| V33-02 | Explicit source details, one-time setup Save, durable multi-workspace drafts | `tests/test_web_production.py` Save/Prepare/concurrent-key assertions; `web/tests/library-setup.test.mjs` offline browser journey |
| V33-03 | Prepare supervised import | Browser real Prepare; import disconnect/API tests |
| V33-04 | Workflow/section aggregate counts, P1 gate | API workflow and production policy tests |
| V33-05 | Typed rail/phase routes | Browser phase navigation/deep reload |
| V33-06 | Versioned processing overlay | D02 lifecycle/revision/retained-evidence tests |
| V33-07 | Independent content_type, frozen membership | Application configuration checks; D02 tests |
| V33-08 | Bounded escaped preview drawer | Preview API tests; browser drawer capture |
| V33-09 | ProviderPool overrides, persistent palette | Palette/projection tests; pipeline regressions |
| V33-10 | Explicit model permission/digest | Configuration validation and profile regression tests |
| V33-11 | Detached per-workspace workers | Runtime concurrency tests; browser navigation/reload during work |
| V33-12 | Stop/cleanup ownership gate | Runtime cancellation/descendant cleanup tests |
| V33-13 | Request receipts and mutation latch | Duplicate/conflicting receipt tests; StrictMode browser journey |
| V33-14 | Snapshot and replay reducer | Frontend stream unit tests; runtime SSE tests |
| V33-15 | Global IDs, per-job sequence, gap/reset reconcile | Frontend stream tests; runtime history tests |
| V33-16 | Poll/offline/focus recovery | Large-data recovery browser test; real proxy SSE |
| V33-17 | Scoped query keys, workspace remount boundary | Stream isolation test; separate workspace browser journey |
| V33-18 | URL/scoped filters/drawer/accordion preferences | Browser conflict focus preservation; route integration |
| V33-19 | Full glossary counts, intersecting filters | Legacy filter JS/browser regressions; large Review test |
| V33-20 | Revision-controlled candidate/custom/source mutations | Review API tests; competing-edit browser journey |
| V33-21 | Pre-save filtered successor | Review journey; existing Review filter regressions |
| V33-22 | Save-before-navigation and dirty blocker | Review integration; legacy draft-flush browser smoke |
| V33-23 | Atomic confirmation + committed approval | D08 application test; real approval journey |
| V33-24 | Explicit conflict/reapply, retained focused input | Browser competing writer scenario |
| V33-25 | Approved digest freshness and retained outputs | D08 test; browser approval invalidation/reapproval |
| V33-26 | Completion from checked P5, not terminal jobs | Limited-translation/API/runtime regressions |
| V33-27 | Automatic publication projection/retry/download | Publication failure/retry regressions; browser EPUB publication |
| V33-28 | Persisted normalized usage with null coverage | Existing usage/attempt regressions; phase diagnostics |
| V33-29 | Revision lifecycle + cleanup gate | Archive API tests and browser archive/restore |
| V33-30 | Verified Reader and archive availability | Reader Python regressions; Reader/marker browser journey |
| V33-31 | Stable chapter cache + canonical location | Range tests; chapter replacement excluded in coordinator |
| V33-32 | Read-only real profiles and UI-only reset | Browser Settings; profile redaction/API tests |
| V33-33 | Scoped themes/native dialog accessibility | Two-theme visual suite; overlay/component/browser tests |
| V33-34 | Same-origin, confinement, serializers, escaped text | Both-adapter route/body/security matrix; historical SSE tests |
| V33-35 | Explicit ASGI SPA/asset/API routes | ASGI static/path tests; browser deep reload |
| V33-36 | Bounded/coalesced stream and activity | 1,000 sections/2,000 terms/100 events per second browser test |
| V33-37 | Existing registry restart/recovery | Runtime abandoned/reconciliation regression tests |
| V33-38 | Preserved CLI, legacy Reader/Review and runtime | Full Python/legacy JS and legacy Review browser suites |

No product-semantic conflict remains for the five approved decisions. Boundaries and
verification limitations are recorded separately rather than silently inventing features.
