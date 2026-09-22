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
  and setup preflight read the selected source without a model call. Add to workspace
  opens one-time setup; Save persists a draft with label, language pair and P1–P5
  profiles. Cancel leaves no draft. A source stays in Library and may produce several
  workspaces. Draft archive/restore preserves its setup; Prepare performs the real
  supervised import and stops before P1. The setup modal is a product addition absent
  from v33. The language selector currently offers the supported English→Polish pair;
  broader translation and Review language support is a separate issue item.
- Confirm glossary commits approval against the latest complete Review revision.
  Subsequent terminology edits require renewed approval and retain prior output.
- P2–P4 retained receipts display amber retained/unverified state where the existing
  checkpoint query cannot establish current input fingerprints. They are not fabricated
  green completion. Checked P5 counts determine translation completion.
- Model swatches use persistent profile indices and historical provenance rather than
  the mock's fixed sample model identities. Unknown identity/usage remains unknown.
- Connection state, pending mutations, revision conflict/reapply and safe errors are
  visible. Accessible native buttons/dialogs replace clickable mock divs.
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
