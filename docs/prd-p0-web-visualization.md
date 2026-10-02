# PRD: P0 workspace visualization and phase details

Status: Ready for implementation by Codex.
Date: 2026-10-02.
Repository: `hipotures/intelitex`.
Target branch: `work/p0-p5-persistent-sessions`.
Reviewed implementation: `4bed8f2090b179bb66a9cb373f4ba143cf9e40bc`.

## 1. Objective and delivery boundary

Make persistent source preload, P0, visible and inspectable alongside P1-P5. Add a P0 column to the workspace section table, insert a P0 tile into the workflow rail, and implement a P0 detail page that follows the existing P1 detail page's layout, components, information density, and interactions. The page must be accessible before P0 has run, including before a persisted P1 analysis plan exists.

The requested experience is the P1 page adapted to P0, not a new dashboard, a JSON inspector, or a redesigned workflow. Include stage-level metrics, model assignments, historical usage by model/profile, chapter totals, unit/session totals, source preview, progress, and a targeted Run control. Run on a P0 row prepares only that source-session target; it must not execute P1 or any translation pass.

This document supersedes only the web-UI deferral in `docs/prd-p0-p5-persistent-sessions.md`. The source-session architecture, lazy P0 execution, original dependency graph, outcome-first revert policy, and recovery fixes remain authoritative. Read that PRD, `AGENTS.md`, and both source-session validation reports before implementation.

Implement in the existing dedicated worktree for this branch. Do not switch, modify, merge into, deploy, restart, or probe the production `main` checkout. Use a fresh test workspace with its own chapter-local private Codex homes and isolated service state. The currently translating production book is not a test fixture. This PRD authorizes implementation and offline validation, not paid/cloud inference or production operations.

The commit adding this PRD is documentation-only. Codex subsequently implements the scope below and records actual validation results in `docs/validation-p0-web-visualization.md`.

## 2. Findings in the reviewed code

| Area | Current behavior and implementation consequence |
| --- | --- |
| `web/src/features/pipeline/Workspace.tsx` | The rail contains Prepare, Analyse, Review, Translate, Publish. Its P1 tile is disabled until saved analysis state exists. The section table repeats `[1,2,3,4,5]` in its colgroup, headings, and body. Add P0 deliberately in all three places; do not copy the P1 navigation gate. `phaseStatus()` falls through to publication behavior for an unknown phase and needs an explicit P0 case. |
| `web/src/router.tsx` | The phase route permits only `prepare`, `analyse`, `translate`, and `publish`. Add `preload`, including direct navigation and refresh. |
| `web/src/features/pipeline/Phase.tsx` | Usage is loaded only for analyse/translate. Metric filters and headings are hardcoded to P1 or P2-P5. The full analysis view and metrics are hidden before a saved plan. P0 needs an independent data/empty-state branch that keeps the page shell visible. |
| `web/src/features/pipeline/AnalyseContent.tsx` | Provides the reference layout: unit table, state/preview and Play buttons, paginated source/result preview, costs and tokens accordion, model assignments, usage tables, and recent execution. Its per-unit usage lookup uses `.find(pass_no === 1)`; do not copy that for P0 because one source scope can contain several model/profile groups. Its sequential next-P1-unit rule does not apply to independent P0 sources. |
| `web/src/features/pipeline/p1Cost.ts` | P1 and translation costs are stage-filtered and distinguish missing prices and incompatible pricing identities. Generalize the arithmetic without changing existing P1 output, rather than copy hardcoded P1 filtering into P0. |
| `bookpipe/application/web.py` | `section_summaries()` builds only P1-P5. `analysis_unit_preview()` requires `analysis_plan.json` and reads analysis receipts. P0 cannot use this endpoint as its data authority. |
| `bookpipe/application/workflow.py` | Query paths are read-only. Missing analysis plans produce no analysis units. Settings queries intentionally avoid the mutating `effective_settings()` path. Preserve that separation when projecting pending P0 targets. |
| `bookpipe/engine.py:analysis_plan()` | Planning currently combines calculation, optional provider counting, and writing `analysis_plan.json`. Extract a pure calculation seam for preview; a GET must not call this mutating function. Preserve its exact existing budgets, IDs, split rules, and saved-plan authority. |
| `bookpipe/source_sessions.py` | `scope_for()` writes inventory/source artifacts. Manager construction probes the installed executable/protocol; acquire/connect/ensure/reconcile are execution paths. None may be called by a page GET. `session_inventory()` is read-only but lists only already-created slots; it cannot show the initial pending book. |
| `bookpipe/usage.py` and `bookpipe/server/serialization.py` | Physical-attempt reporting already includes pass 0 and exposes per-attempt tokens, cost, and submission status. P0 uses source scope IDs, not P1 unit IDs. Groups may contain multiple reported models, represented by `multiple`; split actual model statistics at attempt level. |
| `web/src/api/schema.ts` | Pass numbers already permit zero, but the pipeline has no P0 projection. Zod currently drops several per-attempt accounting fields and profile effort information needed by this UI. Extend explicit schemas instead of using untyped casts. |
| `bookpipe/server/service.py` | Runtime table decoration recognizes a limited set of pass/progress boundaries and misses P0 preload events nested inside analyze/translate. Job start validation has no P0-only operation. |
| `bookpipe/runtime/protocol.py` | The metadata whitelist does not include source scope/slot/parent-consumer identifiers. Add only reviewed safe identifiers; do not publish raw session records. |
| `web/src/realtime/coordinator.tsx` | Workspace query invalidation has an explicit endpoint regex. Register the new detail/preview resources, or the P0 page will remain stale while the existing views update. |

Also inspect the affected CSS, route handling in both HTTP adapters, worker dispatch, command/application boundaries, debug-region registry, and existing web fixtures before editing. Existing product behavior, rather than a wholesale replacement of these modules, is the compatibility target.

## 3. Product decisions

### 3.1 Placement and navigation

The rail order becomes:

`Prepare -> Preload · P0 -> Analyse · P1 -> Review -> Translate -> Publish`

The workspace table order becomes:

`Section | Processing | P0 | P1 | P2 | P3 | P4 | P5`

Use `/work/workspaces/$workspaceId/preload` for details. The P0 tile opens whenever the workspace exists, regardless of whether P0, P1, or any model call has started. A draft that is not prepared yet opens the same page shell with a Prepare-required explanation; it does not request a prepared-workspace pipeline endpoint that must fail. For a prepared workspace, display source inventory and preview before execution, not a dead-end message telling the user to start P1 first.

Navigation is read-only and remains available during jobs, when archived, and during loss of the live connection. Offline navigation may show the last available snapshot with a freshness notice. Mutating controls retain normal busy/archive/connectivity restrictions. A missing workspace is still a normal not-found condition.

Clicking a section's P0 status opens this page with that chapter selected. Support a validated `chapter` query parameter and, where needed, an opaque `preloadTarget` parameter for a specific source/model target. Browser back/forward and direct reload restore selection. Unknown target IDs produce a bounded stale-selection notice and safe fallback; they are never interpreted as paths. Prevent the click from also opening the existing source drawer.

### 3.2 A tile is not a new scheduling barrier

Do not run P0 for the whole book before allowing any P1. Automatic execution remains lazy: create or recover P0 immediately before the first actual consumer that needs it. P0 can first run during analysis or during translation, including after Review when another scope/model is needed.

Do not change P1's vertical memory dependencies, P2-P5's horizontal dependencies, approval requirements, translation ordering, or publication readiness. Do not silently enable `source-session-v1` for an existing legacy workspace. Do not change the existing weighted overall progress percentage merely to add this tile.

The global workspace Run/Start keeps its existing workflow behavior and prepares missing P0 internally. The P0 page's targeted Play is an additional explicit P0-only action, not a replacement for global Start. There is no Preload-all-book command in this delivery.

### 3.3 P0 has inherited session configurations, not an independent model assignment

Keep the existing P1-P5 model selectors and configuration keys. Do not add an editable `pass_profiles['0']` or section `profiles['0']`. A P0 session must be compatible with the consumer's model, effort, and source-session contract.

Display the resolved configurations inherited from eligible P1-P5 consumers, including chapter overrides and consumer labels. One scope can therefore show multiple P0 targets. Two profile names with the same actual compatibility must not force duplicate P0; display the aliases/consumers together. Do not share across incompatible efforts, models, source maps, protocols, or workspaces.

### 3.4 Do not copy P1 destructive semantics

P0 has source and a readiness acknowledgement, not terms, observations, entity totals, or prose translation. Adapt those labels and data intentionally. Do not show a false empty glossary as P0 output.

Do not clone the Clear P1 panel, call analysis reset, or offer automatic P0 deletion/rebuild to clear a warning. Native-session archive/rebuild/purge remains the existing explicit maintenance workflow outside this UI delivery. Preserve an inspection notice for held/retired/missing state. This is the deliberate semantic exception to P1 interaction parity; destructive P0 controls need a separate design.

## 4. Data model and readiness semantics

### 4.1 Separate three identities

1. **Chapter/section** is the user-visible grouping from the frozen source manifest.
2. **Source scope** is the exact canonical source/map used by an existing analysis unit or translation chunk. P1 and translation may have different boundaries and must then have different scopes.
3. **Session target/slot** is the scope plus compatible execution configuration. A projected target exists before a native slot does. An actual slot additionally records runtime/protocol binding and generation.

Do not use chapter ID as a slot ID, conflate P1 unit IDs with P0 scope IDs, or set `slot_id = scope_id`. Use an opaque deterministic projected target ID, with nullable actual slot/generation fields. Once execution creates a slot, reconcile its association without duplicating the projected row.

Store remains checkpoint authority. Immutable source packages, existing session manifests, P0 receipts, selection-verification receipts, and physical-attempt evidence remain execution/evidence authorities. The new DTO is a read projection, not another writable registry. Do not create an unrelated UI database or persist a preview plan as execution truth.

The default P0 list mirrors P1. Its progress denominator counts analysis targets only. The workspace chapter P0 cell follows the same analysis preparation whenever the chapter has P1 targets; translate-only chapters use their applicable P0 targets. Accepted P0 is green, independent of model palette colors. Future translation targets do not make already loaded analysis source appear partially loaded. A separate translation list exposes the existing chunk targets, including translate-only sections, with its own authoritative summary. Lifetime P0 usage continues to include all targets. Changing views does not change source scopes, sessions, scheduling or prerequisites.

Private native state lives at `PROJECT/artifacts/<chapter>/codex-home/<scope[:12]>/<slot[:12]>/`, with full identities retained in manifests and collision checks. It must never be returned by these APIs or traversed by artifact/usage enumeration, including the narrowly authorized `home/auth.json` link to configured shared credentials and the private backup of an older auth copy. Read-only inventory does not migrate homes; execution or explicit offline maintenance performs migration under locks.

### 4.2 Inventory before execution

Implement an application-layer read model that combines:

- Frozen `book.json`, current processing choices, and read-only resolved settings/profile overrides.
- The existing saved analysis plan, when present, plus frozen translation chunks.
- A pure prospective analysis-plan calculation when no plan exists and the required counting/configuration facts are available locally.
- Existing P0 source packages, slot manifests, durable readiness records, and historical usage.

Extract the calculation from `analysis_plan()` and reuse it for preview and execution; the execution wrapper remains responsible for persistence. Do not alter packing, split offsets, synthetic block IDs, cached-token checks, source budgets, or the timing/meaning of P1 membership locking. Add an equivalence test between the prospective plan and the actual subsequently persisted plan.

For a configured Codex analysis path, counting is the current local UTF-8-byte bound, not an RPC. It can support exact prospective execution boundaries with the same resolved planning inputs. Do not instantiate a provider merely to obtain this counter. Preserve the current planner's profile/context resolution behavior; this task is not a planner redesign.

Where exact prospective analysis boundaries genuinely require unavailable provider information, list the chapters and their source preview immediately with `planning_state = unresolved`. Show known translation targets separately. Label the unresolved count explicitly and disable only that target's Play. Do not invent one analysis unit per chapter, perform remote tokenization, or hide the complete P0 page. This fallback must not replace the supported locally plannable Codex case.

A missing saved P1 plan must not be created by opening P0. No GET may write `analysis_plan.json`, `analysis_inputs`, source inventory, project UUID, source packages, P0 attempts, or native homes; freeze review inputs; lock membership; migrate settings; contact a provider; run Codex discovery; acquire an execution lease; or reconcile/revert a session.

### 4.3 Current requirements versus historical evidence

The current coverage set is the deduplicated set of P0 targets for eligible Codex consumers under the current source-session configuration. Count a target when unfinished work needs it or a matching accepted P0 already exists for that eligible source/configuration. Include known future consumers even when their semantic stage is waiting for P1/Review; source-only preload does not consume their future memory. A target does not disappear from completion counts merely because its consumer finishes: an unchanged book that reached 50/50 accepted P0 targets must remain 50/50 after P5 finishes.

A valid saved consumer result without a matching P0 does not by itself require creating or recreating P0; label that consumer as satisfied by its saved output and omit this otherwise unnecessary target from required counts. Preserve zero-inference checkpoint reuse. Matching accepted P0 for completed consumers remains in the current coverage view. Recorded sessions that become excluded, superseded, or incompatible remain in an explicitly labelled retained/history view; their physical usage remains in historical totals.

For `full` sections, consider eligible P1 and P2-P5 consumers. For `translate` sections, do not invent a P1 requirement, but include applicable translation P0 targets. For excluded sections, no current target is required. For non-Codex consumers or a legacy execution mode, P0 is not applicable, not failed or falsely complete. Mixed-provider books can have only some P0-eligible consumers.

Report separate counts for source scopes, current coverage targets, targets still needing execution, accepted P0 baselines, unresolved planning groups, and physical attempts. The accepted/required ratio uses the current coverage set consistently; execution eligibility is a separate field. Rail and table tooltips name their denominator. Do not confuse `50 chapters` with `50 P0 calls`.

### 4.4 Readiness is not provider-cache residency

A completed P0 means a durable, accepted source preload with the correct saved source/configuration and verified model-selection evidence. It does not promise cached tokens, a cache TTL, or zero input cost on the next request.

Read queries report the saved evidence and its observation time. Compare all compatibility inputs that can be resolved read-only. Keep runtime/protocol checks that require an executable in the worker, not the GET path. Expose a distinction such as `verification_scope = saved_evidence` and `native_check = on_use`; never fabricate a freshly verified resume. If known configuration/prompt/source inputs disagree, mark the slot incompatible/retained rather than current-ready. If compatibility cannot be established, report an explicit unknown state instead of guessing.

Use the R2 durable verification binding when determining whether recorded P0 is accepted. A generic completed metadata field or a `READY` string alone is insufficient. Missing/corrupt evidence must not produce a green completion mark. Read-only evidence validation must not call a helper that creates a missing verification receipt.

The following are separate axes:

| Axis | Meaning |
| --- | --- |
| P0 baseline state | Not started, preloading, accepted, interrupted/failed, unverifiable, or not applicable. |
| Session availability | Clean, consumer active, cleanup pending, recovery required, retired/incompatible, or native state unavailable. |
| Current relevance | Required by remaining work, retained for inspection, superseded, or excluded. |

An accepted P0 remains accepted while P1/P2 is running or a consumer's cleanup is pending. Show a separate session warning; do not turn a P3 failure into a P0 generation failure or reload P0 to make the tile green. Conversely, a failed P0 cannot be marked complete merely because a consumer job is labelled analyze/translate.

### 4.5 Compact status mapping

For the rail and per-section P0 cell, use existing visual state vocabulary where possible. Resolve active P0 generation first; then a relevant P0 failure/hold; then completed applicable targets; then partial; then pending. Preserve session warnings separately.

- `pending`: Known applicable target exists, no accepted P0 yet.
- `running`: Correlated P0 work is currently active, not just a consumer in the same session.
- `completed`: Every currently required known target has an accepted P0 and there are no unresolved planning groups; retained accepted P0 can be displayed independently.
- `partial`: Some required targets are accepted while others remain pending.
- `error` or `unknown`: Explicit failed/unverifiable P0; attach a safe reason and retain inspection.
- `not_applicable`: No applicable required P0, for example legacy mode, non-Codex-only work, exclusion, or fully saved consumers with no preload requirement.

Do not render `0/0` as a successful 100 percent run. Distinguish `Not required` from `Plan not resolved`. A retired historical failure does not override a new compatible accepted baseline. A session cleanup warning must not be hidden by a completed icon; expose it in the detail row and compact tooltip.

## 5. UI specification

### 5.1 Workspace rail and table

At desktop width, all six rail tiles fit using the existing visual treatment. At narrower widths, wrap or scroll consistently with existing responsive behavior; do not overlap labels or reduce text to illegibility. Preserve the existing rail debug identity.

The P0 column has the same width, alignment, compact symbol vocabulary, profile-color treatment, hover explanation, and accessible naming as P1-P5. Keep status symbols rather than adding repetitive status words inside every table cell. Mixed configurations must not be colored as one arbitrary model. Tooltip/accessible name includes accepted/required target counts, unresolved counts if present, actual recorded provenance, and any session warning.

Use a real button or link for the P0 cell's detail navigation. Existing row clicks, processing filters, section drawer, profile edits, keyboard navigation, and excluded-row dimming must continue to work. Keep the model-assignment grids at P1-P5; adding P0 to the pass table must not create a sixth independent model selector.

### 5.2 P0 detail page, matching P1

Use the existing phase-page frame and shared layout/style tokens. Prefer a small shared component extraction with stage-specific data adapters over a second divergent copy of `AnalyseContent`.

| Existing P1 element | P0 counterpart |
| --- | --- |
| Back to Workspace | Same navigation and placement. |
| Analyse heading and status | `Preload · P0`; baseline status plus a separate session-availability notice when needed. |
| Whole-book analysis summary | Source preload summary with chapters/scopes/session targets, accepted counts, and explicit unresolved planning. No entity count. |
| Input / Cache / Reason / Output / Cost cards | Same five cards, positions, sizing, number formatting, loading/partial behavior, and tooltip treatment; pass 0 only. |
| Analysis units table | Default `Analysis units`: the same analysis units and order as P1, with unit ID, word/UTF-8 byte counts, state/preview and targeted Play. Keep future translation fragments in a separate selectable list, never interleaved with analysis. |
| P1 preview | `P0 preview`; the selected scope's exact source and saved preload acknowledgement/metadata. |
| P1 costs and tokens accordion | `P0 costs and tokens`, including the breakdowns in section 6. |
| Recent execution | Same card with P0-correlated activity and useful consumer/session context. |
| Run unit confirmation | `Run this P0 unit?`, resolved profile/model/effort, exact source scope, consumer references, and token-use warning. |

Keep the five metric cards, left unit panel, right preview panel, and cost accordion visible before execution. No recorded usage is displayed as `—` with `Not run yet`, matching P1's missing-measurement convention. A measured zero remains zero. Do not hide metrics behind P1's `analysisEmpty` branch.

For the common case of one target per chapter, the page should resemble the supplied P1 screenshot directly. For multiple scopes or models, add compact secondary identifiers and grouped rows, not an entirely different information architecture. Use shortened IDs for display with accessible full identification. Selection must be stable under polling and not jump to the first row on every update.

Preserve the preview stack's independent scrolling/sticky behavior and responsive stacking. Use the existing source-language display instead of always labelling source as English. Keep the same panel borders, typography, spacing, neutral backgrounds, and model swatches. Do not add a new theme or dependency.

### 5.3 Source preview before and after P0

Before execution, show canonical source with the explicit label `Source to preload — P0 has not run`. After acceptance, show the recorded immutable source for that scope, the actual P0 acknowledgement, saved model/effort evidence, generation, and accepted/last-verified timestamps where available.

Acknowledge readiness once per session, not once per paragraph. Do not fabricate per-block findings. Preserve the two-pane P1-like preview area: source text and compact session/readiness information when available; a source-only or clearly empty readiness side before execution.

Reuse bounded five-block pagination and long-block truncation notices. Display source as escaped text using existing rendering conventions. Do not expose raw prompts, private instruction text, credentials, absolute home/work/SQLite paths, arbitrary filesystem links, or full native rollout files. Opaque thread/slot IDs may appear in an optional diagnostics subsection, not dominate the main table.

If the source binding is corrupt, show an unavailable/error state for the affected target and retain historical diagnostics. Never replace its source with another chapter's content. Preview selection and GET pagination must not create native state.

## 6. Accounting and model/chapter breakdowns

### 6.1 One physical ledger

Use the existing physical-attempt accounting as the source of truth. Filter for numeric `pass_no === 0` explicitly. Do not infer P0 usage from P1, source character length, consumer totals, native thread cumulative totals, or profile defaults.

Count a physical P0 attempt once even when the baseline serves P1-P5, multiple aliases, or multiple views. Include submitted failed attempts and retries in historical usage. Resume, accepted-result reuse, reconciliation, and revert are not extra P0 inference calls. Keep known/unknown submission status and measured/null values intact.

Use all P0 groups within a scope. Do not use `.find()` to pick the first model group. For model-level reporting, group physical attempts by their actual reported model and provider, retaining requested-model/profile provenance. A group-level `reported_model = multiple` is not a model name. Split it using per-attempt records. Missing applied-model evidence is labelled unverified/requested-only, never silently presented as verified applied identity.

Extend the frontend per-attempt schema to retain the relevant fields already exposed by the backend usage DTO: provider-contacted status, usage status, input/cache/write/output/reasoning/total tokens, cost, and elapsed time. If slot/generation association is needed, add a safe explicit identifier to the DTO or a backend join; do not guess from display labels or expose artifact paths. Use a stable physical-record identity within its scope/task, not an assumed globally unique short attempt label.

### 6.2 Required detail tables

Inside the P0 costs and tokens accordion, retain the P1 visual/table style and provide:

1. **Current inherited assignments:** default consumer assignments and chapter overrides, resolved model/effort/provider, and which consumers share each P0 target. Read-only.
2. **Recorded P0 summary:** used profiles/models, unique source scopes and baselines, submitted/unknown/failed attempt counts, Input/Cache/Reason/Output, and estimated cost.
3. **Usage by model/profile:** actual applied model where verified, requested identity where not, profile provenance, calls, tokens, elapsed time, cost, and coverage/partial flags. Preserve effort distinctions in session details and in grouped rows when evidence supports them.
4. **Usage by chapter:** one aggregate row per chapter, summing its physical P0 attempts once, with current readiness counts kept distinct from historical spend.
5. **Usage by source unit/session:** selected or expandable scope/model/generation rows with drilldown into their physical attempts and retained historical generations.

Top metrics and historical model/chapter/unit tables use the same workspace-wide P0 ledger and must reconcile numerically. Changing the selected preview row must not silently change top-level totals. Any optional filter is labelled and applied consistently. A known history row whose chapter can no longer be resolved belongs in a visible `Unassigned historical usage` bucket rather than disappearing.

Current readiness excludes retired, excluded, or superseded targets from its denominator, but retains accepted targets after their consumers finish; lifetime usage does not erase historical cost. Keep those populations explicitly labelled. A source shared by multiple consumers is not duplicated in chapter totals.

### 6.3 Arithmetic and wording

Reuse or extract the existing cost-formatting/stage-cost helper. Preserve four-decimal and very-small-positive formatting. Sum only compatible currencies and estimate types; otherwise show separate groups or `—` with an explanation. A known partial sum is labelled partial, including in detail tables. Unknown pricing or usage is not `$0.0000`.

Cache is a subset of input and reasoning is a subset of output under the existing accounting contract. Never sum the four displayed cards to calculate total tokens. Include cache writes where the existing cost calculation requires them, even if there is no extra headline card. Do not recalculate costs in the browser using new hardcoded rates.

Use the existing API-equivalent estimate explanation for Codex, not an assertion of an actual subscription charge. Do not promise cache hits or retry when cached tokens are zero. Expose recorded cache coverage only when the corresponding input measurements are available; omit a percentage rather than divide unknown values or mix incomparable populations.

## 7. Application and API contracts

Implement the read model in the application layer, with explicit safe server DTOs and Zod schemas. The following resource names are the target contract; follow the existing service/route architecture rather than executing providers in HTTP handlers.

### 7.1 Read endpoints

`GET /api/workspaces/{workspace_id}/source-preload`

Return a versioned, bounded inventory with these concepts:

- Workspace ID, execution-mode applicability, preparation/planning status, snapshot/revision information, and safe diagnostic reasons.
- Summary of current source scopes/targets, accepted/pending/active/failed/unverifiable/not-applicable counts, unresolved planning groups, and retained history.
- Chapter groups in source order and target rows with stable projected IDs, source identity, consumer references, inherited configurations, baseline state, independent session state, and optional actual slot/generation.
- Accepted/last-verified timestamps, evidence freshness, safe model-selection status, and explicit `can_run`/reason for each target.
- A separately defined revision for executable target intent; usage ticks alone must not invalidate a confirmation dialog.

Do not include full chapter text or raw attempt logs in the inventory. Read source pages only when selected. Bound/paginate retained history as necessary; return complete summary counts independently of any row page.

`GET /api/workspaces/{workspace_id}/source-preload/targets/{target_id}/preview?page=0`

Return a bounded source page, source kind (`planned` or `recorded`), readiness availability, safe session details, truncation flag, and next page. Resolve targets within the workspace inventory server-side. Validate page and target identifiers like the existing preview APIs. Apply the same explicit query validation in ASGI and the compatibility HTTP adapter.

Extend the full pipeline response with a compact `source_preload` summary and per-section `passes['0']`, derived from the same read model. Do not independently recompute contradictory counts in frontend code. Preserve lightweight work-list `pipeline_summary` behavior; do not attach all source text, slots, or usage scans to every work-card request.

Use the existing `/usage` endpoint with an extended typed frontend projection for accounting; avoid introducing a second incompatible pricing ledger. If a backend grouping helper is needed, it must aggregate the same evidence values and be tested against the existing report.

### 7.2 Explicit targeted P0-only command

Implement the row Play interaction using the existing asynchronous job/supervisor infrastructure:

```json
{
  "operation": "preload",
  "preload_target_id": "opaque-current-target-id",
  "expected_preload_revision": "read-model-intent-revision",
  "request_key": "existing-idempotency-key-format"
}
```

Post to the existing workspace jobs endpoint. Add a typed application command and validated runtime specification; update worker dispatch and serialization deliberately. Do not overload `analyze`, send `pass_no=0` through targeted translation, or call `Runner.run(0)` with P1-P5 schemas.

The worker resolves and revalidates the target under the normal workspace/session ownership rules, including current source, mode, profile, effort, prompt/contract, generation, archive status, and changed processing choices. A stale/unknown/mismatched intent is rejected before inference. A GET-derived target ID is not authority to supply arbitrary source, paths, model names, or instructions.

Reuse the persistent-session manager's P0 creation/recovery and R2 acceptance gate. Extract a narrow P0-only application entry point if necessary. Set the appropriate consumer context for provenance without fabricating a completed consumer. Reuse an accepted compatible P0 without a new turn. An unfinished existing attempt goes through the same conservative recovery checks. Held/rejected/ambiguous evidence is not automatically rebuilt or retried. The operation finishes at the accepted P0 checkpoint; it does not submit P1-P5, write analysis memory, approve review, or publish output.

For a new target, allow at most one new P0 inference attempt per explicit job under existing no-blind-retry policy. Do not run every model or every chapter just because a grouped chapter row was selected; the confirmation identifies one concrete target. Different P0 chapters have no artificial earlier-P0 completion dependency.

A standalone preload checks the complete P0 request bound. When a future consumer's dynamic memory/output is not yet available, do not fabricate it to claim a full future-pass fit. Explain that subsequent consumer preflight still applies. Preserve the existing stronger full-request preflight in the normal automatic consumer path.

Reuse P1's confirmation, request-key persistence, unknown-outcome receipt lookup, click latch, offline/busy/archive guards, and normal Stop control. A lost HTTP response or double click must not submit another turn. Browser navigation away never cancels a job implicitly. The global Action component must recognize an active preload job sufficiently to show Stop, without changing its normal workflow selection.

There is no new global P0 Run-all, retry-until-success, independent model configuration, or destructive Clear P0 action.

## 8. Realtime, privacy, and performance

P0 runs inside ordinary analyze/translate jobs as well as the explicit preload operation. Derive the active stage from correlated progress and saved evidence, not solely `active_job.operation`. Correct workspace P0/P1 cell spinners so P0 is active while source loading occurs, then the consumer is active after P0 completes. Do not briefly count an unaccepted acknowledgement as completed.

Preserve and enrich the existing source-preload lifecycle events with safe correlation metadata: numeric pass 0, chapter, scope, actual slot when known, parent consumer pass, and unit/chunk reference as applicable. Add only these opaque identifiers to `PROGRESS_FIELDS`; raw source, credentials, session paths, full prompts, and arbitrary errors stay private. Do not report post-P1 cleanup as P0 generation.

Register `/source-preload` inventory and preview queries with the existing workspace-scoped realtime invalidation. Use existing batching/throttling, event sequencing, duplicate detection, reconnect snapshot, and terminal refresh. Do not install another SSE connection per panel or introduce rapid independent polling. Old terminal events from another job/workspace must not light the current P0 row. Audit zero-valued pass numbers for truthiness bugs.

During active P0, displayed token updates are a correlated provisional snapshot. Do not add cumulative updates repeatedly, mix resumed thread totals with the new attempt, or add live values to a persisted copy of the same attempt. After completion, reconcile against persisted usage. Page reload, reconnect, and selecting a different row converge on the same totals.

Keep source loading paginated and avoid rescanning the entire book and every native rollout on each token event. Use safe read-only snapshot caching keyed by source/config/plan/evidence revisions when beneficial; never use stale cached data to authorize execution. Corrupt evidence in one target must produce a localized safe error rather than crash the complete workspace view or fabricate zero usage. Keep summary performance comparable to the current UI.

Retain stable existing UI debug IDs. Register distinct P0 unit, preview, usage, and confirmation regions in `web/src/debug/regions.ts` and the corresponding debug documentation. Shared phase-region IDs may remain shared; do not relabel existing P1 regions as P0 or create duplicate keys within one view.

## 9. Implementation sequence

### Step 1: Baseline and seams

Confirm branch/worktree/head, read applicable instructions, and inspect existing route/UI fixtures. Identify all frontend hardcoded P1 phase gates, stage-number arrays, runtime event filters, DTO whitelist boundaries, and P1-specific data actions. Record the implementation baseline. Preserve the recovered-session regression suite.

### Step 2: Read-only planning and inventory

Extract the pure analysis-plan calculation and source-target/configuration projection, with saved-plan precedence. Implement the application read model and safe readiness evidence inspection. Separate projected targets from actual runtime slots. Test initial inventory, shared scopes, multiple profiles/efforts, incompatible generations, unresolved planning, and zero side effects before connecting the frontend.

### Step 3: Public contracts

Add safe inventory/preview DTOs, routes for both HTTP adapters, compact pipeline P0 summary/cells, and frontend schemas. Extend the frontend usage-attempt fields and inherited-effort projection. Update `docs/server-api.md` and `docs/web/contract-map.md`. Test privacy, input validation, and compatibility with existing P1-P5 responses.

### Step 4: Accounting helpers

Extract stage-filtered cost/token helpers without changing P1/P2-P5 results. Add attempt-based P0 grouping by model/profile, chapter, and scope/slot. Build fixtures that deliberately contain more than one P0 group per scope, shared consumers, failed/unknown attempts, and superseded generations. Verify reconciliation of every subtotal.

### Step 5: Workspace and P0 page

Add the six-tile rail, P0 status column, route, early-access shell, and P1-matching detail layout. Reuse shared styling/components. Implement stable target selection, source preview, expanded usage details, unavailable/not-applicable states, and debug IDs. Do not copy P1's membership gate or Clear P1 action.

### Step 6: Targeted P0 execution

Add the narrow preload command and supervisor/worker dispatch. Revalidate the intent and invoke existing P0/session safety logic, stopping before any semantic consumer. Wire the Play confirmation and receipt recovery. Preserve global Start and Stop behavior. No cloud calls are needed to test this with existing native loopback fixtures.

### Step 7: Live behavior and responsive verification

Wire source-preload events, safe metadata whitelist, runtime cell decoration, and query invalidation. Test nested P0 inside analyze/translate, standalone P0, reconnect, stale events, cancellation, and provisional usage reconciliation. Compare P0 and P1 layouts at the same viewport and with debug labels both on and off.

### Step 8: Regression and handoff

Run the final complete offline regression on the final implementation, not only a targeted subset after the last fix. Update web contract/debug/difference/verification documents where affected. Add the validation report with exact commands, results, skips, resource limits, remaining limitations, and confirmation of zero cloud inference/production operations. Commit only intended implementation/tests/docs on this branch; do not merge or deploy.

## 10. Acceptance tests

All items below are acceptance requirements, not optional examples.

| ID | Required evidence |
| --- | --- |
| UI-01 | Rail order is Prepare, P0, P1, Review, Translate, Publish; section columns are Section, Processing, P0-P5. |
| UI-02 | P0 tile and direct route open on a prepared workspace with no attempts, no P0 manifest, and no saved analysis plan. The page shows source inventory, five metric cards, preview, and usage panels. |
| UI-03 | A draft can open a Prepare-required P0 shell; archived, busy, and offline states do not disable read navigation. |
| UI-04 | Clicking P0 for a chapter selects that chapter without opening the source drawer. Direct selection, back/forward, reload, and polling retain selection. |
| UI-05 | P0 uses the existing P1 frame/layout and source-language labels. No entity, glossary, translation, or Clear P1 semantics leak into P0. |
| UI-06 | Five headline metrics and all cost tables show only pass 0. No attempts, measured zero, unknown, and partial values are distinguishable. |
| UI-07 | Desktop six-tile/eight-column layout, a narrower desktop/tablet layout, and a mobile layout have no overlaps or inaccessible controls. Source preview remains usable. |
| UI-08 | Keyboard/screen-reader names distinguish preview from Run, focus survives refresh, and existing debug IDs remain stable with new P0 regions registered. |
| DATA-01 | Prospective Codex analysis boundaries/IDs equal the subsequently persisted execution plan, including split long blocks and Unicode. A saved plan always takes precedence. |
| DATA-02 | Unresolved nonlocal planning shows source chapters immediately, explicit unknown counts, and no fake exact scope or premature 100 percent completion. |
| DATA-03 | Different P1/chunk source ranges remain separate; identical scope/configuration shared by several consumers produces one target and one physical P0 accounting record. |
| DATA-04 | Different models/efforts/contracts/generations and chapter overrides are represented correctly. Equivalent profile aliases do not create duplicate native targets. |
| DATA-05 | Full, translate-only, excluded, legacy, non-Codex, mixed-provider, and fully saved-consumer cases produce correct applicability without triggering migration or preload. Accepted P0 coverage remains complete after P5; it does not collapse to 0/0. |
| DATA-06 | Accepted P0 with an active/failed consumer or pending cleanup remains accepted with a session warning. Failed/held P0 is not marked accepted. |
| DATA-07 | Corrupt/missing/foreign source or selection evidence gives a localized unknown/error, not green completion or another chapter's source. |
| SAFE-01 | Repeat all P0/pipeline/preview GETs on a pristine prepared workspace while snapshotting application files and Store state: no plan, inputs, UUID, inventory, attempts, runtime, or receipts are created or changed. |
| SAFE-02 | The same reads work with Codex absent and provider/manager construction, subprocess, tokenization RPC, inference, resume, and revert configured to fail if called. |
| SAFE-03 | Invalid page/target/path traversal, foreign workspace IDs, symlinked evidence, and unsafe raw fields cannot escape the authorized workspace or leak private native state. |
| USAGE-01 | One P0 shared by five consumers contributes once to top/model/chapter/unit totals; resume, cleanup, and accepted reuse add no calls. |
| USAGE-02 | Multiple P0 model/profile groups for one scope, and different reported models within one group, all appear and sum correctly; `multiple` is not used as a model identity. |
| USAGE-03 | Failed attempts, unsubmitted attempts, unknown submission, missing pricing, mixed currencies/types, cache writes, and partial coverage follow the existing ledger semantics. |
| USAGE-04 | Retired/rebuilt/excluded/history records stay in lifetime spend while not inflating current readiness. Unassigned history remains visible. |
| USAGE-05 | Chapter/model/unit subtotals reconcile with headline P0 totals; reasoning/cache subsets are never double-added and historical model usage is not relabelled after a profile change. |
| RUN-01 | Confirming one P0 Play submits at most one missing P0; P1-P5, analysis memory/receipts, review, and publication remain untouched. |
| RUN-02 | Reusing an accepted P0, repeating a request key, double click, lost POST response, and reload produce no duplicate model turn. |
| RUN-03 | Stale target revision, changed assignment/source/mode, archive/busy state, incompatible generation, and held/ambiguous evidence refuse unsafe execution. |
| RUN-04 | P0 can be explicitly preloaded for a later chapter without running earlier P1/P0 units, but unresolved targets remain disabled with a reason. |
| RUN-05 | The existing automatic Start still prepares P0 lazily, respects full consumer preflight, P1 memory order and Review, and never creates a whole-book P0 barrier. |
| RUN-06 | Stop and restart preserve/recover evidence; R1/R2 and all pre-existing persistent-session regression assertions still pass. |
| LIVE-01 | A P0 nested inside analyze/translate lights P0 only during preload and then the proper consumer; a consumer cleanup event never masquerades as P0 generation. |
| LIVE-02 | Safe scope/slot/pass-0 metadata survives worker/public serialization. Reconnect, duplicate/out-of-order events, and another workspace's events cannot corrupt current status. |
| LIVE-03 | Repeated cumulative live usage updates, terminal persistence, and resume converge on one ledger total without double counting. |
| COMPAT-01 | Existing P1 detail, targeted P1, Clear P1, P2-P5 previews/usage, model assignment, source drawer, Reader, Review, publishing, and work-list performance remain functional. |
| COMPAT-02 | Both HTTP adapters, TypeScript schema parsing, production frontend build, browser tests, and debug-region verification cover the new contracts. |

Use synthetic fixtures and the existing native app-server loopback provider to validate P0 commands without cloud inference. Fake frontend counters alone are insufficient for accounting or command acceptance tests. Visual screenshots should be taken from the implemented fixture-backed page, not generated mockups.

## 11. Validation commands and environment

Use the project's locked dependencies and current scripts. From the dedicated worktree, the validation set includes:

```bash
uv run python -m pytest -q
node --test --test-concurrency=1 tests/*.cjs
uv run python -m compileall -q bookpipe translate.py
uv lock --check
git diff --check

cd web
npm ci
npm run build
npm run test:unit
npm run test:browser
npm run test:visual
```

Run relevant lint checks using the existing `npm run lint` script and report baseline issues separately if present. Do not install a different framework or replace the build toolchain.

These commands are a validation checklist, not permission to run unbounded concurrent workloads on the production host. Use separate scratch directories, application service state, credentials-free native homes, loopback fixture ports, and disk-backed temporary space. Run backend/native validation under the established 256 MiB memory / 50 percent CPU scope when compatible with the existing harness. Browser/build workloads need their own adequately sized bounded environment; do not cause OOM by blindly applying the native cap or by removing isolation. Use another test environment if necessary. Record the actual limits and results.

Never start the production listener, use its workspace roots, copy its active native homes into writable tests, or launch model inference against the user's account. Explicitly distinguish native loopback tests from cloud/live tests. If a required local tool or browser is absent, record the test as unrun rather than claim success. Do not substitute a focused subset for the final full regression without clearly reporting the limitation.

## 12. Definition of done and Codex handoff

Delivery is complete when P0 is visible in both requested workspace locations; the P1-matching P0 page works before the first call; source/model/session identity and all requested usage breakdowns are correct; targeted P0-only Play and ordinary lazy Start are safe; realtime progress and restart behavior remain correct; and the final validation report covers every acceptance group above.

The new UI must not require migration or any action on the production book. Preserve the existing feature branch and durable session recovery architecture. No merge or deployment is part of this task.

Suggested Codex instruction:

```text
Read AGENTS.md and docs/prd-p0-web-visualization.md, then implement the full
specified P0 workspace UI and P1-matching P0 detail page on the existing
work/p0-p5-persistent-sessions branch in its dedicated worktree.

Preserve the P0-P5 architecture and the R1/R2 recovery fixes. Keep the P0
page accessible before execution and before a saved P1 plan exists.
Implement source/model-aware read-only projections, correct P0 accounting,
P0-only targeted Play, and the existing lazy Start behavior. Do not turn P0
into a global scheduling barrier or add independent P0 model settings.

Use isolated synthetic/native-loopback tests only. Do not touch production
main, running services, active translation workspaces, or cloud inference.
Run the required final regressions and write the validation report with
actual results, unrun checks, and resource limits. Commit the implementation
on this branch. Do not merge or deploy.
```
