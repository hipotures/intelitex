# Frontend state and mutation contract

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

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
outcome must be reconciled before any retry. Current API persists accepted job receipts keyed by request_key; GET /api/requests/{key}
reconciles an unknown outcome. Keys are scoped to the server/workspace/operation.

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

For refined D05, a Library card click opens source details without a mutation.
`Add to workspace` gathers read-only preflight and setup values; Cancel discards only
the unsaved local form. Save persists the draft through a backend command before
navigation. Keep the same request identity for reconciliation after an unknown Save
outcome; a fresh deliberate Save may create another workspace for the same source.
Neither React nor localStorage owns durable draft identity, language pair, profiles
or Prepare state. The Library details/setup flow uses selected-source preflight and
the explicit `POST /api/workspaces/setup` Save route; card click does not mutate.
The unprepared workspace reads its saved P1–P5 assignments and settings revision
from GET `/api/workspaces/{id}/profiles`. Its visible model panel PATCHes one
assignment with that revision and explicit confirmation; a 409 requires a fresh
read and deliberate reapplication. The panel remains usable after a failed early
Prepare, including a leftover empty `.lock`. No provider call occurs on this edit
or during web Prepare. P1 performs provider-specific counting later.
The prepared Prepare detail page offers a confirmed in-place rebuild only when the
snapshot permits it; the backend remains the final guard. It uses a request key,
fresh pipeline read and current config revision, then follows the supervised import
job. Analyse/P1 is displayed as whole-book work; section cells show actual counts.
For F/T/E PATCH, a successful returned revision updates the affected cached cell,
old in-flight pipeline reads are cancelled, and reconciliation proceeds without
holding the control on unrelated workspace-list queries. The global list is marked
stale for its next use without refetching it on this screen. Only queries scoped to
that workspace are immediately refetched; Reader prose remains protected from replacement.
Workspace resource matches require the complete ID boundary (`w-1` must not match
`w-10`). Subsequent server reads replace the provisional cell/readiness with
authoritative state.
The application checks persisted P1 receipts and P1 attempt manifests directly
for membership locking, without constructing the full historical usage report on
every Processing change.
