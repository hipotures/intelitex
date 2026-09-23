# Original v33 visual contract

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

Canonical file: `../assets/intelitex_workspace_mockup_v33.html`.
Run verify-mockup.py first. It must remain exactly 143,061 bytes with SHA-256
c50fe95eb761ae332171759cfb2ba95fcc996baaa98d9a1471a570f6f54a26a5.
Render the original separately using the same browser/fonts/viewport as production.
Never reconstruct v33 from v32 or modify the reference to make tests pass.

Detailed original geometry, controls, screen flows, breakpoints and source-function
traceability are preserved in implementation-contract.md sections 2,3,5-13,18,20,22.
Read only those sections for a targeted visual task; read the whole brief for a
full UI implementation and apply the provenance/decision register first.

## Mandatory rendered structure

- 64px sticky topbar: IX gradient mark/Intelitex, centered Work/Reader segmented
  switch, theme/settings icons. No permanent navigation sidebar.
- Work: active-workspace **rows in one bordered list**, then cover-card Library.
  Not v32's active-card grid. Real count badges and independent per-row action.
- Workspace: header plus five equal phase tiles Prepare/Analyse/Review/Translate/
  Publish. The Analyse tile opens P1 details while it runs or after a plan or attempt exists;
  the primary workspace action starts a new P1. Other available tiles navigate.
  The original has no P1 prefix in rail titles; the approved production clarification
  labels Analyse as whole-book P1 so it is visible independently of section P1 cells.
- Section table columns exactly Section,Processing,P1,P2,P3,P4,P5. Compact F/T/E,
  model-colored state icons with accessible tooltips. No redundant textual status
  per cell, Type column, Models column or right permanent progress sidebar.
- Excluded rows are visibly gray/dimmed across the whole row, yet inspectable.
- Right section drawer: source preview, Processing, Content type (metadata only),
  five optional profile overrides. Acknowledged autosave, no Save Section invention.
- Pipeline models and Live activity are collapsed accordions **below** the table.
- Full-page Review has search/status/category filters, independent scrolling term
  list (~40%) and detail (~60%), pinned Previous/Review & next/Next footer. Not a modal
  or the three-column reconstructed Review.
- Four phase-detail pages use four metrics and a narrower two-column diagnostics
  layout; review has its own layout. No invented phase-step navigation buttons.
- Archive drawer/confirmation, Settings Paths/Models/Interface modal and bounded
  Reader shell preserve the reference's distinct visual purpose.

## Key values

Dark bg #0d0f12, panel #14181e, text #edf1f5, border #282f39, accent #4f9dea.
Light bg #f5f6f8, panel #fff, text #17202a, border #dce2e8, accent #2f7ed1.
Use the full CSS token set, soft alphas and radius/shadow definitions from v33.
Main width 1480px/padding 30px 34px 70px; Workspace 1520px; Review 1600px;
phase details 1180px; radius 14px/9px; h1 27px; Review h1 24px.
Drawer min(520px,94vw); Settings min(850px,96vw)/max-height 88vh.
Reader prose uses Georgia, not UI typography. Use available system fonts; no external CDN.

Original breakpoints: 1020,900,820,720,560px. Match their actual CSS, including Review
one-column below 820px, top term list max-height 240px, detail min-height 520px,
library two columns below 720px, and all pass columns retained. Tables may scroll
locally; no page-level horizontal overflow or clipped footer controls.

Missing-cover initials use up to three title words after leading articles where
appropriate (The Glass Meridian -> GM). Support Unicode without inventing cover art.
Missing explicit section title uses source sentence/text with italic dim fallback.
Don't convert absent word counts/confidence/usage into invented numeric values.

## Never copy simulation behavior

localStorage project state; seeded defaultState books/models; single activeTimer;
tickPipeline/prepare timers; mockTokenStats; unconditional model Test success;
estimated EPUB size; fake checks/logs; bare boolean glossary approval;
Reset mock data; arbitrary installation-path editing; fake publication paths.
Unused helper functions in the HTML do not establish an extra visible table column.

## What is not resolved by appearance

F/T/E invalidation after starting P1, the meaning of weighted progress, publication
Stop availability, draft-workspace persistence and complete Reader/profile editors
are not solved by copying click handlers. D02–D05 are explicitly approved in the
decision register. D05's later issue #1 refinement requires a one-time setup modal
before Save and permits multiple workspaces per source, even though immutable v33
and the historical handoff show an earlier card-click flow. D06–D07 retain their
bounded Reader/Settings scope. No success-shaped placeholder for absent backend
behavior. Any intentional production departure must be documented.
