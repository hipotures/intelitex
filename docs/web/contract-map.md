# Production web implementation and contract map

Audited 2026-09-22 against the working implementation built from
`b0c40dfaf90add7340da2b992d4e18f7b473383e`. The skill's original recorded backend
commit is historical. Exact audited source blob identities are recorded in
`.agents/skills/intelitex-web/references/api-baseline.json`; the delivery commit
contains those files. Hash matching is a drift guard, not runtime verification.

The original v33 HTML and supplied implementation contract remain byte-identical.
The task owner's explicit 2026-09-22 decisions resolve D02, D03, D04, D05 and D08.
See the skill's decision register, [differences](differences.md), and
[executed verification](verification.md).

## Ownership and delivery

`translate.py serve --workspace-root …` creates one `ASGIServer`, one supervisor,
one runtime registry and detached workers. FastAPI/Starlette/Uvicorn serve `web/dist`,
explicit API routes, vetted EPUB downloads and SSE on the same origin. The compatibility
HTTP adapter shares `server/routes.py::dispatch`; it remains covered by the same API tests.
No Node production server, reload worker, upload endpoint or CLI-output parser is used.

`state.sqlite3`, validated checkpoint receipts and atomic project JSON remain durable
pipeline truth. Runtime jobs/events are execution history, not completion truth. Web
metadata lives in `.intelitex-web.json` at the workspace root, `web.config.json` and
`web.lifecycle.json` in prepared projects. No second application database was introduced.
Source linkage uses canonical source identity, not title, and a process/file lock.
Draft archive membership also lives in this catalog with an optimistic revision;
archiving a draft creates no project directory. Opening a source does not create a
project directory. Prepare submits the existing import
command to a supervised worker; only real import produces `book.json` and sections.

`application/web.py::WebWorkspaceService` handles metadata, bounded source preview,
revision-controlled settings, section aggregates and archive lifecycle. `processing.py`
overlays eligibility on the frozen import plan, retains dormant evidence and validates
re-inclusion against checked results, input/schema/prompt receipts and continuity.
P1 membership freezes after persisted attempt/checkpoint evidence. Idle T↔E remains
available; F transitions then return `analysis_membership_locked`.

`WorkflowQueries.pipeline` calculates half-up weighted workflow progress from real P1/P5
counts and returns the numerator, denominator and its meaning for each phase. Unknown
denominators produce null. `ReviewService` enforces approval freshness
against the approved review digest; historical approval alone cannot authorize changed
decisions. Confirm-and-approve checks one exact revision under the application lock.
It never starts translation. `PublishingService` retains existing automatic publication
and explicit retry semantics; the download verifies currentness and exact response bytes.

## Frontend structure and real bindings

React/strict TypeScript/Vite, Tailwind 4, source-owned button/dialog primitives,
TanStack Router/Query and Zod schemas live under `web/src`. Native dialogs supply
focus trapping, Escape, scrim close and focus return. CSS retains v33 tokens/layout.

| Screen/component | HTTP/application binding | Behavior |
| --- | --- | --- |
| Work, active list, Library | GET workspaces/library/pipeline; POST workspaces | Real source metadata, independent workspace rows, idempotent persisted drafts |
| Workspace | GET pipeline/settings/activity; jobs/stop | Five-phase rail, seven-column sections table, counts, actual provenance, supervised Run/Stop |
| Prepare | POST workspace prepare; GET preparation | Real import; frozen manifest and source-package checks; no simulated sections |
| Analyse | GET pipeline/usage | Whole-book P1 units, recorded usage and artifact availability |
| Review | GET review/evidence; PATCH term; POST confirm-and-approve | Intersecting filters, candidates/custom/source, reviewed state, committed approval |
| Translate | GET pipeline/usage/activity | Existing P2→P3→P4→P5 chunk execution, actual attempt diagnostics and section aggregates |
| Publish | GET pipeline/activity; POST publish job; GET publication/download | Real validation/size, disabled automatic Publishing, explicit retry without retranslating |
| Section drawer | GET sections/id/page; PATCH sections/id | Escaped bounded source, F/T/E/content type, confirmed profile overrides |
| Pipeline models | GET settings; PATCH settings | Five configured pass assignments, inheritance and explicit model-change confirmation |
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

Mutations serialize by server/workspace and remain pending through reconciliation.
No automatic mutation retry occurs. A session-scoped request key persists unknown start
outcomes and resolves through GET requests/key; backend receipts reject mismatched reuse.
Revision conflicts preserve buffered input and require explicit reapplication. Query refresh
does not replace focused drafts, filters, drawer selection or current Reader prose.

Review & next captures the filtered order before saving. Previous/Next do not mark
reviewed. Dirty navigation is guarded. Reader chapters are excluded from automatic
text replacement; metadata/markers refresh independently. Location is scoped by server,
workspace, book fingerprint, chapter, block ID and Unicode code-point offset. LocalStorage
contains UI preferences/location only; sessionStorage contains pending request keys only.
Entering Reader without an explicit book reuses the last valid Reader route or checks
backend Reader progress for the first workspace with verified translated text.

## Acceptance traceability

`web/tests/bootstrap.test.mjs` is the real production/offline journey; `recovery.test.mjs`
uses test-only large-data/event fixtures; `development.test.mjs` exercises the real proxy.
The matrix records implementation and relevant evidence, not a claim that every row has
an independent browser test. Runtime/API/application assertions remain in Python.

| ID | Implementation | Evidence |
| --- | --- | --- |
| V33-01 | Home list/library, initials, responsive CSS | Work pixel comparison; component initials tests; browser journey |
| V33-02 | WebCatalog draft/source lock | D05 two-tab and existing-source tests |
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
