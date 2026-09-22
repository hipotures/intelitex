# Authority, provenance, and unresolved decisions

## Verified baseline

Inspected repository: `hipotures/intelitex`, main commit `2bb9aa2df178355502a7529e460fca12804ef377`, titled
`Complete single-server workspace workflow API`. Re-read actual HEAD before work.
The live code is newer than the `1f85da8` baseline in the supplied implementation
prompt. Do not resurrect that older route inventory or undo newer work.

The initial skill is documentation/tooling only. It does not install the frontend,
change workflow rules, certify backend correctness, or authorize a full rewrite.

## Four distinct evidence classes

| Class | Meaning | Required treatment |
| --- | --- | --- |
| B: verified code | Existing application/HTTP behavior at the recorded SHA | Bind exact contracts; test, do not assume bug-free |
| R: reference | Original v33 DOM/CSS/rendered controls | Preserve visual structure, not simulated execution |
| P: specified target | Prior implementation-brief decisions beyond the mock | Label as target; verify scope and backend support |
| U: unresolved | Conflicting proposals or a material gap without accepted semantics | Do not guess or silently expand scope |

The original HTML is in `../assets/intelitex_workspace_mockup_v33.html`.
SHA-256: `c50fe95eb761ae332171759cfb2ba95fcc996baaa98d9a1471a570f6f54a26a5`.
It contains Polish translation examples and source excerpts; preserve those bytes.
New instructions, code and documentation are English.

`implementation-contract.md` is the exact 92,586-byte handoff prompt from this
conversation, SHA-256 `5cb7d0ed9520517d8e2a6f29239b0d0f217b7e2ea189d748b81f035c4503af91`.
It is deliberately not silently rewritten to disguise disagreements. Its historical
paths `reference/...` and proposed copy into `docs/mockups/` refer to the same
original now bundled in this skill. Do not create competing editable copies.

The previously posted reconstructed v32 is superseded as a visual reference.
A later shorter assistant draft also differed from the supplied prompt on several
policies. It is not an independently accepted replacement specification. Generic
skill suggestions mentioning Pause do not establish a Pause feature: v33 has Stop,
and the current API has cancellation plus new resumable jobs.

## Decision register

| ID | Topic | What is supported | What must not be silently decided |
| --- | --- | --- | --- |
| D01 | P1/review/translation | Global eligible-book P1, human approval, then P2-P5 per chunk | No auto-approval or per-section P1-to-P5 loop |
| D02 | F/T/E | Three modes and immediate acknowledged saves are required by v33; no mode-edit API in baseline | Earlier proposals conflict: freeze all edits after start vs allow stopped T/E changes. Do not choose a new invalidation/deletion policy. Implement only an explicitly approved backend plan-edit contract; otherwise report this control blocked |
| D03 | Progress | The mock uses weighted workflow values; one brief preserves backend-calculated weights, another proposes phase-only percentage | Choose neither as an unlabelled universal truth. Use actual backend counters, explicit denominator and unknown/indeterminate fallback until a product decision authorizes a percentage model |
| D04 | Publishing control | Runtime cancellation exists; automatic publish remains in the translation worker | One draft disables Publishing, another exposes Stop. Do not introduce a conflicting primary-action policy without approval; do not submit duplicate publish |
| D05 | Draft library | Mock opens a draft before Prepare | Real API creates a new destination during POST /api/imports and refuses preexisting directories. Do not precreate it in React or invent draft persistence |
| D06 | Reader expansion | Real Reader/context/markers exist; v33 shows a placeholder shell | Preserve existing behavior; do not invent annotation types, comment lists, AI tools or a complete Reader redesign |
| D07 | Settings | Read-only safe profile queries exist; mock Test/Add profile are simulations | Do not invent an editor, live diagnostic contract, credentials or automatic model request |
| D08 | Approval freshness | `approved` is a committed flag; `review.current` means source/analysis revision matches | It is not proof the newest edited choices were approved. A target requiring reapproval before further translation needs explicit backend validation, not a fabricated client boolean |
| D09 | Intended HTTP framework | FastAPI/Starlette was chosen for future delivery; baseline still uses http.server | Do not pretend the migration is installed or start two supervisors. Migration is a separate tested, authorized step |

For a task touching a U entry: inspect for a newer accepted implementation/decision,
record the concrete source, then proceed. If none exists, state the exact decision
needed and continue unrelated safe work. A disabled control with an explanation is
an honest temporary state, not completion of that required feature.

## Updating the skill

Update technical route/schema facts when verified code changes. Do not turn code
bugs into product requirements. Preserve conflict entries until they are explicitly
resolved; record date, instruction/decision source and affected tests. A description
of a proposal must not be relabeled as a user decision. Do not change reference hashes
or snapshot masks to make a visual regression pass.

## Skill format sources

Codex local-skill format/discovery checked on 2026-09-22 against official docs:
https://developers.openai.com/codex/skills (redirects to the official Build skills page).
Repository instructions: https://developers.openai.com/codex/guides/agents-md.
`.agents/skills/intelitex-web/SKILL.md` is the repo-local entry; do not install a
second independent `.codex/skills/intelitex-web` copy that can drift.
