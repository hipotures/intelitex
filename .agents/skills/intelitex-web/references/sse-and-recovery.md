# SSE, replay and recovery

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

B: `server/http.py:Handler._events`, `runtime/events.py`, `registry.py`,
`protocol.py`, `models.py`. Client policy below is implemented in `web/src/realtime`; its tests distinguish snapshot ordering, replay and reconciliation.

Use one app-level EventSource per browser tab for /api/events. Keep it across routes.
Do not subscribe once per component/row or use a global current-workspace variable.
Existing optional query keys: workspace_id,job_id,after. Last-Event-ID takes precedence.
Native EventSource does not accept arbitrary headers; use built-in reconnection or
supported after when deliberately constructing a new connection.

Named events, not default onmessage:

```text
event: snapshot
data: {jobs:[...],cursor:N}

after that:
id: N+1
event: progress
data: {id,job_id,workspace_id,sequence,timestamp,event:{kind,current,total,values,...}}
```

Snapshot has no SSE id so it does not replace the requested replay cursor. Keepalive
comments arrive about every ten seconds and are not JS progress callbacks. No progress
callbacks for a long provider wait is not proof of failure/offline.

## Apply state and append history separately

Track the global ID for replay/activity de-duplication and per-job sequence watermark
for runtime-state application. Snapshot initializes current jobs/watermarks. An older
replayed envelope may enter history once but cannot roll newer job state backward.
New job ID means a separate sequence. Never compare per-job sequence numbers across
jobs or replace workspace B from A's event. Never invent IDs for missing history.

Job completion, P1 completion, P5 availability, publication, recovery and direct HTTP
mutations trigger authoritative query invalidation. Waiting/character-count events
update only matching transient task fields. Never convert those into durable checkpoints.
Failed historical attempts remain history after a later success.

Start with current queries plus stream; reconcile again after first snapshot to repair
initial-load races. If an event arrives during a query, mark that key dirty and schedule
one trailing refresh. Opaque digests are equality tokens, not monotonically sortable IDs.

After reconnect, gaps or pruned history: refresh jobs and affected domain snapshots;
replay is bounded history, not complete project truth. Baseline retains 1,000 events
per job. Scope watermarks to the known origin and actual server/workspace context.
The current API exposes no registry epoch. Do not persist a cursor across unrelated
server/root sessions at the same origin without a verified scope mechanism. A lower
snapshot cursor requires discarding incompatible watermarks and reconciliation.

## Deterministic client policy from the supplied handoff

Coalesce durable invalidations at most once per 1,000ms per visible workspace with
a trailing refresh; terminal states/direct successful mutations flush immediately.
Transient counters render at most every 100ms. Test local runtime updates within
250ms and durable refetch within 1,000ms plus network time. No timer fabricates progress.

Healthy-stream reconciliation: visible summaries every 15s, idle review revision
every 5s, Reader availability every 15s. Unhealthy stream: active jobs/models every
5s, idle visible listings every 15s. Stop fallback when stream plus reconciliation
succeed. Pause routine polling when hidden; refresh on visibility/focus/online.
Use one coordinator, not multiplied intervals in every component.

GET transient retries: at most two (1s,2s); no automatic 4xx or mutation retries.
Show one Live/Reconnecting/Offline indicator and preserve last known content as stale.
No client reconnect event starts/stops/resumes a job or approves terminology.

Keep logs bounded and deduplicated. Autoscroll only when already following the bottom;
manual inspection must not jump to the live unit. Refs/filters/scroll/drafts survive.
All new events, public last_event snapshots, and legacy replay remain path-sanitized.
