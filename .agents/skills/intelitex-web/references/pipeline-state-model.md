# Pipeline, job and display state

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

B: use `WorkflowQueries.pipeline`, `runtime/models.py` and `docs/server-api.md`.
R: use v33's five-phase rail and compact pass marks. Read D02-D04/D08 before
implementing a policy not supplied by the backend.

```text
Prepare/import -> global eligible-book Analyse/P1 -> human Review -> approve
  -> chunk A: P2 -> P3 -> P4 -> P5
  -> chunk B: P2 -> P3 -> P4 -> P5
  -> ... -> automatic publication in the final translation worker
```

Source sections group units; one section may contain multiple analysis units/chunks.
Do not count table rows as execution units or run P2 for the first analysed section
before the complete P1/review gate. Confirmation and committed approval are distinct.

## Raw runtime states (B)

| State | Meaning / frontend behavior |
| --- | --- |
| starting | Launch in progress; suppress duplicate starts |
| running | Supervised live job; operation/task may be known |
| stopping | Cancellation requested; wait, no instant completed/stopped claim |
| succeeded | Operation ended successfully; refresh domain state |
| failed | Structured failure/protocol/exit failure; safe reason and authorized retry |
| cancelled | Cancellation finished; retain committed checkpoints |
| abandoned | Prior server no longer owns the record; no auto-restart or PID assumption |

Normal path: starting -> running -> succeeded/failed; cancellation uses stopping ->
cancelled, with error paths permitted by the actual supervisor. Launch can fail
before running. Startup marks unfinished persisted records abandoned. Never persist
`paused`, `resuming`, `partial`, or visual labels as new raw runtime states.

Stop is SIGINT with grace, then SIGTERM, then SIGKILL fallback, as runtime documents.
Do not copy those grace windows into a browser timer that fabricates cancellation.
Workers are released from `owned` only after their cleanup path finishes. Retained
history stays in the registry. A terminal job status and cleanup ownership may not
become observable simultaneously; authoritative locks still protect a new action.

## Raw pipeline response at the baseline (B)

`stage`: analysis | review | translation | publication | complete.
There is no prepared-draft stage for a not-yet-imported workspace in this endpoint.

`analysis`: complete, planned, units. Unit states are pending/completed/error,
with receipt/checkpoint validation and separate failed-attempt metadata.

Translation units carry persisted status pending/done/stale and passes 2-5.
Each pass has `checkpoint_state`, `retained_count`, nullable `attempt_result`,
`failed_attempt_count`. P2-P4 are pending/retained. P5 can additionally be
completed/stale/error based on the registered verified final artifact.

**Retained is not current completed.** Do not turn a historical P2-P4 cache entry
into a green check. A richer current-pass projection needs backend support. Preserve
historical failures separately: a later valid successful checkpoint is not failed.

`review.current` checks source/analysis compatibility. The public pipeline `approved`
now additionally validates the committed approval receipt against the current Review
digest. The historical Store flag alone is insufficient. D08 is approved: editing
terminology blocks translation until renewed confirmation and committed approval.
Checkpoints are retained; existing application dependency validation marks staleness.

## Actions and projections

`actions` contains analyze, prepare_review, review, approve, translate, publish,
each `{allowed, reason}`. These are advisory at snapshot time; commands recheck locks
and domain validation. Use reason codes rather than parsing human prose.

not_applicable/pending/running/partial/completed/stale/error/unknown is a **target
UI projection vocabulary**, not an existing persisted enum or complete response.
It must not discard the raw `retained` distinction. Group authoritative counts only;
unknown truth remains unknown. Tie transient running overlays to matching live
job/task IDs and their freshness. On disconnect label cached data stale, not stopped.

F=Full(P1-P5), T=Translate only(P2-P5), E=Excluded. Content type is descriptive.
D02 is approved and implemented by `WebWorkspaceService.configure`: all modes can
change before the first persisted P1 attempt; then Full membership is locked while
idle T↔E remains allowed. Active jobs/cleanup and stale revisions reject mutations.
Eligibility is an overlay on frozen source boundaries. Excluded evidence is dormant;
re-inclusion validates retained inputs, receipt and continuity before reuse.

Usage uses known/unknown coverage, exact units and actual historical model identity.
Cached input/reasoning may be subsets of reported totals. Do not double-count them.
Preflight UTF-8 upper bounds are not tokens; received characters are not usage.

Progress must name its denominator and distinguish phase/workflow/reading progress.
No fake smoothing, timer-driven completion or inferred 100% from `succeeded`.
D03 is resolved: `WorkflowQueries.pipeline` returns the implementation contract’s weighted workflow percentage together with analysis/translation counts and basis. Unknown denominators produce null; approval invalidation can reduce progress.
