# Paired Codex translation execution

Codex profiles default to these transport/execution options:

```json
{"options": {
  "p1_wire_format": "cache-shared-v2",
  "translation_wire_format": "cache-shared-v2",
  "translation_thread_strategy": "paired-passes-v1"
}}
```

Explicit project/profile options override defaults. A normal IntelliTex server
restart loads the new default; no accepted work needs migration or regeneration.
Models, reasoning efforts and pass assignments remain user-controlled. P1 stays
outside this lifecycle: independent book-wide analysis, then merge/review, then
translation chunks.

## Supported strategies

| Strategy | Conversations | Purpose |
| --- | --- | --- |
| `fresh-root` | Independent complete root for each attempt | Opt out of conversation reuse |
| `p2-parent-ephemeral-fork-v1` | P2 parent; P3/P4/P5 ephemeral siblings before P2 | Maximum conversational isolation; retained intentionally for future native explicit cache-breakpoint support |
| `paired-passes-v1` (default) | P2→P3 and separately P4→P5 | Reuse within dependency pairs while keeping P4 isolated from P2/P3 conversation |

The earlier [ephemeral-fork strategy](codex-cache-parent.md) remains supported.
Its implicit cache-boundary limitations do not change its semantic isolation.
Neither Codex 0.159.3 nor 0.160.0 exposes native explicit cache breakpoints through
app-server. This implementation adds no unsupported fields or interception.

## Lifecycle and actual history

```text
P2 private runtime:
  persistent fresh thread/start A
  inject SOURCE; inject COMMON
  one P2 turn -> raw answer -> full canonical validation
  accept P2 + immutable native snapshot; close/remove runtime

P3 new private runtime:
  copy accepted P2 native snapshot unchanged into this runtime
  thread/resume A, native path, excludeTurns=true
  one P3 turn: explicit accepted canonical SEMANTIC_AUDIT + ACTIVE_PASS=3
  close/remove runtime

P4 new private runtime:
  persistent fresh thread/start B (no resume/fork of A)
  inject SOURCE; inject COMMON; inject accepted canonical DRAFT
  one P4 turn: canonical SEMANTIC_AUDIT + SOURCE_SENTENCES + ACTIVE_PASS=4
  accept P4 + immutable native snapshot; close/remove runtime

P5 new private runtime:
  copy accepted P4 native snapshot unchanged into this runtime
  thread/resume B, native path, excludeTurns=true
  one P5 turn: explicit accepted canonical CORRECTION_LEDGER + ACTIVE_PASS=5
  close/remove runtime
```

P3 sees SOURCE, COMMON, the accepted P2 user turn and raw assistant answer, then
its own suffix. P5 sees SOURCE, COMMON, DRAFT, the accepted P4 turn and raw answer,
then its own suffix. P4/P5 see canonical P2/P3 artifacts as supplied data; they
never inherit their conversations. The raw answers are additional context, not
the canonical source of truth. Canonical decoding, conservative repair, effective
schema validation and semantic validation remain mandatory.

`cache-shared-v2` encoding, instructions, SOURCE/COMMON/DRAFT text bytes, compact
maps and fixed provider schema are unchanged. Continuations skip injection of
already inherited messages and retain the explicit accepted canonical audit or
ledger in the new turn. Canonical fingerprints and checkpoint reuse do not
include the strategy. The implementation does not fork/revert/compact threads
for this strategy, change P1, or introduce warm-ups/extra model calls.

## Persistence, guard and retries

`pair_parent.json`, response metadata and the normal selected checkpoint own each
accepted parent. They retain pass/pair, thread/session/turn IDs, native filename,
rollout and prompt/answer hashes, SOURCE/COMMON/instruction/schema hashes (and
DRAFT for P4), accepted canonical-result hash and repair consistency evidence.
The original snapshot is never resumed in-place. Each continuation/retry receives
an independent working copy in a fresh private CODEX_HOME/SQLITE home. No state
DB, user config, global runtime or auth changes are shared.

Before resuming, guards require a current selected accepted checkpoint, Codex
`cache-shared-v2`, identical source/common/instructions/schema, identical P4 draft
for P5, complete native history with exactly one completed parent turn, exact raw
answer/prompt consistency, and the current canonical dependency equal to the
selected accepted result. Decoding and repair of the saved raw answer must
reproduce that accepted result. Persistent existing cache-shared-v2 P2/P4 roots
can qualify without new metadata. Ephemeral P4 exports cannot be resumed without
a durable native snapshot and fall back safely.

Rejected P2/P4 attempts never become parents; their retries start new roots.
Every P3/P5 retry restores the original accepted parent snapshot, so failed child
answers do not contaminate later attempts. An unavailable/incompatible parent or
a native resume RPC rejection before submission uses the complete fresh-root
cache-shared-v2 request. An accepted upstream pass is never rerun just for cache.
Submitted inferences are not automatically repeated for a cache setup failure.
Local evidence failures do not become billable validation retries.

## Evidence and limitations

Attempts record `translation_thread_strategy`, `pair`, `pair_role`, and for
continuations `parent_pass`, `parent_attempt`, `resumed_thread_id`,
`resumed_session_id`, `pair_parent_status: available|unavailable|incompatible`,
`execution_strategy: paired_resume|fresh_root_fallback`. Parents record
`execution_strategy: pair_parent`. The actual resume/start, injection and turn
RPCs plus an application message plan and durable child rollouts are retained.
The native thread/session IDs must remain equal within each pair before a turn
can be submitted.

Usage comes from the final `tokenUsage.last`, with cumulative thread totals
stored separately. Cached/reasoning categories overlap input/output respectively;
never sum cumulative snapshots or add reasoning again. Only provider-reported
`cachedInputTokens` proves a cache hit. Same IDs and matching hashes do not.

The capability-based reasoning-effort integration is unchanged. Native Codex owns
configuration updates and request-level pinning; application baseline intent is
not proof of the provider's actual request-level effort. Effective requested
model/effort are checked against observable native evidence.

Paired history intentionally reduces conversational isolation inside each pair.
It is intended to improve reuse without the full-chain self-review risk; this
implementation does not establish a quality or latency improvement. Provider
cache expiration affects performance only. Correctness needs no cache hit or
local TTL tracking. Real quality/cache measurements remain for the user's next
chapter run.

## Offline verification

```bash
uv run pytest -q tests/test_codex_pair.py tests/test_codex_parent.py \
  tests/test_codex_cache_shared_v2_app_server.py tests/test_codex_effort.py \
  tests/test_profile_wire_defaults.py tests/test_transports.py tests/test_pipeline.py
```

Provider-facing tests use the installed standard app-server and an unauthenticated
loopback Responses mock. They stop/remove the parent runtime before starting a
new app-server, capture actual outgoing histories, verify equal native identities,
clean retries, isolated P4, one inference per attempt, per-turn usage and fallback.
No real model call or browser test is required.
