# Intentional differences from original v33

The original asset remains immutable. Production preserves its visual tokens, Work
layout, five-phase rail, seven-column sections table, drawers, Review panels, themes
and responsive breakpoints. The following differences carry real application semantics.

- No seeded books, fake progress/tokens, publication timers, simulated model tests,
  fake validation, generated cover artwork or localStorage pipeline state.
- Sources without metadata show explicit absence. Covers use title initials. Paths
  are summarized as server-configured rather than exposing private filesystem paths.
  Extracted cover assets and the rebuildable SQLite catalog cache remain in their
  separate issue #1 item; this setup change does not claim to provide them.
- Library loads bounded source pages on scroll, and its dedicated Refresh control
  restarts discovery without shifting cards. Packed EPUBs are indexed alongside
  folders. The redundant source-directory label and unknown “— words” placeholder
  under covers are omitted.
- F/T/E follows approved D02: Full membership freezes after the first persisted P1
  attempt; idle T↔E retains and validates dormant evidence. Disabled controls explain
  the membership restriction. Content type is independent.
- Weighted workflow progress is calculated in Python using required P1/P5 counts,
  with null for unknown denominators. Counts may decrease after real invalidation.
- Publishing is disabled while the worker automatically publishes; terminal failure
  offers publish-only retry. EPUB bytes, size and checks are validated by the backend.
- Library card selection opens source details without creating a workspace. Inspect
  and setup preflight read the selected source without a model call. Inspect shows
  bounded source excerpts and explicitly distinguishes sampled words, reading-order
  files and heuristic language detection from whole-book facts. Add to workspace
  opens one-time setup; Save persists a draft with label, language pair and P1–P5
  profiles. Cancel leaves no draft. A source stays in Library and may produce several
  workspaces. Draft archive/restore preserves its setup; Prepare performs the real
  supervised import and stops before P1. Web Prepare uses a local, explicitly
  estimated count of one token per four characters and never contacts a model;
  P1 does the provider-specific recount. An existing draft exposes revision-checked
  P1–P5 profile selectors before Prepare. The setup modal and draft model panel are product additions absent
  from v33. The language selector currently offers the supported English→Polish pair;
  broader translation and Review language support is a separate issue item.
- Prepared workspaces add `Rebuild` in the Prepare detail page. It versions
  the old plan in the same workspace and resets section-specific choices only before
  any persisted pipeline work. The original v33 has no in-place rebuild control.
- Prepare places a compact Source structure table beside an on-demand first-1-KiB
  source preview. Source metadata and checks follow the preview; v33 has no
  row-driven Prepare preview. Processing is edited as F/T/E beside the preview and
  shown read-only on the workspace overview. P1 membership appears as
  lowercase `in`, with full meanings available through labels/tooltips. It does not
  claim that P1 has completed.
- The Analyse phase explicitly says `P1` and `whole book`; section P1 cells still
  report only real per-section unit evidence rather than fabricated progress. A ready
  Analyse phase is neutral and says Ready; only an active `analyze` job says Running.
- Analyse adds a fifth summary metric and a Cost column after Output. The metric
  gently pulses only while P1 runs; incomplete usage is described in tooltips instead
  of appending `(partial)` to the visible totals. The Cost tooltip explains saved
  versus current catalog rates and the API-equivalent nature of Codex estimates.
- Analyse presents Generated data as a compact strip above the full-width Analysis
  units table, with Artifacts below it. The table fills available width and scrolls
  internally only when its content needs more space.
- Before P1 has a saved plan, Analyse shows one clear empty state and a return action
  rather than empty token and artifact panels. Saved P1 can be cleared through a
  confirmed, revision-controlled action when no dependent work exists; historical
  evidence is preserved in the workspace's versioned history. V33 has no reset UI.
- Reload first shows a stable connection shell and phase loading card while backend
  state arrives. Neither screen uses mock pipeline data.
- Confirm glossary commits approval against the latest complete Review revision.
  Subsequent terminology edits require renewed approval and retain prior output.
- P2–P4 retained receipts display amber retained/unverified state where the existing
  checkpoint query cannot establish current input fingerprints. They are not fabricated
  green completion. Checked P5 counts determine translation completion.
- Model swatches use persistent profile indices and historical provenance rather than
  the mock's fixed sample model identities. Unknown identity/usage remains unknown.
- Connection state, pending mutations, revision conflict/reapply and safe errors are
  visible. F/T/E displays a distinct local saving state immediately, then accepts
  the backend revision or rolls back on conflict. Accessible native buttons/dialogs
  replace clickable mock divs.
- Source preview is escaped, bounded and paginated. Diagnostic tables scroll internally
  on narrow screens; no extra section-table columns were introduced.
- Reader uses actual verified translated chapters, canonical Unicode markers, deletion,
  contextual information and saved location instead of the mock's placeholder prose.
  Standalone Reader retains its existing gesture/preferences behavior. The new shell
  supplies explicit selection actions; no new annotation types or AI tools were added.
- Settings exposes supported read-only profile definitions/scope and UI preferences.
  Profile creation/editing, credentials, arbitrary path changes and model diagnostics
  remain unavailable because no production contract supports them. Test never generates.
  UI reset affects only scoped presentation preferences, never projects or request receipts.

The stable Work fixtures receive unmasked automated pixel comparison. The Library's
requested Refresh control and removed text create small intentional differences from
v33 within the existing 1% budget. Other screens
are compared manually against original captures with different real fixture data; their
screenshots are not claimed to be pixel-identical. See the verification report for results.
