# Intelitex web application — implementation contract from the original v33 mockup

**Task:** implement the real, backend-connected Intelitex web application, not another demonstrator.

**Repository:** `hipotures/intelitex`. Work on `main`, preserving unrelated changes. Commit the implementation and its tests. Do not push unless separately instructed.

**Interface language:** English. Source book text, translations, terminology candidates and user-entered content retain their original languages. Code, comments and new documentation must be English.

## 0. Authority, provenance and scope

Read this entire document and the reference HTML before changing code.

The authoritative visual/interaction source is the **original user-supplied v33 file**, not a reconstruction:

```text
reference/intelitex_workspace_mockup_v33.html
Original upload name: intelitex_workspace_mockup_v33(1).html
Size: 143061 bytes
SHA-256: c50fe95eb761ae332171759cfb2ba95fcc996baaa98d9a1471a570f6f54a26a5
HTML title: Intelitex Workspace Mockup v33
```

Copy this original, without changing its bytes, to `docs/mockups/intelitex_workspace_mockup_v33.html` when installing this specification in the repository. Update the mockups README to designate v33 as the active reference. Do not regenerate an approximation from screenshots. Keep v32 only as historical material and explicitly mark it superseded.

The repository snapshot inspected while drafting this contract was `1f85da8c0bd38ebbe01c3f867ef70f1793a5b957`. At that snapshot, the repository's mockup README designated a **reconstructed v32**, while the main runtime exposed jobs, workspace queries, usage and SSE. This does **not** establish that the later environment-bootstrap or complete-backend tasks have landed. Inspect the actual current HEAD and local worktree. Never reset to the inspected SHA or overwrite newer work.

### 0.1 How to read the requirements

- **R — Reference requirement:** directly describes v33's rendered UI, labels, layout or an implemented interaction in its JavaScript.
- **P — Production decision:** an explicit implementation rule supplied by this contract where v33 uses fake data, omits networking, has an inconsistency or deliberately leaves a feature unfinished. It is not claimed to be implemented or fully specified by the mockup.
- **B — Backend invariant:** preserve existing authoritative Python/application behavior, locks, checkpoints, review validation and process supervision.

Use this precedence: security and checkpoint integrity; the explicit P/B rules in this contract; original v33 visual structure and interactions; existing component conventions. A mockup simulation must never override a real checkpoint or weaken a backend safety check.

Do not improvise an alternative dashboard, silently omit a control, create pretend successful requests, or describe an unimplemented mock feature as finished. The only deliberately deferred controls are listed in section 18. All other listed functionality is required, including any narrowly necessary backend extensions.

### 0.2 Required result

A user must be able to browse sources and workspaces, prepare a supported source, configure eligible sections and profiles, run global analysis, review and approve terminology, run P2–P5, monitor multiple independent jobs, stop one job without affecting others, inspect phase diagnostics, inspect publication, read verified output, and archive/restore workspace membership. Browser navigation must never own execution.

This task is now the **production UI implementation**, unlike earlier bootstrap-only tasks. It does not authorize redesigning the translation algorithms or running paid/live model experiments during development.

## 1. Preflight and implementation boundaries

### 1.1 Inspect before implementation

Read repository instructions and relevant architecture documentation. Inspect Python packaging, existing `web/` tooling, the main server, runtime, application services, review/reader adapters, publishing, import planning, profile resolution, usage normalizers and all existing tests. Record the actual starting HEAD.

Create `docs/web/implementation-map.md` with one row per required capability: UI requirement ID, actual application method, actual HTTP method/path, request/response serializer, test and status. Record real contracts, not proposed endpoints masquerading as existing ones.

Verify these baseline behaviors with existing tests before changing their adapters: multiple workspaces; one mutating job per project; asynchronous stop and descendant cleanup; replay sanitization; release of completed workers; read-only snapshots during writes; revision conflicts; checkpoint resume; automatic publication. Do not claim those tests passed unless you ran them.

### 1.2 Technology and deployment

Use the agreed frontend stack: React, strict TypeScript, Vite, Tailwind CSS 4, source-owned shadcn/ui primitives, TanStack Router and TanStack Query. Reuse a working bootstrap if present. Do not introduce Next.js, a second frontend framework, Redux, another component library or a separate application database in the browser.

The intended Python delivery adapter is FastAPI/Starlette. If it has already been integrated, reuse it. If `serve` still uses `ThreadingHTTPServer`, migrate only the HTTP/static/SSE adapter behind the same entrypoint in a separate, testable integration step; retain existing application services, supervisor and worker architecture. Do not run a new FastAPI server beside the old main server. HTTP-adapter migration must pass route/security/runtime compatibility tests before frontend integration.

Production entrypoint remains one command:

```bash
uv run translate.py serve --workspace-root /configured/workspaces
```

Keep the existing optional source-root configuration when present; add a confined import root only through the backend capability described below. Documentation must use illustrative paths, not machine-specific credentials or actual user directories.

One production server process owns one supervisor and serves the frontend build and `/api`. Workers remain separate processes. Use a single ASGI worker and no production reload process that could duplicate ownership. Serve deep frontend links through a restricted SPA fallback; unknown `/api/*` paths must return API errors, never HTML. Missing assets must be 404, not the SPA shell. Serve only the built frontend asset directory and explicit vetted resources, never the repository or workspace root.

Development may run Vite separately. Proxy `/api` to the single backend. Do not disable Host/Origin checks or add wildcard CORS to make the proxy work. Add an integration test for the chosen same-origin development arrangement; any development-origin exception must be exact, explicit, development-only and disabled by default. Preserve unbuffered SSE through the proxy.

Use `uv`, never direct `pip`. Preserve the established Node package manager; if none exists, use npm and commit its lockfile. Resolve versions from the actual project and current official documentation; do not assume mock model names are installed dependencies or real provider identifiers.

### 1.3 Frontend organization

Keep components separate from protocol and workflow decisions. Use these responsibilities, adapting filenames to existing conventions:

```text
web/src/app/            router, providers, app shell, connection lifecycle
web/src/api/            typed DTOs, runtime validation, HTTP errors, query keys
web/src/realtime/       one SSE connection, replay reducer, invalidation coordinator
web/src/features/work/ library, workspace list and archive
web/src/features/pipeline/ workspace, sections, phase details and profile assignments
web/src/features/review/ filters, term list, candidate editor and approval
web/src/features/reader/ basic reading mode
web/src/features/settings/ settings modal
web/src/components/ui/ shared primitives styled to v33
web/src/styles/        v33 tokens, application layout and specialized typography
```

Do not put pipeline execution in React, parse terminal output, mount the mock HTML in an iframe, or copy its global mutable state/timers into production. Reuse its CSS values and component geometry as appropriate; do not import its simulator.

## 2. Visual contract

**R:** reproduce v33's actual rendered composition. Do not replace it with the earlier v32 layout.

### 2.1 Global tokens

Use the reference CSS as the exact starting token definition. The following are required anchors, not a suggestion to approximate the theme:

| Token | Dark | Light |
| --- | --- | --- |
| background | `#0d0f12` | `#f5f6f8` |
| soft background | `#111419` | `#eef1f4` |
| panel | `#14181e` | `#ffffff` |
| panel 2 | `#171c23` | `#f8fafc` |
| panel 3 | `#1d232c` | `#edf1f5` |
| border | `#282f39` | `#dce2e8` |
| strong border | `#35404d` | `#c9d1da` |
| primary text | `#edf1f5` | `#17202a` |
| secondary text | `#b5bdc8` | `#4f5d6c` |
| dim text | `#7f8996` | `#7b8794` |
| accent | `#4f9dea` | `#2f7ed1` |
| success | `#32b889` | `#178b67` |
| error | `#e15d58` | `#c84a45` |
| warning | `#d5a63e` | `#a87916` |

Preserve the corresponding translucent colors, gradients, shadows, border widths and transitions from v33. Default theme is dark unless the browser has an explicit saved choice. Only Dark and Light are in scope. Apply the saved theme before the first visible paint; switching theme must not reload the page or restart requests.

Use v33's font stack (`Inter`, system sans fallbacks); it does not load an external font asset. Do not introduce an external font/CDN requirement. Match reference screenshots using the same available fonts in the reference and production renderers. Reader prose uses Georgia/serif, independently from controls.

### 2.2 Geometry

| Element | Required reference geometry |
| --- | --- |
| Topbar | sticky, 64 px tall, 28 px horizontal padding, bottom border, backdrop blur |
| Brand | 28×28 px rounded gradient square with `IX`, then `Intelitex` |
| Topbar center | compact segmented Work / Reader switch; not a sidebar |
| Main | max width 1480 px; padding 30 px 34 px 70 px |
| Workspace main | max width 1520 px |
| Review main | max width 1600 px |
| Phase detail main | max width 1180 px |
| General large/small radius | 14 px / 9 px |
| Normal h1 | 27 px; reference tracking and line height |
| Workspace header cover | 44×60 px |
| Active list cover | 48×66 px |
| Library cover | aspect ratio 2:3, initials 42 px on desktop |
| Standard icon button | 36×36 px |
| Right drawer | `min(520px, 94vw)`, full height |
| Settings modal | `min(850px, 96vw)`, max height 88vh |
| Archive confirmation | `min(500px, 94vw)` |
| Phase metrics | four equal cards; two-column detail layout 1.35fr/.65fr |

Keep workspace tables full-width with **Pipeline models** and **Live activity** accordions below. There is no permanent models/activity sidebar in v33. Keep the full-page Review as two panels, not the v32 three-column reconstruction and not a modal.

### 2.3 Responsive behavior

Reproduce the actual breakpoint rules, not generic framework defaults:

- At 1020 px and below: active-workspace rows reduce to cover/title/action, hide their secondary status/progress columns; phase rail becomes a vertical list; model grid becomes two columns; Reader sidebar becomes 220 px.
- At 900 px and below: phase-detail metrics become two columns and phase-detail body becomes one column.
- At 820 px and below: Review header stacks; term list is above detail, max height 240 px; detail is 68vh with minimum height 520 px; custom-form controls stack; footer uses one full-width button row.
- At 720 px and below: header padding 14 px, hide brand text but retain IX; main padding 20 px 14 px 55 px; workspace-row action moves below its title; library is two columns; model grid is one column; Reader book selector becomes a horizontal strip and prose padding is 30 px 20 px.
- At 560 px and below: phase-detail header stacks and key/value items stack.

Copy section-table mobile proportions and compact marks from section 8. Long names ellipsize without growing column widths. Only deliberately wide diagnostic tables scroll horizontally; the entire page must not overflow the viewport. At 200% zoom controls must remain reachable.

### 2.4 Accessibility and overlays

Implement real buttons/links, labeled controls, keyboard navigation, visible focus and screen-reader names for icon-only status marks. Visual status cannot depend on color alone. Preserve visible reference glyphs or visually equivalent icons with the same size and stroke weight.

Drawers and modals have one active scrim, focus containment, restored trigger focus, background scroll locking and Escape dismissal. Clicking the scrim closes the top overlay. There is only one open modal/drawer at a time. Keyboard dismissal must not discard an unresolved save silently; section 15 defines pending/dirty behavior.

Respect reduced-motion preferences. Keep state indication when spinner animation is disabled. Preserve the excluded-row muted look, but provide readable focus/disabled explanations and an accessible equivalent in the section drawer; do not make the mode impossible to restore.

## 3. Navigation and browser-owned state

**R:** Work and Reader are top-level modes. Work contains Home, Workspace, Review and phase details. Reader is independent. **P:** implement actual browser routes and history.

| URL | Screen |
| --- | --- |
| `/` | redirect to `/work` |
| `/work` | Books in progress / Active workspaces / Library |
| `/work/workspaces/$workspaceId` | workspace overview |
| `/work/workspaces/$workspaceId/prepare` | Prepare details |
| `/work/workspaces/$workspaceId/analyse` | Analyse details |
| `/work/workspaces/$workspaceId/review` | full-page terminology review |
| `/work/workspaces/$workspaceId/translate` | Translate details |
| `/work/workspaces/$workspaceId/publish` | Publish details |
| `/reader` | last Reader selection, otherwise first available book |
| `/reader/$workspaceId` | selected book and saved reading location |

Keep external IDs opaque; URL-encode them. Never derive an ID from title or a filesystem path. Use typed search parameters for section filter (`all/full/translate/excluded`), review search/category/status/selected term, Reader chapter and optional section drawer selection. Unrecognized values fall back to documented defaults, not arbitrary component state.

Browser Back/Forward and direct URL reload must work. Opening a workspace/review/phase pushes a history entry; changing search/filter inputs replaces the current entry. Drawer open/close is presentation state and does not add a second page history entry. Back links are exactly `← Library` from Workspace and `← Workspace` from Review/phase details.

Switching to Reader remembers the full last Work location and scroll position. Work returns there, not unconditionally to Home. Reader remembers its book/chapter/anchor. Navigating elsewhere does not send Stop, Start, Prepare or Approve.

Keep only theme, last visited route, filter preferences, accordion expansion, scroll positions and reading-location preferences in versioned browser storage, namespaced by a server-supplied non-path installation/scope identifier. Never persist authoritative job, pass, review, profile-assignment, archive or publication state in localStorage. Never read the mock's `intelitex-mockup-v22` key. Clear incompatible preference versions safely.

Show a designed not-found state for deleted/unknown workspace IDs with a link to Work. A server outage is not a not-found condition and must not redirect away from the user's work.

## 4. Domain state: exact distinctions

**B/P:** server responses own state. The browser may format or project explicit state; it must not independently decide readiness from a handful of booleans.

A **source book** is a discovered supported input. A **workspace** is a server-persisted working record linked to one source. A **section** is a source reading-order grouping. An **analysis unit** or **translation unit/chunk** is a persisted execution unit. A **pass checkpoint** is a validated result for one unit/pass. A **job** is one supervised operation; a successful limited job does not imply book completion.

Never assume one section equals one analysis unit or one translation chunk. Multiple units per section must be represented and aggregated explicitly by Python. Never confuse a global phase with a pass executed for one section.

### 4.1 Job and UI state

Preserve runtime states `starting`, `running`, `stopping`, `succeeded`, `failed`, `cancelled`, `abandoned`. Add operation-specific states only through a documented backend contract. UI labels are a presentation mapping, not alternative persisted states.

- `starting`: Starting…; disable duplicate starts.
- `running`: show the actual operation, active unit/pass if known and Stop where allowed.
- `stopping`: Stopping…; disable Run/Retry/Archive and further Stop submissions.
- `succeeded`: refetch pipeline/read models; choose the next action from server gates.
- `cancelled`: Stopped; keep completed checkpoints and show Run when the backend permits continuation.
- `failed`: Error; safe code/message and Retry only for the failed supported operation.
- `abandoned`: Interrupted; state after server restart. No automatic retry, process adoption or assumption that historical PID is dead.

A pass is Running only if tied to a live matching job/task. A stored `active` marker from the prototype, stale browser cache or old event must never animate as live work. While disconnected, stop animations and label cached execution data stale; do not assert the actual worker stopped.

### 4.2 Phase rail

Order and labels are exactly **Prepare → Analyse → Review → Translate → Publish**. Do not add a P1 prefix to Analyse or Review. Every **unblocked** tile is clickable in v33; Review opens Review, the others open their own detail pages. An available/current phase may have an accent outline without claiming an active worker. Finished phases have the green treatment; blocked phases are dimmed; failures use the error treatment.

Upstream phases remain inspectable after completion. Blocked tiles are noninteractive, have an accessible reason and do not trigger execution. A direct link to a blocked phase shows a compact blocked explanation and Workspace link; it must not run or skip prerequisites.

### 4.3 Progress calculations

**R:** v33 uses a weighted workflow percentage, not elapsed time. **P:** retain that presentation model but calculate it in the backend from validated counts:

```text
not prepared:                                    0
prepared, analysis not complete:                 round(5 + 25 * analysis_fraction)
analysis complete, current approval missing:     32
current approval, translation not complete:      round(35 + 58 * translation_fraction)
translation complete, no current publication:    98
current validated publication:                   100
```

Use positive-value half-up rounding consistently. `analysis_fraction` is completed required P1 units / required P1 units. `translation_fraction` is completed required P5 units / required translation units. Required zero-unit phases are complete only when the backend explicitly records/validates them as such; never divide by zero or have the browser self-approve an empty glossary. Before a valid prepared plan, percentage is unknown or zero as the backend states.

The server returns percentage, phase counts, denominator meaning and completion flags. The percentage can decrease after an explicitly accepted plan/approval invalidation; do not smooth it into a false monotonic value. Label its tooltip `Workflow progress — not an ETA`. Preparation's inner progress uses real reported current/total; show an indeterminate bar when unknown. No timer-based fabricated percentages anywhere.

## 5. Work/Home and source library

**R:** the title is `Books in progress`, with `Active workspaces` as a list and `Library` as a cover grid underneath. Each has a real count badge. Archive opens from the active-list header.

### 5.1 Active list

Rows contain cover, title, author/language pair, current status and subordinate detail, progress plus Updated age, and a fixed-width action button. Desktop columns follow v33: `58px minmax(260px,1.25fr) minmax(250px,1fr) minmax(230px,.8fr) 112px`. Minimum row height is 100 px.

Only nonarchived workspaces appear. Sort by backend `last_updated` descending on initial load or explicit list refresh, with workspace ID as a tie-breaker. **P:** do not reshuffle rows on every character/progress event while the user is interacting with the list; apply structural/order updates after interaction ends or the next navigation. Real progress still updates in place.

Clicking a row opens its Workspace. Clicking its action executes only that action and does not trigger row navigation. Primary action is returned by backend action availability and follows section 6. Never infer running from an old `last_event`.

Updated uses a server domain-change timestamp, not the last render, polling request or mere client navigation. Display relative time and expose the exact timestamp in a tooltip. Update relative display at most once per minute. Invalid timestamps show `—`.

### 5.2 Library cards and opening a source

Cards show a real vetted cover when supplied, otherwise v33's cover gradient and maximum-three-character title initials. Remove leading `A`, `An`, `The` while another title word remains; use the first alphanumeric character of up to three remaining words. Preserve the reference examples: `The Glass Meridian` → `GM`, `A Bright Tomorrow` → `BT`, `Orbital Ashes` → `OA`. Support Unicode letters/digits; empty titles use `?`. Do not invent a book cover from an unrelated image.

Metadata below: ellipsized title, author, word count and a workspace badge when a linked workspace exists. Unknown word count is `—`, not zero. The card exposes its full title accessibly. Keep source ordering stable; use backend catalog order, with title/ID tie-breaking if no order is provided.

Opening an existing linked workspace navigates to it. Opening a source without a workspace creates exactly one **persisted draft workspace** and opens its unprepared Workspace. It does not prepare automatically and does not call a model. Repeated clicks or two browser tabs must resolve to the same linked workspace, not duplicate directories.

**P/B:** source discovery is confined to the configured import root. Source IDs, safe display names and capabilities are returned by Python; browsers never supply arbitrary native paths. Unsupported source formats are marked unavailable with an explanation. Do not fabricate catalog entries. Source scanning must not generate text or consume model quota.

The reference's `Source directory: /...` label becomes `Source directory: <safe configured label>` without exposing an absolute path. With no import root, show `Source library is not configured` and keep existing workspaces usable. An empty configured root shows `No source books found`. A failed scan has Retry; it must not look like an empty library.

## 6. Workspace overview and operation controls

**R:** preserve cover/title/author/language/words header, one main action at the right, phase rail, conditional notice, then the preparation card or the sections/accordions/archive layout.

### 6.1 Action table

The same backend gate drives both the Home row action and Workspace action. Display the following context-appropriate labels:

| Authoritative condition | Workspace main action | Home row action | Effect |
| --- | --- | --- | --- |
| draft, not prepared | Prepare | Prepare | start one import/prepare job |
| Prepare/P1/translation starting | Starting… disabled | Starting… disabled | wait for job state |
| eligible live preparation/P1/translation | Stop | Stop | stop that job only |
| job stopping | Stopping… disabled | Stopping… disabled | await terminal state and cleanup |
| prepared, analysis incomplete, idle | Run | Run | start/resume analyze |
| P1 failed, no active/cleanup owner | Retry | Retry | retry analyze from checkpoints |
| analysis complete, approval not current | Review | Review | navigate; do not translate |
| approval current, translation incomplete | Run | Run | translate all remaining eligible units |
| translation failed, idle | Retry | Retry | resume failed translation operation |
| final publication currently executing | Publishing… disabled | Publishing… disabled | publication runs in the existing worker |
| translation complete, publication missing/failed/stale, idle | Publish or Retry publish | Publish or Retry publish | explicit publication job only |
| current publication complete, no required work | Finished disabled | Open | inspect completed workspace |
| archived | Restore before running | absent from active list | no job can start while archived |
| connection not fresh | disabled with connection explanation | disabled | no blind mutation |

The failed-publication row is a **P correction** to v33's perpetual Publishing… placeholder. Finishing P5 does not itself prove an EPUB exists. Do not require retranslation to retry publishing. Do not issue a second publish request when the translation worker is already publishing automatically.

Run after approval starts a job with all remaining units (`chunk_limit: 0` or the current equivalent), not the legacy CLI default of five. Run is a continuation; it never erases successful checkpoints. No separate Pause button and no invented pause state.

### 6.2 Stop and errors

Stop sends one cancellation request for the selected job ID, immediately disables the control as a local pending request, then follows the backend `stopping` state. It must not optimistically set passes pending, erase partial evidence, claim the worker is gone, close SSE or cancel jobs belonging to other workspaces.

Do not show `Stopped` until the backend reports cancellation; gate another mutating operation until process/descendant cleanup releases ownership, even if a terminal result arrived first. A rejected stop displays a safe inline explanation and refetches the job. A Stop request does not require an extra confirmation modal.

Error notice shows the relevant phase/pass, section/unit display label when known, safe error text and Retry when allowed. Do not show provider response bodies or private paths. P1 completion notice says translation is blocked until terminology is confirmed/approved and opens Review. The UI never bypasses this gate through Retry.

### 6.3 Prepare

Before preparation, show exactly the reference centered prepare card with P icon, explanation, Prepare button and optional progress. Do not render a fabricated source table.

Prepare inspects the supported source and creates the frozen section/chunk plan through existing application semantics. It makes no text-generation request. If existing planning needs metadata/tokenizer discovery, expose it accurately and require configured providers; it is not a model-generation smoke test. Missing prerequisites are returned as errors/capabilities, not worked around with invented token counts.

Show server-reported preparation messages and determinate progress when available. Disable both Prepare triggers while submission is unresolved or a job is active. Navigating away must not break preparation. On success, refetch the workspace/plan, then replace the card with real sections; never complete from a timeout.

## 7. Archive and restoration

**R:** Home's `Archive →` opens a right drawer. Workspace has a bottom-right `Archive workspace` action styled as in v33. Clicking it opens the 500 px confirmation modal naming the book, with Cancel and Archive workspace.

**P/B:** archival is persisted presentation/lifecycle metadata, not deletion, file movement or checkpoint rewriting. It hides the workspace from the active list and its linked source card from the normal Work library while keeping Reader availability and source/checkpoint artifacts. A source rescan must not silently unarchive it. One source has one linked workspace for this UI; multiple independently imported sources with identical titles still have distinct IDs.

Archiving is forbidden while any mutating job or owned-process cleanup is active, including automatic publication. Both backend and UI enforce it. Keep the modal visible on failure. On success close it, navigate to Work, update both counts and show `Workspace moved to archive.` No optimistic removal before acknowledgement.

Archive drawer lists archived workspace title and its real workflow progress, with Restore. Restore is idempotent, preserves previous state, does not run anything, re-adds the workspace/source to Work and closes the drawer after success. Reader includes archived workspaces. Empty archive has `Archive is empty.` No delete/permanent-remove feature is in scope.

## 8. Sections table and section drawer

### 8.1 Table content and title fallback

**R:** columns are exactly **Section | Processing | P1 | P2 | P3 | P4 | P5**. Do not remove P1 because analysis is global. Do not add Type/Model columns simply because unused helper functions exist in the mock source.

Desktop widths: Section 40%, Processing 18%, each pass 8.4%. At 720 px and below: 43%, 21%, each pass 7.2%. Desktop processing switch is 84×28 px; mobile 54×25 px. Pass marks are 18 px desktop, 14 px mobile.

Use source reading order and a two-digit ordinal, never the currently filtered position. Title precedence: trimmed explicit title; otherwise first source sentence after whitespace normalization; otherwise source excerpt; otherwise `Untitled section`. Fallback text is slightly muted/italic and ellipsized. Tooltip contains the full safe label and explains that it is source text when no explicit title exists.

Clicking a noninteractive part of a row opens the section drawer. Mode controls, tooltips and other nested controls do not open the drawer. Filters `All`, `Full`, `Translate only`, `Excluded` intersect only with the processing mode, retain source order and never mutate the project. Preserve filter, scroll and open drawer when events or saves update the table. The displayed detected/excluded totals describe all sections, not just filtered rows.

### 8.2 Processing modes

| Mode | P1 membership | P2–P5 membership | Rendering |
| --- | --- | --- | --- |
| F — Full | required | required | green selected F control |
| T — Translate only | not required | required | blue selected T control; P1 not applicable unless historical result is separately inspected |
| E — Excluded | not required | not required | selected E, whole row muted at reference opacity; retained results must not be deleted |

Only Processing controls pipeline inclusion. Content type is descriptive metadata and must never automatically change F/T/E. A section becoming Excluded is visually greyed out only after a successful acknowledged save. Excluded controls remain restorable when policy permits.

**P — safe editing policy, explicitly different from the simulator:**

1. All processing changes require no active mutating job and no incomplete owned-process cleanup, plus an expected plan/config revision.
2. Before any persisted P1 attempt exists, F/T/E can change freely without rebuilding section/chunk boundaries. Default planning classifications are kept until explicitly changed.
3. Once a P1 attempt exists, membership in the P1 set is frozen for this implementation: transitions between F and T/E return `analysis_membership_locked`. T↔E remains allowed while idle because it does not alter P1 membership. This avoids pretending that cumulative analysis can be selectively undone. An explicit reanalysis/replanning product workflow is deferred, not silently simulated.
4. T↔E recomputes required downstream work/readiness and invalidates a previously current publication as necessary. Keep prior checkpoints/evidence; excluded work becomes dormant rather than deleted. Re-including a section reuses only checkpoints the application validates against the current approved/configuration state.
5. The backend returns effective membership and cell state. An excluded section with retained historical completion may show the retained checkmark muted, with `Excluded — retained result; not scheduled` in its tooltip; it must not count toward required progress.
6. Changing mode never starts analysis/translation/publication automatically. Explain a disabled option in the drawer; do not make a visually successful frontend-only change.

This policy is a deliberate production boundary, not a claim that v33 implements invalidation correctly. If newer application code already supports a stronger transactional replan flow, do not silently invoke it from F/T/E; integrating a destructive replan still requires a separate explicit user workflow.

### 8.3 Pass marks, aggregation and history

Python returns cell state and counts for each section/pass. The client renders:

| State | Mark |
| --- | --- |
| not applicable | em dash |
| pending | hollow circle in planned profile color |
| running | profile-colored ring, halo and spinner |
| complete | filled profile-colored circle with white check |
| failed | crossed/error treatment retaining profile identity |
| partial section completion | P addition: partially filled ring, no full check, tooltip `k/n units complete` |
| stale checkpoint | P addition: amber dashed ring with refresh glyph, never a success check |
| connection stale | last known mark without a live spinner plus stale-data explanation |

Aggregation is based on required units: all valid completed → complete; matching live unit → running; blocking failed unit → failed; stale required checkpoint → stale; some valid units and remaining pending → partial; none complete → pending. A running retry supersedes its historical failed attempt; never let an old failure overwrite current successful completion. Historical attempts remain inspectable in diagnostics.

Completed-cell color/tooltip comes from the profile/model that actually produced the retained result, not a newly selected future default. Pending uses effective planned assignment. For a section aggregated from several actual profiles, use a neutral/segmented mixed-model mark with an explicit tooltip listing them; do not falsely choose one profile.

Tooltip is accessible on hover and keyboard focus. It includes pass, section/unit completion count, actual/planned profile and model, state, and available usage: Input, Cache, Reason, Output. Unknown values are `—`; never call `mockTokenStats`, estimate from characters or relabel UTF-8 bytes as tokens.

### 8.4 Drawer

Reproduce the fixed right drawer with section eyebrow/title, descriptive metadata subtitle and Close. Body order: Processing and Content type side by side; autosave note; five optional model overrides; source preview. No footer Save button is added: selectors save immediately through the common mutation queue.

Content type values and reference glyphs: Narrative ¶; Contents ☷; Glossary ≡; Footnotes †; Front matter ◁; Back matter ▷; Advertisement ◇; Unclassified ?. A new value must not be inferred from the label. A legacy unknown value is displayed as unknown with a safe option to choose a recognized metadata value.

Source preview is real extracted source content, not translation. Render plain escaped text/validated source blocks, never raw executable EPUB HTML. Preserve meaningful paragraph breaks. For large sections load bounded pages of blocks in the drawer with `Load more`; show whether preview is partial. Do not put entire books in the workspace-list response.

The original source contains an `openTypeMenu` helper but does not render a content-type button in the main table. Do not add that unused table UI. Content type editing belongs in the drawer.

## 9. Profile assignment and colors

**R:** keep the collapsed-by-default Pipeline models accordion with five Pass 1–Pass 5 slots. Each select uses actual configured profiles. The section drawer has one override row per pass with a colored swatch, label and select; its first option inherits that workspace pass's current default.

**P:** precedence is section override → workspace pass assignment → existing project/default profile resolution. Use stable profile IDs in requests; names are labels. Represent inherit as null/absence, never as a copied profile ID. Label its first option `Inherit — <workspace profile>`, an intentional clarification of v33's unlabeled inheritance option. Keep an explicit override even when it currently equals the inherited profile; show both choices unambiguously so a later workspace-default change does not lose intent.

Assignments persist on the backend. No profile choice changes the executable model silently before successful save. Serialize assignment saves per workspace config revision. Disabled assignments explain active-workspace locks or invalid profile capabilities. Do not launch provider discovery merely when a select opens.

Changes apply to not-yet-executed work; accepted successful checkpoints and their historical model colors remain unchanged. Do not rerun completed passes automatically. When the existing application requires explicit `allow_model_change`, show a confirmation using the existing modal style listing the affected future passes and stating that completed results are kept. Never set that permission without the user's explicit confirmation. Failure/cancel leaves the old assignment intact.

Use this v33 palette in order:

```text
#5AA9FF #7C5CFF #2FD6A1 #F5B942 #FF7A59 #E56BCE
#4DD0E1 #9CCC65 #FF8A80 #B39DDB #26C6DA #D4A24C
#F06292 #64B5F6 #AED581 #FFB74D #7986CB #4DB6AC
```

**P:** assign a persistent palette index to each stable profile identity on first registration, reusing an existing persisted color when available. Sorting, renaming or removing another profile must not recolor past results. After eighteen profiles the palette cycles, but tooltips/names always disambiguate. Both themes use the same identity palette. Never hard-code Astra/Sol/Qwen fixture profiles into production data.

## 10. Phase detail pages

**R:** each page uses `← Workspace`, the phase/book eyebrow, phase h1, `Phase details and execution diagnostics`, a current state badge, four metrics and the reference card/table layout. Phase navigation itself does not run an operation. Data updates while the page remains open.

### 10.1 Prepare

Metrics: Format, Sections, Processable, Words. Format is actual source format, not always EPUB. After preparation, Source structure table shows Section, Content type, Processing and P1 membership in source order. P1 membership is explicitly the analysis plan, not a count fabricated from section quantity.

Source metadata shows title, author, language and safe source-relative display identifier. Checks show actual backend validation results for package readability, reading order and frozen plan. `✓` requires completed validation, not merely `prepared=true`. Non-EPUB inputs show applicable checks and mark EPUB-specific checks not applicable.

Before preparation show the reference empty explanation and unknown metrics. Do not display fictitious frozen sections or success checkmarks. Source structure is read-only here; editing remains in Workspace/drawer.

### 10.2 Analyse

Metrics: Input, Cache, Reason, Output. Main table: Section/unit, Model, Status, In, Cache, Reason, Out. One row per actual analysis unit, grouped/labeled by section; do not compress multiple analysis units into one made-up request. Maintain the 820 px minimum table width and local horizontal scrolling.

Right-side Generated data shows real terms/entities count, analysis-unit count, workspace default P1 profile and review-gate state. Artifacts card shows actual terminology/book-memory availability. `Book memory` is an availability indicator only in this scope; do not create an editor or filesystem explorer. Remove the mock's speculative development placeholder text.

### 10.3 Translate

The same four usage metrics summarize P2–P5. Pass summary has P2–P5 rows with default profile, completed/required units and usage. Its default-profile column is a current configuration label; usage reflects actual attempts, including explicit per-section overrides. Show `Mixed actual models` in a tooltip when relevant rather than implying the default did all the work.

Section progress table uses authoritative aggregate cells for P2–P5. Recent execution reuses the workspace activity stream. Diagnostics uses real retained attempt metadata: unit/pass, attempt identity/number, outcome, actual model/provider, start/end/duration when recorded and safe failure code. Unknown fields are `—`; do not fabricate retries, latency or provider metadata. Detailed prompts/responses and raw files are not part of this public view.

### 10.4 Publish

Metrics: actual publication state, format, included section count and actual artifact size; no word-count-based size estimate. Published output shows real filename/resource ID, safe relative output display path, language, edition/publisher marker when actually recorded, total source sections and excluded count.

Show backend validation checks individually. Atomic finalized output and current matching fingerprint are required for Published. A previous stale publication may be displayed as previous/stale; never present it as current. Publish error or cancellation preserves valid translated checkpoints and enables explicit retry through the workspace gate.

Metadata shows real title, creator, source/output language and publisher marker. Publication log contains publication events only, not unrelated P2 waits. **P:** add an `Open EPUB` download link within Published output only when the server provides a vetted current artifact resource. Use a dedicated resource route; never make an arbitrary relative path a file-server capability. No model call occurs on download.

### 10.5 Usage semantics

**P/B:** retain existing Python usage normalization. Required view metrics are normalized input tokens, cached input tokens, reasoning output tokens and output tokens, each with availability/provenance. Do not add the four tiles together: cache/reasoning may already be subsets according to the provider normalizer. Do not redefine accounting in TypeScript.

Phase totals cover known actual attempts in that phase, including retries with recorded usage, and explain that scope in a tooltip. Cell result tooltips identify retained successful-result usage, separately from total attempt expenditure where available. Dedupe live cumulative counters by attempt and field; do not sum repeated streaming snapshots. Once finalized, persisted normalized usage supersedes interim values.

Return null/unavailable distinctly from a real measured zero. A recovered checkpoint without a new call must not add new consumption. Units measured only in UTF-8 bytes stay labeled `UTF-8 bytes`, not `tok`. Compact formatting is display-only; exact numbers remain in tooltips. Do not show cost unless the server has an authoritative priced metric; adding cost cards is out of scope.

## 11. Terminology Review

### 11.1 Page structure

**R:** full page, not a modal. Header: `Review · <book>`, `Terminology Review`, real `reviewed/total · remaining · uncertain` summary. Right side has Glossary confirmed badge when appropriate and Confirm glossary / Reconfirm glossary. The approved-state warning remains visible while the review is current.

Search and status chips are on the first toolbar; category chips are a separate row. Status choices are exactly All, Unreviewed, Reviewed. Categories come from actual data in backend order, with All first. Do not add an Uncertain filter just because the summary contains that count; v33 does not render it.

Desktop layout: left term list `minmax(340px,40%)`, right detail `minmax(0,1fr)`, 18 px gap. Both use the reference panel-height rule `clamp(560px, calc(100vh - 300px), 780px)`. The term list scrolls independently. Detail body scrolls, footer stays inside the bottom of its card and never scrolls away. Do not let evidence expand the page indefinitely.

Term rows show source, reviewed/pending status and category/effective translation. Selection uses the accent background. The detail card has Selected term eyebrow, source title, stable term ID, category and actual confidence/uncertainty tags; Meaning; Aliases; Translation candidates; custom form with Keep source and Use candidate; all supplied evidence in order.

**P:** do not infer confidence or gender from a name/category. If confidence is missing, omit that badge or show Unknown rather than the mock's unconditional high. If existing review metadata includes additional meaning/identity fields, preserve them in data and show their existing safe representation in the Meaning area rather than inventing values.

### 11.2 Filtering and selection

Search is a case-insensitive normalized substring match across source, aliases, meaning, category, candidate translations and effective custom translation. Preserve accents; do not add fuzzy search. Search AND category AND status all apply simultaneously. Use a 150 ms input debounce for filter application while preserving the input cursor and IME composition. Never replace/remount the search field on each keystroke.

Category badges show counts in the full review, as in v33; header totals also cover the full review, not just filtered rows. Server-side pagination, if needed, must preserve these full-set counts and search semantics. Do not filter only the currently downloaded page and report it as the whole glossary.

Retain the selected term while it still matches. If it disappears, select the first matching term after the old position, otherwise the closest preceding term, otherwise show the empty detail state. This explicit successor rule prevents Review & next from skipping an extra term after Unreviewed filtering removes the current term. Preserve left-list scroll; scroll the new selected row minimally into view.

Changing only selection or filters does not mark anything reviewed. A selected detail body's scroll resets when selecting a different term, not when the same term's counters update. Empty match state: `No terms match the current filters.` No selected term: `Select a terminology item.` A loading/error state must not be presented as zero terms.

### 11.3 Candidate and custom-form controls

Candidates retain backend order, text, note and confidence. Use candidate IDs when supplied; if the backend uses one-based `select`, map explicitly at the API boundary and test it. Do not assume a zero-based wire format.

Click a candidate: choose it, clear custom override, mark the term unreviewed and invalidate current glossary confirmation as required by the application. Save through the backend; do not change durable status solely in React. Selecting the already active candidate with no custom form is a no-op.

The custom form is a local draft while typing. Commit its trimmed value on blur or Enter, not every keystroke. A nonempty custom form overrides the selected candidate. Clearing it restores the selected candidate. Effective translation in the list and detail must prefer the custom form: this corrects the mock's candidate-only list preview.

Keep source explicitly saves source text as custom translation. Use candidate clears the custom override and uses the currently selected candidate. If no valid candidate exists, disable Use candidate with an explanation; do not silently choose a fabricated candidate. Any effective translation change makes the term unreviewed. Identical values do not create gratuitous revisions.

On save failure retain the draft and selection visibly. On review-revision conflict never silently merge or overwrite another editor. Display `Review changed elsewhere. Reload the latest review to continue.` Preserve the unsaved form locally until the user explicitly reloads/discards or deliberately reapplies against the latest revision. Do not automatically resend stale mutations with a new revision.

### 11.4 Footer actions

Keep all three buttons as in v33:

- `← Previous`: move to the previous filtered term without changing reviewed state.
- Primary `Review & next` for an unreviewed term: first flush a pending custom-form save, then validate and mark the current term reviewed, wait for success, then select the next filtered term using the pre-mutation position/successor rule.
- Primary `Next →` for an already reviewed term: navigate only.
- Secondary `Next →`: navigate without marking reviewed.

Previous is disabled at the first item; navigation-only Next is disabled at the last item. Review & next remains enabled for the last unreviewed item because it still has useful work: mark it reviewed and remain on it, or show the no-matching-items state if Unreviewed filtering empties the list. Never wrap to the first term automatically.

Do not navigate after a failed review save. While a mutation is pending, serialize all other review mutations for that workspace; keep scrolling/search usable but preserve the edited term context. Use the full review revision returned by every successful mutation for the next mutation, not separate invented per-term revisions.

### 11.5 Confirmation is real approval

**P/B:** `reviewed`, `confirmed` and committed application approval are separate concepts. `Confirm glossary` must complete the real human gate; setting a browser boolean or only a draft flag is insufficient.

Enable confirmation only when the full glossary has zero remaining unreviewed terms, valid effective selections, no dirty/pending editor mutation, a fresh revision and an allowed backend approval action. Uncertain items must be explicitly reviewed; uncertainty alone must not introduce a new undocumented block beyond existing application rules. For an empty glossary, rely on the backend's validated empty-review gate rather than frontend autoapproval.

One application-level confirm-and-approve operation must validate the supplied expected revision under the existing project mutation lock, confirm the exact reviewed state and commit approval with existing stale-checkpoint/publication semantics. If the current API exposes these as two primitives, add an application orchestration command with a structured outcome; do not implement a race-prone client-side check-then-approve sequence. Never show Glossary confirmed until committed current approval is verified. A crash/failure between draft confirmation and approval must leave translation blocked and be safely recoverable.

On successful confirmation, stay on Review, show the badge, update Workspace/Home gates and do **not** automatically start translation. Run is a separate user action. Reconfirm glossary performs the same validation against the latest revision. Review remains readable after approval.

Changing terminology after approval invalidates the appropriate backend gate; the badge and pipeline update from that result. Keep historical translated text/checkpoints, and let the application mark stale outputs. Never delete or secretly retranslate them.

Mutations and approval are disabled while a project writer/cleanup owner is active; read-only review remains available where supported. The browser page owns no writer lock or long-lived Store. A newer review schema or unsupported field produces a visible incompatibility error, not dropped evidence or a partially saved term.

## 12. Reader mode: bounded scope from v33

**R:** v33 explicitly leaves Reader simple: a book list at the left and a reading area at the right. It does not specify a complete replacement for the existing sophisticated Reader. Do not invent a new reading application to fill that gap.

**P — required real functionality:** replace placeholder paragraphs with actual verified P5 output. Keep the reference sidebar/prose layout and the independent Work/Reader state. Add a compact labeled chapter selector and Previous/Next chapter controls at the top of the reading area; this is an explicit operability addition, not something drawn in v33.

List workspace-linked books, including archived ones. Sources with no workspace are not reading items. Books with no verified translated text may appear with `No translated text yet`; selecting them shows that explanation, never source text pretending to be a translation. Default selection: last valid reading workspace, otherwise the first with readable output, otherwise the first linked workspace/empty state.

Render only chapters/blocks the existing Reader application validates as readable. Preserve chapter order, stable block IDs, paragraph boundaries and any unavailable/stale notices the application supplies. Do not concatenate across gaps as if missing text existed. Do not fetch all books' prose for the sidebar.

Persist reading position as chapter/block/offset (not only pixel position) scoped to server/workspace. Restore after content arrives. New P5 output updates availability in the background without jumping the reader, changing selected chapter or replacing an actively selected text range. Reconcile when switching books, focusing the tab, or receiving relevant completion events. Do not mark prose unavailable merely because an unrelated workspace fails.

No new font/margin settings, context-helper redesign, marker interaction redesign, reader-progress metric or annotation editor is invented in this task. Preserve the existing standalone Reader and all its features without regression. Their later incorporation into this new shell is explicitly deferred because v33 itself defers them. Do not call this basic v33 Reader a full feature-parity replacement for the legacy Reader.

## 13. Settings modal

**R:** retain the three tabs Paths, Models, Interface and the reference modal layout. Remember the chosen tab as browser preference. Settings must not start/stop jobs on open or close.

### 13.1 Paths

**P/B:** the mock allows arbitrary installation paths in localStorage, conflicting with the real server's confined-root model. Production fields are **read-only safe labels/configured state**, not editable absolute paths. Keep the three roles: Source books directory, Workspace directory, Default output directory. Explain `Managed by server configuration; changes require server configuration/restart.` Display Not configured when appropriate.

Do not add an HTTP endpoint that switches roots or browses the host filesystem. Actual workspace/source/credential paths must not leak through values, labels, tooltips, errors, logs or form defaults. The normal Work screen uses the same safe source label.

### 13.2 Models

Show actual configured profile names, stable swatches, safe provider description and diagnostic state. Settings opened in a workspace context describes that workspace's effective profile scope; at Home it shows installation-level profiles. Display a small scope subtitle so profiles from differently configured workspaces are not conflated by name. Never assume the first three profiles are Codex just because the sample array is ordered that way.

Test is a real, explicit, **non-generating** diagnostic. It must not send a translation/prompt, consume generation quota or silently run the CLI live smoke command. Use existing safe configuration/auth/connectivity diagnostics. Backend capability states whether a resolved profile is testable. A profile that requires workspace context is disabled at Home with `Select a workspace to test this profile.`

After clicking Test: one request/job; show Testing… and disable only that profile's button. Show the actual result and tested timestamp: e.g. Configuration valid, Available (only if reachability was actually checked), Authentication required, Unreachable, or Diagnostic unavailable. Do not equate valid configuration with a successful provider connection. A request failure remains visible with Retry; no 700 ms success timer. Redact sensitive details.

`+ Add model profile` is only a toast placeholder in v33. In production retain it disabled with an explicit `Profile editing is not part of this version; configure profiles on the server.` explanation. Do not invent an editor, API-key fields or a fake saved profile.

### 13.3 Interface

Theme choices are Dark and Light, synchronized with the topbar toggle and persisted locally. **P:** replace `Reset mock data` with `Reset interface preferences`. Confirm that it only resets this browser's theme/layout/filter/reading-location preferences and does not affect books, workspaces, jobs, profiles, markers, review or checkpoints. Reset must never clear a pending-operation idempotency receipt or request cancellation. Do not ship any fake-data reset path in production.

## 14. Real-time state and refresh contract

This section is **P/B**. v33 has no network synchronization; its timers/localStorage are not an implementation model.

### 14.1 Ownership and query keys

Create one TanStack Query client and one app-level SSE owner per browser tab. Use keys containing a safe server scope ID and workspace ID, for example:

```text
[scope, "capabilities"]
[scope, "library"]
[scope, "workspaces", "active"]
[scope, "workspaces", "archived"]
[scope, "workspace", workspaceId, "pipeline"]
[scope, "workspace", workspaceId, "section", sectionId]
[scope, "workspace", workspaceId, "review"]
[scope, "workspace", workspaceId, "evidence", termId, reviewRevision]
[scope, "workspace", workspaceId, "profiles"]
[scope, "workspace", workspaceId, "usage", phase]
[scope, "workspace", workspaceId, "activity"]
[scope, "workspace", workspaceId, "reader", chapterId]
```

Never key review/evidence/chapter data only by a term/chapter ID reused across books. When workspace A's slow response arrives after navigating to B, it may update A's cache, never B's screen. Abort unused reads where appropriate; aborting a browser fetch never cancels a server job.

### 14.2 SSE connection and boot

Use the existing `/api/events` stream, with named `snapshot` and `progress` listeners, not only `onmessage`. Connect at application scope to all workspaces and dispatch by IDs; do not open one stream per row, component, phase or job. This also avoids requiring the deferred filtered-cursor optimization.

Initial boot: fetch capabilities/scope; create query client/stream owner for that scope; establish SSE and receive its initial job snapshot; fetch visible read models and reconcile them against any newer events. Render layout skeletons immediately. Do not delay the entire interface until every book/prose/usage request completes.

The stream remains connected while switching Work/Reader, changing workspaces, opening drawers and closing accordions. React development StrictMode must not leave duplicate live streams/listeners or duplicate mutations. On app unmount close only the browser stream, not workers.

### 14.3 Two different replay watermarks

Maintain separate structures for **job-state sequence** and **activity-event identity**.

1. On `snapshot`, validate the payload, replace/merge job snapshots by authoritative per-job sequence, and establish each job's state watermark. The existing snapshot has a global cursor but no SSE `id`; do not treat it as a replayed progress event.
2. On `progress`, validate IDs, sequence, envelope and metadata. A progress event at or below a job's state watermark must not roll a newer snapshot backward.
3. That older replay event may still be new to the activity log. Deduplicate log entries by global event ID, independently from whether the event can update current state. Do not lose replay history by using the state watermark as the log filter.
4. Advance the received-stream cursor only from validated stream events/cursor reconciliation. Do not synthesize event IDs or use array positions as identity.
5. Update cheap job/activity projections immediately. Invalidate authoritative domain read models when an event indicates durable progress or a terminal transition; a waiting/generation event alone cannot create a completed checkpoint.
6. On a sequence gap, incompatible payload, unexpected new server scope or pruned history, refetch affected snapshots/read models. Unknown future event kinds may be ignored for projection after validation but must not crash the application or mark work complete.

Use EventSource's built-in Last-Event-ID reconnection on the same object. Do not pretend its constructor accepts arbitrary HTTP headers. For an explicitly created new stream or page reload, use the supported `after` parameter with a cursor belonging to the same server scope/history. On history reset or a snapshot cursor lower than the retained cursor, clear the obsolete replay/state watermarks and resynchronize. Do not keep an old cursor across another workspace root served at the same origin.

### 14.4 Invalidation matrix

| Event/acknowledged change | Refetch/update |
| --- | --- |
| job starting/running/stopping | jobs, matching Home row, matching workspace runtime projection/action gates |
| pass checkpoint committed/recovered | pipeline, matching phase details, relevant usage; Reader availability on P5 |
| analysis completed | pipeline, review summary/state, Analyse details, Home |
| review edit/reviewed flag | review/evidence as needed, approval gate, pipeline/Home |
| glossary confirmed and approved | review, pipeline, stale-result/publication state, Home |
| profile assignment | profiles/config revision, planned cells and open drawer; retain historical result models |
| processing/content-type change | section, plan/read model, phase counts, publication state as returned |
| publication started/completed/failed | pipeline, Publish page, artifact availability, Home |
| job terminal/abandoned | all visible data for that workspace plus jobs; never infer next phase only from job outcome |
| archive/restore/create/prepare success | library, active/archive lists, matching workspace/capabilities |
| Reader P5 availability change | metadata/TOC, not forced replacement of current prose/scroll |

Requests should be coalesced per query key. Durable-event invalidation may be throttled to one request per 1000 ms per visible workspace, with a trailing refresh; terminal transitions and successful direct mutations flush immediately. Streamed character counters may render at most every 100 ms; terminal/error updates must not wait behind this throttle. A local reference-test event must visibly update runtime status within 250 ms, and a durable pipeline query must reconcile within 1000 ms plus its network time.

Do not re-render/remount the whole application on each event, reset filters, collapse accordions, close drawers or move scroll. No progress simulation timers.

### 14.5 Reconnection, fallback and cross-client edits

Show one compact connection indicator near global controls: Live, Reconnecting…, or Offline. Do not duplicate an error banner in every card. Keep last known content visible with a stale indication; disable state-changing actions until a fresh reconciliation succeeds. A long provider wait is not a disconnected stream. SSE keepalive comments are not normal JS message events: do not implement a false timeout based on absence of `progress` callbacks.

On transport failure retain the EventSource's reconnect behavior. Add bounded fallback polling while the connection is unhealthy: jobs and visible active-workspace read models every 5 seconds, idle visible lists every 15 seconds. Stop the fallback when reconnection plus reconciliation succeeds. Do not replay mutations or automatically start abandoned/cancelled jobs.

Even with healthy SSE, HTTP/CLI edits may not have job events. Reconcile visible Home/workspace summaries every 15 seconds, visible review revision every 5 seconds while editing is idle, and Reader availability every 15 seconds. Prefer lightweight conditional revision/ETag queries; fetch full content only when changed. Refocus/reconnect triggers immediate reconciliation. Pause ordinary background-tab polling; resume on visibility/focus. Keep a single coordinator so multiple components do not multiply intervals.

If the current backend emits resource-change events, consume them for earlier invalidation but preserve periodic reconciliation as repair for missed/CLI changes. A dirty editor is never overwritten by a background refresh: show that newer state exists and use the conflict flow.

Configure Query stale time, retries and focus refetch explicitly instead of accepting uncontrolled refetch storms. GETs may retry twice with 1 s then 2 s backoff on transient network/5xx errors; do not retry 4xx conflicts/validation errors. Mutations have no automatic retry.

### 14.6 Snapshot races

Consume backend revision/snapshot-generation tokens where available. Cancel/ignore a read invalidated by a newer domain mutation before applying it; issue a fresh read. Opaque digests are equality tokens, not sortable version numbers. If a new event arrives while a snapshot request is in flight, mark that key dirty and schedule one trailing reconciliation instead of installing an older state as final.

Backend action gating always rechecks current state under the correct lock, even when the UI just received an allowed action. A cached enabled button cannot authorize a concurrent mutation. Do not serialize all workspaces through one browser mutex; mutation ordering is per affected revision/project.

## 15. Mutation lifecycle, errors and persistence

### 15.1 Common UI lifecycle

Idle → local submitting indicator → backend acknowledgement → authoritative pending/running or saved state → query reconciliation. Use local busy state only for transport/UI feedback; it is not a persisted domain status.

Immediately disable duplicate submission for the same action. Keep the rest of the interface usable. For autosave selects show Saving… next to the affected control/drawer without shifting layout; apply the acknowledged effective value. If a provisional selection is shown while saving, mark it provisional and roll it back on failure. On success use the new returned revision, update related caches and show a short nonblocking toast. Do not toast every streaming event.

Serialize review mutations by full review revision; serialize section/profile changes by workspace configuration revision. Do not use one stale revision for concurrent optimistic patches. Preserve pending input if another update changes the underlying object. Navigation away from a dirty, unsent text field requires an explicit discard/stay choice; a submitted safe request can finish in the cache without changing the new page.

### 15.2 Unknown outcome and idempotency

A timeout does not prove a mutation failed. Job starts/preparation/workspace creation require an idempotency key generated once per user action and a backend-persisted request-key/result mapping. Repeated transmission of that same action/key returns the same outcome; reusing the key with a different payload returns a conflict. Keys survive a server restart for at least 24 hours. Ordinary deliberate new runs use a new key.

If acknowledgement is lost, show `Request status unknown — checking server…`, query current jobs/workspace state or the idempotency receipt, and reconcile. Never start another job blindly or claim success from a client timer. Approval/archive/config writes use expected revisions; after a lost response, refetch before offering a deliberate retry. Do not silently reinterpret a later 409 as success without matching the original operation.

### 15.3 Error presentation

Normalize the current API's errors at one boundary and introduce safe machine-readable codes where missing. Target envelope:

```json
{
  "error": {
    "code": "review_revision_conflict",
    "message": "Review state changed.",
    "details": {"resource": "review"}
  }
}
```

Required distinguishable codes include validation_error, not_found, forbidden_origin, workspace_busy, workspace_archived, review_revision_conflict, config_revision_conflict, approval_required, analysis_membership_locked, model_change_confirmation_required, unsupported_source, import_disabled, publication_not_ready, provider_diagnostic_unavailable and internal_error. Keep established equivalents if already public, and map them explicitly in the implementation map rather than creating a parallel contract.

Use appropriate 400/403/404/409/413/422/500 statuses and retain existing security checks. Inline controls show actionable validation/conflict messages. Page-level load errors include Retry and keep navigation available. Background read errors retain stale data instead of wiping the UI. Unknown errors show a safe generic message and diagnostic correlation ID, not stack traces or raw provider output.

## 16. Required backend/API surface and DTOs

This is a **target contract**, not a claim that these endpoints are all currently present. At the inspected baseline only the runtime subset was exposed. Inspect newer code first. Reuse established equivalent routes; otherwise implement these narrowly scoped application/adaptor additions. Freeze/document actual wire contracts before integrating components.

### 16.1 Capabilities, source catalog and workspace metadata

| Capability | Target route when no equivalent exists | Required behavior |
| --- | --- | --- |
| capabilities | `GET /api/capabilities` | safe scope ID; source import/diagnostic availability; supported formats/actions; API contract version |
| source library | `GET /api/library` | safe source IDs/labels, metadata, vetted covers, linked workspace ID; separate configured/empty/error state |
| draft workspace | `POST /api/workspaces` | source ID only plus explicit safe options; idempotent linkage; no import or model generation |
| active/archive list | `GET /api/workspaces?archived=false/true` | metadata, authoritative progress, current/next action and updated timestamp |
| plan/read model | `GET /api/workspaces/{id}/pipeline` | phase status, real section/pass aggregates, action gates, revisions, active/cleanup ownership, publication |
| section preview | `GET /api/workspaces/{id}/sections/{sectionId}` | safe source blocks, metadata, overrides, revision; bounded continuation for long sections |
| section metadata | `PATCH /api/workspaces/{id}/sections/{sectionId}` | expected config revision; processing/content-type/overrides with declared invalidation policy |
| workspace profile assignment | `PATCH /api/workspaces/{id}/settings` | expected config revision; per-pass IDs; no arbitrary native paths or secrets |
| archive/restore | `POST /api/workspaces/{id}/archive`, `/restore` | expected lifecycle revision, no writer/cleanup, no file deletion |

Backend-owned draft/archive/source-link metadata must not fabricate `book.json` or a checkpoint database before successful preparation. Use the existing repository/state storage conventions and a small metadata store if needed; project checkpoints remain the translation truth. Root confinement and stable source IDs are mandatory. A missing imported state is a valid draft, not silently a corrupt completed project.

### 16.2 Runtime and diagnostics

Keep existing `GET /api/jobs`, `GET /api/jobs/{jobId}`, `POST /api/workspaces/{id}/jobs`, `POST /api/jobs/{jobId}/stop` and `GET /api/events` compatible. Existing job operations analyze/translate/publish remain; preparation uses the validated import/prepare job implementation when available or a separate typed specification, not arbitrary subprocess arguments.

Retain `GET /api/workspaces/{id}/usage` and add only required phase/unit filters. Provide bounded activity and safe attempt-metadata queries, such as `/activity?limit=120` and `/attempts?phase=translate&limit=50`, with cursor pagination. Do not build log display by downloading raw prompt/response artifacts or parsing CLI text.

Publication details may use the pipeline publication DTO plus a dedicated details query. Downloads use a fixed authorized current-publication endpoint, such as `GET /api/workspaces/{id}/publication/download`; the server resolves and validates the artifact, not a browser path argument.

### 16.3 Review, Reader, settings

Reuse/complete the following application-backed route families:

```text
POST  /api/workspaces/{id}/review/prepare
GET   /api/workspaces/{id}/review
GET   /api/workspaces/{id}/review/terms/{termId}/evidence
PATCH /api/workspaces/{id}/review/terms/{termId}
POST  /api/workspaces/{id}/review/confirm-and-approve
GET   /api/workspaces/{id}/reader
GET   /api/workspaces/{id}/reader/chapters/{chapterId}
GET   /api/workspaces/{id}/profiles
GET   /api/workspaces/{id}/settings
GET   /api/settings
POST  /api/workspaces/{id}/profiles/{profileId}/diagnostic
```

Do not mutate via GET. If review preparation is required before opening the page, use an explicit idempotent preparation command and show Preparing review…; never create draft state accidentally in a supposedly read-only query. Do not call prepare repeatedly on every render or overwrite human edits.

Review confirmation must invoke the real approval application operation; existing per-term/bulk/confirmation routes and standalone clients remain compatible. New UI requires only the controls actually specified, not every possible administrative endpoint. Existing Reader context/marker APIs remain intact even though the v33 basic Reader does not redesign those interactions.

### 16.4 Minimum normalized view data

Write explicit serializers and strict TypeScript types/runtime validation. Do not expose arbitrary `asdict()` results. At minimum the adapter must make these facts available:

```text
Workspace summary:
  id, source_id, title, author, source_language, target_language,
  word_count|null, cover_resource|null, archived, prepared,
  updated_at, lifecycle_revision, progress{percent|null, basis, counts},
  runtime{active_job|null, cleanup_in_progress},
  primary_action{kind,label,allowed,reason_code|null}

Pipeline:
  workspace_id, snapshot/revision tokens,
  phases[{key,status,clickable,reason,completed_units,required_units}],
  sections[{id,ordinal,title|null,fallback_excerpt,content_type,processing,
            editability,model_overrides,passes[1..5]}],
  review{prepared,revision,reviewed,total,uncertain,confirmed,approval_current},
  publication{state,current,relative_output|null,size_bytes|null,checks},
  actions keyed by operation with allowed/reason and current ownership

Section pass:
  pass_no,state,required_units,completed_units,stale_units,
  active_unit_id|null,planned_profile_id,actual_result_profiles[],
  result_usage availability/values, historical_results_retained

Profile:
  stable_id,display_name,scope_label,provider_kind,model_id|null,
  stable_palette_index,diagnostic_capabilities

Review:
  revision,terms or a complete server-filtered/paginated collection,
  global counts,confirmation and committed approval state;
  term ID/source/category/meaning/aliases/candidates/current choice/custom/
  reviewed/uncertain/evidence references with explicit candidate indexing
```

A required field unavailable in the actual backend is a backend implementation gap, not permission to invent a frontend value. Distinguish null, empty collections, measured zero, skipped/not-applicable and unprepared state. Stable IDs and persisted timestamps must come from the backend. Do not introduce recursive giant schemas or provider-specific nested payloads into the UI contract.

## 17. Loading, performance and interaction stability

Keep the page shell/header visible during navigation. Use skeletons matching actual rows/cards only on initial load; background refresh keeps existing content. A query for one workspace must not block rendering another workspace's cached state. No full-screen spinner on every SSE event.

Cache source metadata separately from detailed prose/evidence. Read-only requests must not hold project writer locks or Store sessions for a page/SSE lifetime. Backend snapshots are short-lived and checkpoint-validating. A slow book query must not block stop control for another book.

Bound activity memory to 120 retained UI entries per visited workspace and render the most recent 80 in the accordion, matching v33's display scale. Badge reports retained activity entries, not invented lifetime event count. Fetch recent history on first expansion/detail-page use; merge incoming events by ID. Do not download every registry event on every page load.

On first activity expansion scroll to the bottom. Subsequently auto-follow only when the user is within 24 px of the end; scrolling up disables auto-follow and shows a small `New activity` affordance to return to the end. Never yank scroll while someone reads an error. Closing the accordion stops rendering work, not ingestion or server execution.

For large plans/glossaries, use pagination or virtualization only where needed, preserving table/list appearance, keyboard selection and full-set counts. Test with at least 1000 sections and 2000 review terms. Keep controls responsive during a synthetic stream burst of 100 events/s; batch transient rendering without dropping terminal events or persistent activity identity. This is a test workload, not an assertion of expected real provider traffic.

## 18. Explicit departures, non-features and safety boundaries

These are intentional P decisions, not hidden visual redesign permissions:

| Prototype shortcut/omission | Required production behavior |
| --- | --- |
| localStorage stores fake projects/jobs/review | backend is authoritative; only browser preferences stay local |
| global timer advances model work | independent real supervised jobs; no simulated execution |
| fixture `active` marks can exist with `running=false` | real job/task linkage required for animation |
| sample approved glossary has unreviewed sample terms | use actual approval/review state, never copy contradictory fixture flags |
| weighted progress uses one mock row per unit | backend uses actual required units while keeping the documented weighted presentation |
| token/size/validation values are invented | measured/validated values or explicitly unavailable |
| model colors follow current configuration even for completed work | historical result provenance retained |
| F/T/E changes freely after P1 | section 8's explicit membership-lock and dormant-result policy |
| browser flag confirms glossary | revision-protected real application approval |
| stopped/publication failure can look Publishing forever | real terminal state and explicit publication retry |
| settings accept arbitrary local path strings | read-only safe server configuration labels |
| Test always succeeds after 700 ms | real non-generating diagnostics or explicitly unavailable |
| Add model profile opens only a toast | visibly disabled/explained; profile editor deferred |
| Reader is placeholder prose | bounded basic real P5 Reader; rich legacy-reader redesign deferred |
| mock reset erases fake data | browser-only interface-preference reset |
| unused type-menu/section-model-icons helper code | do not add controls not rendered by v33 |
| phase diagnostic artifact editor is only suggested text | actual availability/metadata only; no arbitrary artifact editor |

No authentication redesign, uploads, general filesystem browser, destructive delete, cumulative-analysis reset UI, model-profile editor, cloud deployment, additional provider scheduler, external telemetry, web sockets or product-wide theme redesign is authorized. Preserve standalone CLI/Review/Reader behavior. Do not use these exclusions to omit required real endpoints or working controls.

## 19. Security and data integrity acceptance rules

Preserve loopback binding by default, Host/Origin/Fetch Metadata checks, JSON/body limits, revision checks, root containment, symlink protections and safe errors. Apply the same guard to POST/PATCH/DELETE and diagnostic routes. Non-loopback use remains trusted-network use until separately authorized authentication exists.

No credentials, auth/home paths, prompts, raw provider replies or absolute source/workspace/output paths appear in client responses, JS bundles, source maps published by default, logs, toast text or SSE replay. Old persisted events must remain sanitized. Source/translation/evidence text is untrusted content: render as text or through the existing strict safe block converter. Never run embedded scripts, external resource URLs, event handlers or unsafe links from an EPUB.

Do not weaken project `.lock`, review/marker revisions, one-active-writer exclusion or descendant cleanup to make a UI action pass. Archive/config mutations remain blocked during cleanup. A refresh must not replay a POST. A readonly GET must not execute an operation.

Tests and screenshots use fabricated/offline test projects. Never modify real translation workspaces or send real model requests to produce a prettier demonstration. Do not include real book text, credentials or real private project data in committed fixtures beyond the source artifact the user supplied.

## 20. Tests and verification: completion requires evidence

### 20.1 Test layers

Run existing Python, legacy JavaScript and browser smoke suites. Add frontend unit/component tests, HTTP contract/integration tests, real-browser end-to-end tests using a deterministic offline backend, and visual comparisons against the actual v33 reference.

Unit-test pure reducers/formatters separately from components. Test the real HTTP server with offline worker/providers, not only mocked `fetch`. Use Playwright or an established repository browser runner for actual navigation/mutations/reconnection tests. Stub model execution, not the entire backend domain workflow.

### 20.2 Required behavioral scenarios

Use these IDs in test names or a traceability table:

| ID | Scenario and required observation |
| --- | --- |
| V33-01 | Home matches active-list/library structure, counts, initials, responsive action widths |
| V33-02 | Open source creates one draft; double-click/two tabs return the same workspace; no generation |
| V33-03 | Prepare runs once, survives navigation/reload, ends only after real plan creation |
| V33-04 | Multi-unit sections aggregate correctly; P1 is shown and globally gated before P2–P5 |
| V33-05 | Every unblocked rail tile opens its own correct page; blocked tiles do not execute |
| V33-06 | F/T/E mode saves, revision conflicts, exclusion styling and allowed transitions follow section 8 |
| V33-07 | Content type never changes processing; forbidden P1 membership change is explained |
| V33-08 | Drawer preview is genuine escaped source; long/untitled sections do not break layout |
| V33-09 | Profile inheritance and overrides survive reload; historical result colors do not change |
| V33-10 | Model-change permission is explicit; cancelled confirmation keeps previous assignment |
| V33-11 | Two workspaces run together; switching/closing the tab does not cancel either |
| V33-12 | Stop A shows Stopping until authoritative terminal/cleanup state; B remains running |
| V33-13 | No duplicate starts on slow acknowledgement, lost response, retry or StrictMode |
| V33-14 | Old replay after newer snapshot appears once in activity without rolling current state backward |
| V33-15 | Duplicate/out-of-order/gapped events and pruned/reset history reconcile safely |
| V33-16 | SSE reconnect, fallback polling, offline disabled actions and refocus refresh work |
| V33-17 | Slow A response cannot populate B's page; two books with identical term IDs stay isolated |
| V33-18 | Refresh preserves filters, selected term, accordions, drawer and scroll as specified |
| V33-19 | Review search/category/status intersect; counts cover the full glossary |
| V33-20 | Candidate/custom/Keep source/Use candidate persist correctly and invalidate approval |
| V33-21 | Review & next under Unreviewed filter never skips the next term; last item can be reviewed |
| V33-22 | Previous/Next do not mark reviewed; pending/failed save cannot navigate deceptively |
| V33-23 | Confirmation validates latest full revision and commits real approval; no automatic translation |
| V33-24 | Two review editors conflict safely; dirty text is not overwritten by background refresh |
| V33-25 | Editing approved terminology shows the real stale/approval/publication consequences |
| V33-26 | Partial translation progress and successful limited jobs never imply whole-book completion |
| V33-27 | Automatic publication is not duplicated; failure enables publish-only retry; size/checks are real |
| V33-28 | Usage zero/null/bytes/reason/cache/retries/cumulative updates are not miscounted |
| V33-29 | Archive is metadata-only, rejects active/publishing/cleanup work, survives rescan/restart; Restore does not Run |
| V33-30 | Reader shows verified text or explicit absence; archived books remain readable; Work state preserved |
| V33-31 | New P5 availability does not move Reader position or destroy text selection |
| V33-32 | Settings reflect real profile scope; Test never generates; no fake Add profile/reset/path mutation |
| V33-33 | Theme switches without reload; keyboard/focus/Escape/scrim behavior is correct |
| V33-34 | Raw paths/secrets/HTML injection are rejected/redacted on new routes and historical SSE |
| V33-35 | Deep frontend URL reload works; bad API/asset path returns the correct non-HTML error |
| V33-36 | Browser remains responsive on large datasets and stream bursts; terminal events are not dropped |
| V33-37 | Server restart produces abandoned/reconciled jobs, not silent resume; checkpoints remain reusable |
| V33-38 | All existing runtime, application, legacy Review/Reader and CLI regression tests remain valid |

Include a complete offline E2E journey: discover source → draft → Prepare → processing/profile selections → global P1 → review all terms → Confirm glossary → Run P2–P5 → automatic publication → inspect details/download → basic Reader → archive → restore. Also cover import-disabled deployment; it must keep already imported workspaces functional without pretending preparation is supported.

### 20.3 Visual comparison

Open the unmodified v33 HTML and production UI in the same browser/font environment. Use deterministic fixtures and clocks; mock only test data/network providers. Never enable fixture mode in the normal production bundle or public API.

Capture Home, unprepared Workspace, prepared Workspace, open section drawer, Review with long evidence, all four phase detail pages, Settings tabs, Archive drawer/confirmation and basic Reader. Use dark and light themes. Required baseline viewport is 1440×1000; also verify 1024×768, 820 px, 720 px and 390×844 plus breakpoint-adjacent widths for obvious regressions.

Compare stable region geometry/colors and a screenshot diff. Freeze animations and mask only genuine timestamps/live counters/test-generated identities; do not mask entire panels to hide a redesign. Use a 1% maximum differing-pixel budget for stable full-screen reference fixtures with the same renderer, plus direct checks for key sizes/columns. Differences explicitly required by P rules (real status correctness, read-only paths, basic Reader controls, unavailable metrics) must be listed individually; do not label unrelated layout changes as production differences.

Reference screenshots bundled with this prompt are supplementary evidence, not a substitute for running the original HTML. They were captured from the original HTML in Chromium using an in-memory storage shim, not a live backend, and include the mock's inconsistent fake state. They are not production tests. Generate consistent test-state screenshots for state assertions rather than copying contradictory fake flags into production.

### 20.4 Required commands and reporting

Use the actual repository scripts, adding explicit scripts where missing:

```text
uv sync --group dev
uv run --group dev python -m pytest -q
node --test tests/*.cjs
frontend clean lockfile install
frontend typecheck
frontend lint
frontend unit/component tests
frontend production build
frontend/backend browser E2E
visual reference comparisons
existing standalone browser smoke
```

For npm defaults the frontend commands should be `npm --prefix web ci` and named `typecheck`, `lint`, `test`, `build`, `test:e2e`, `test:visual` scripts. Do not claim a nonexisting script ran. Test the built bundle through `serve`, not only Vite dev mode. Confirm that running the built application needs no Node development server.

Do not silently update screenshot baselines to accept implementation drift. Review diffs, fix layout, and document every authorized P departure. Record test commands, actual counts/results, browser/version, test duration if measured and artifact locations. Report blocked/skipped tests honestly.

## 21. Implementation order and final deliverables

Proceed in verified increments, retaining the overall acceptance scope:

1. Establish original-v33 provenance, current repo state and capability/contract map.
2. Complete any narrowly missing backend capabilities, typed serializers, safe mutation semantics and single-server frontend serving; test before building fake UI around gaps.
3. Implement tokens, app shell, navigation and shared primitives against reference screenshots.
4. Implement query/SSE/reconciliation/mutation infrastructure and test races before wiring all screens.
5. Implement Work/library/drafts/archive, Workspace/sections/profiles, phase details, Review, basic Reader and bounded Settings exactly as specified.
6. Exercise real offline end-to-end operation, long-running multiworkspace/reconnect flows and visual/accessibility regressions.
7. Review the entire diff and security boundaries; update documentation/traceability and commit cohesive changes. Preserve unrelated user changes; do not force-reset or force-push.

Required documentation: API implementation map, frontend run/build/test instructions, state/replay design, explicit reference-to-production departures and final verification report. Keep documents in English. Include the original v33 as the reference and mark the reconstruction superseded.

Final report must provide commit SHA(s), exact commands/results, completed screen/control checklist, actual API routes used/added, remaining explicitly deferred features, screenshot comparison artifacts and the command for starting the integrated application. Distinguish what was implemented from what was only inspected. Do not say the application is complete with nonfunctional mandatory controls, mocked runtime state or a silently missing backend prerequisite.

## 22. Source traceability and technical references

Primary source: original user-uploaded `intelitex_workspace_mockup_v33(1).html`, identified by the SHA-256 in section 0. The adjacent `REFERENCE-INVENTORY.md` maps visible features to functions/source line numbers and records the screenshot capture conditions.

Inspected repository sources (baseline only; recheck current HEAD):

```text
hipotures/intelitex @ 1f85da8c0bd38ebbe01c3f867ef70f1793a5b957
  docs/mockups/README.md
  docs/server-runtime.md
  bookpipe/server/service.py
```

The following primary technical documentation informed only transport/tooling details, not the visual design or product features:

```text
WHATWG HTML — Server-sent events
https://html.spec.whatwg.org/multipage/server-sent-events.html

TanStack Query — Important Defaults
https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults

Vite — Backend Integration
https://vite.dev/guide/backend-integration
```

EventSource supports named events and native reconnection; a newly constructed native EventSource does not offer arbitrary request headers. Query defaults must be explicitly configured for this application's data flow. Vite build assets must be integrated with the one backend rather than requiring a production development server. Product-specific timings, safety restrictions, endpoint additions and completion rules in this document are explicit P decisions, not promises made by those documentation sources.

---

**Completion criterion:** the user can recognize the original v33 interface, and every non-deferred visible control operates on real, current, isolated backend state. No browser timer, placeholder profile test, invented metric or localStorage job flag may masquerade as application behavior.
