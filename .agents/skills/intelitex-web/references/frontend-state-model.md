# Frontend state and mutation contract

Use TanStack Query for authoritative server snapshots; Router/search params for
navigation/filter identity; local React state for open panels, focus and unsaved input.
LocalStorage may hold presentation preferences/bookmark offsets, never approval,
checkpoints, job truth, persisted model assignments or archive membership.

Scope query keys by actual server context, workspace ID, resource and child ID.
Evidence includes term ID; chapter includes chapter/book identity. Cancel/ignore
obsolete results on navigation. Reuse current typed DTOs; runtime-validate wire data
at one API adapter boundary. Missing metadata is unknown, not a fabricated default.

Backend permissions drive commands. Frontend may format/group explicit facts but
cannot invent workflow gates or widen a false action. A disabled control has a safe
reason; unknown/unsupported is not completed. Record missing data in the implementation
map instead of hard-coding books/profiles to satisfy the layout.

## Mutation lifecycle

idle -> submitting -> acknowledged saved state / accepted job -> domain reconciliation.
Local submitting is not a persisted pipeline state. A 202 is not completion. Keep
captured workspace/job IDs through the whole request; do not target whatever route
happens to be selected on response. Suppress duplicate clicks, not all other workspaces.

Serialize mutations sharing a whole-review/config revision. Every accepted response
updates the token used by the next write. Do not fire independent optimistic writes
with the same digest or silently retry a conflict with a new digest. Candidate/custom
input remains local while typing; flush before review/navigation actions. An unknown
outcome must be reconciled before any retry. Current API has no idempotency receipts;
required idempotency is a tested backend addition, not an extra unrecognized field.

On conflict: retain user's input, fetch current server value, explain conflict, require
explicit reapplication. On validation: field-level message. On network failure: preserve
last good state/draft; no fake success. Unsent/failed text drafts require stay/discard
handling on navigation. Never warn that closing the tab stops a pipeline job.

Refetch must preserve current selection, caret/focus, filters, scroll, accordions,
open drawer, local draft and Reader location. Refreshing a live counter must not remount
the whole page. Review & next uses the pre-mutation filtered ordering so removing the
current Unreviewed item cannot skip the successor.

## Routes and controls (target, not HTTP endpoints)

Work home, workspace overview, Prepare/Analyse/Review/Translate/Publish detail views,
and independent Reader mode must support deep link/reload/Back/Forward. v33 only uses
local mock route objects; actual typed route names are in the supplied implementation
contract section 3. Map to existing equivalent routes once rather than create duplicates.
Never confuse browser routes with /api endpoints.

All unblocked phase tiles navigate; blocked links expose prerequisites, never execute
on entry. Source preview is a right drawer. Settings/archive confirmation are dialogs.
Work/Reader remember independent locations. One active overlay, accessible focus/return,
Escape/scrim behavior, no unnoticed draft loss. Table controls stop row-click propagation.

Transient/empty/error states have explicit UI: no source library configured; configured
but empty; load failed; no terms; no evidence; no verified text; missing profile; partial
usage; stale output. Unknown usage is not zero and stale connection is not failed job.
