# Authority, provenance, and unresolved decisions

## Audited baseline and current implementation

Inspected repository: `hipotures/intelitex`, main commit `2bb9aa2df178355502a7529e460fca12804ef377`, titled
`Complete single-server workspace workflow API`. Re-read actual HEAD before work.
The implementation task began at `b0c40dfaf90add7340da2b992d4e18f7b473383e`; current additions are audited in `api-baseline.json` and `docs/web/contract-map.md`. The live code is newer than the `1f85da8` baseline in the supplied implementation
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
| D02 | F/T/E — approved | Before the first persisted P1 attempt, modes may change freely. Afterwards Full membership is frozen (`analysis_membership_locked`); idle T↔E remains allowed. | Reject mutations during jobs/cleanup; recompute required work/readiness and publication currency. Retain dormant evidence and validate reuse. |
| D03 | Workflow progress — approved | Backend calculates the implementation contract’s weighted percentage and returns its counts and denominator. | Label as workflow progress, never ETA; unknown is null. Detail screens retain actual counts. |
| D04 | Publishing control — approved | Automatic publication keeps the primary action disabled as `Publishing…`. | No Pause or duplicate publish; terminal failure exposes backend publication retry without repeating translation. |
| D05 | Library setup and draft — approved, refined in issue #1 and later Prepare decision | Library cards never create workspaces. `Add to workspace` opens read-only preflight and a one-time setup modal; Cancel leaves no workspace; Save atomically persists a configured draft. A source may have multiple workspaces. Draft pass profiles can change with a current settings revision before Prepare. | Save retries must be idempotent for the same request, not unique per source. Persist source identity, optional label and immutable language pair in workspace-owned metadata, and pass profiles in authoritative settings. Web Prepare imports without a provider call, estimates one token per four Unicode characters rounded up, and stops before P1. P1 validates its own tokenizer/context counts; estimates are never exact checkpoint evidence. |
| D06 | Reader expansion | Real Reader/context/markers exist; v33 shows a placeholder shell | Preserve existing behavior; do not invent annotation types, comment lists, AI tools or a complete Reader redesign |
| D07 | Settings | Read-only safe profile queries exist; mock Test/Add profile are simulations | Do not invent an editor, live diagnostic contract, credentials or automatic model request |
| D08 | Approval freshness — approved | Terminology edits after approval invalidate freshness; Python blocks translation until latest revision is confirmed and committed again. | Historical approved flag is insufficient. Retain checkpoints; application validation decides downstream staleness. |
| D09 | HTTP framework | Authorized production migration now uses FastAPI/Starlette behind the existing serve entrypoint. | One supervisor/ASGI worker; compatibility HTTP remains for regression coverage. |

For a task touching a U entry: inspect for a newer accepted implementation/decision,
record the concrete source, then proceed. If none exists, state the exact decision
needed and continue unrelated safe work. A disabled control with an explanation is
an honest temporary state, not completion of that required feature.

## Approved product decision provenance

On 2026-09-22 the task owner explicitly resolved D02, D03, D04, D05 and D08 in the implementation conversation. A later [product decision in issue #1](https://github.com/hipotures/intelitex/issues/1) refines and supersedes the earlier *D05 Library/draft flow*: Library remains a source catalog, `Add to workspace` opens setup before creation, Save creates a configured draft, and multiple workspaces may share one source. Its read-only preflight, source/language/profile setup, durable metadata and Prepare boundary are part of that target. The old one-source/one-draft endpoint remains only for compatibility; the explicit setup Save route implements the approved target. D02, D03, D04 and D08 retain their approved policies unchanged.

The task owner subsequently required web Prepare to avoid model/provider requests and
count one estimated token per four characters. This supersedes the prior backend
behavior that discovered the P1 provider and tokenized source text during import.
It also supersedes the historical handoff's section 5.2 warning against using an
unspecified invented token count: the later user instruction supplies the exact
estimation policy, and the count is labeled estimated rather than exact usage.
The selected profile remains saved for P1–P5 and may be changed on an unprepared
draft through a revision-controlled application command. Direct CLI import retains
its existing provider-aware behavior; the web worker uses the local estimate.

The supplied implementation contract is an immutable historical handoff, so its sections 5.2, 16.1 and V33-02 retain the superseded one-draft wording; read those sections through this decision register. The original v33 bytes also remain unchanged. The separate multilingual pipeline and derived SQLite cache items in issue #1 are not silently included in this Library/setup/Prepare implementation; their scope and evidence stay separate in `docs/web/contract-map.md`.

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
