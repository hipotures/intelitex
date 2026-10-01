# Codex translation cache parent (retained strategy)

The default is now [paired-passes-v1](codex-paired-passes.md). Select this
retained strategy explicitly for maximum conversational isolation:

```json
{"options": {
  "p1_wire_format": "cache-shared-v2",
  "translation_wire_format": "cache-shared-v2",
  "translation_thread_strategy": "p2-parent-ephemeral-fork-v1"
}}
```

Explicit profile options override defaults. `translation_thread_strategy:
fresh-root` opts out; older wire formats remain supported and use fresh roots.
Non-Codex providers are unchanged. A normal server restart reloads the defaults;
no project migration, checkpoint invalidation or regeneration is needed.

## Why native ephemeral forks

The installed Codex 0.159.3 uses distinct cache affinity for fresh roots. In its
matching [session implementation](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/session/session.rs),
ephemeral forks intentionally inherit the source session's cache routing key,
while preserving separate storage/lifecycle identities. This is native Codex
behavior, not an IntelliTex header or cache-affinity override.

The [official app-server contract](https://learn.chatgpt.com/docs/app-server#api-overview)
exposes fork and injection. Exact cutoff/path behavior is audited against the
installed experimental schema and matching
[thread processor](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/app-server/src/request_processors/thread_processor.rs)
and [turn truncation](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/thread_rollout_truncation.rs)
implementation. No Codex modification or build is required.

## One chunk, independent conversations

```text
P2 isolated runtime:
  persistent thread/start
  inject SOURCE user message
  inject TRANSLATION_COMMON user message
  one P2 turn -> canonical validation -> accepted checkpoint
  copy native rollout into attempt evidence -> stop/remove runtime

P3 isolated runtime: fork accepted P2 BEFORE its turn -> one P3 turn
P4 isolated runtime: fork accepted P2 BEFORE its turn -> inject DRAFT -> one P4 turn
P5 isolated runtime: fork accepted P2 BEFORE its turn -> inject same DRAFT -> one P5 turn
```

Each fork specifies `ephemeral=true`, `beforeTurnId=<accepted P2 turn>`,
`excludeTurns=true`. Its history retains SOURCE and COMMON; the P2 user suffix
and assistant response are excluded. P3–P5 are siblings, never continuations of
one another. P4/P5 receive the accepted canonical P3 draft as data, not its
conversation. P3/P4 receive accepted P2 audit data; P5 receives accepted P4 ledger
data and does not acquire P2 audit input. Codec bytes, instructions, schema,
canonical fingerprints, repair and validation rules are unchanged.

P1 remains the independent book-wide analysis phase. It is not a translation
parent and does not change its unit boundaries, defaults or execution lifecycle.

## Cold-runtime persistence and acceptance

The selected P2 checkpoint receipt owns the parent. After successful canonical
validation, `cache_parent.json` and normal response/job metadata retain the native
parent thread/session/turn, copied rollout, content/instruction/schema hashes,
rollout hash and canonical accepted-result hash. A rejected P2 attempt never
becomes a selected parent. P2 retries start a new persistent root each time.

For each child, the self-contained accepted P2 rollout is copied **unchanged**,
using its original native filename, into the new private CODEX_HOME session
directory. This is needed because 0.159.3 resolves paginated forks by ID even
when `path` is supplied. Codex's own resolver discovers/indexes that export;
IntelliTex does not fabricate history or SQLite rows, copy Codex state DBs,
resume a conversation, or keep a shared runtime. Fork parameters use that local
native path. The import and all private authentication/runtime files are removed
after the attempt; durable parent evidence remains in IntelliTex artifacts.

Before inference IntelliTex verifies selected acceptance, provider/wire version,
SOURCE/COMMON/base/developer/schema equality, persisted identities, completed P2
boundary and exact pre-turn data. It rejects pre-turn assistant/tool history.
Current P3/P4 audit input must also match the selected accepted P2 artifact.
No model-name or effort-equality eligibility rule is imposed: each child still
uses the user's chosen model and capability-based effort configuration.

Previously accepted cache-shared-v2 P2 attempts can be used if their original
evidence passes these checks, without rewriting those attempts. Missing,
changed, unsupported or incompatible parents fall back to the existing complete
fresh-root request. A fork RPC rejection before any turn also permits that safe
fallback; a submitted inference is never automatically repeated for cache setup.
An accepted P2 is never rerun merely to seed cache. Every P3–P5 validation retry
uses a new ephemeral sibling or complete fallback root, without failed answers.

## Evidence and limits

`request.transport.json` records the actual fork/start, injections and final turn,
plus the logical complete message plan with inherited SOURCE/COMMON. Child
metadata includes parent evidence reference, child/fork identities, cutoff,
`cache_parent_status: available|unavailable|incompatible` and
`execution_strategy: ephemeral_fork|fresh_root_fallback` (P2: `p2_parent`, explicit
root strategy: `fresh-root`). Ephemeral child `thread_path`/`rollout_copy` are null;
RPC logs, completed answer, codec context, decoded canonical output, validation
and accepted result remain durable evidence. Unobservable effort-update fields
stay null; actual standard turn/settings evidence is checked when available.

All six normalized token categories and raw usage snapshots are preserved. Only
the final `tokenUsage.last` is used for the call, never summed thread totals.
Matching hashes are not hits: provider `cachedInputTokens` is authoritative.
Provider expiry or a model/effort change may reduce reuse; this affects performance
only. No TTL assumptions/state, warm-ups, keepalives or extra model turns exist.

## Deterministic verification

```bash
uv run pytest -q tests/test_codex_parent.py \
  tests/test_codex_cache_shared_v2_app_server.py tests/test_codex_effort.py
```

Tests use installed standard Codex 0.159.3 and an unauthenticated loopback-only
Responses mock. They shut down/remove the P2 runtime, reopen the application
store, start fresh child runtimes, and capture the actual provider-facing request.
They verify inherited SOURCE/COMMON, explicit artifacts, no earlier assistant
answers or active-pass turns, sibling retries, one inference per attempt, native
cache routing key inheritance, configuration precedence and safe fallback.
No paid/live inference is part of implementation testing.

See the [deployment verification report](codex-cache-parent-validation.md) for
executed regression checks, pre-existing failures and effective salvation-03
profile resolution.
