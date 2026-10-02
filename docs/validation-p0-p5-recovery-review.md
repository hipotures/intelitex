# Persistent source-session recovery: review corrections

Date: 2026-10-02. Base commit: `6d921acd50806c6ef34f833968be8a16157d4c62`.
Dedicated worktree: `/home/user/DEV/intelitex-p0-p5`.
Branch: `work/p0-p5-persistent-sessions`.

The corrective implementation and this report are committed on the feature
branch. No production checkout, active translation workspace, listener, service,
or production configuration was modified, controlled, restarted, or probed.
No merge, deployment, push, or additional live model call was performed.

## Review evidence and fixes

The supplied `/home/user/review.md`, `recovery_microprobes.py`, and
`microprobe-results.json` describe the reviewed base commit. Their seven
extracted-method scenarios reproduce five manifestations of two defects and
include two successful controls. They use copied methods and deterministic
doubles, rather than repository or native integration tests. The corrective
regressions below execute the repository's actual Runner and session manager.
The historical script and its observations were not modified or rerun.

R1: native cleanup can succeed for an interrupted or failed consumer while its
revert reply or local clean marker is lost. One shared durable-outcome check now
protects ordinary cleanup and reconciliation when native history already contains
only P0. It verifies the original intent, task/pass/fingerprint, frozen semantic
and transport requests, model/effort binding, thread/session/slot IDs, complete
attempt evidence, matching persisted terminal history, P0 boundary, response
metadata, and retained usage. Terminal native failures remain failed outcomes
with validation not run. A generic `generation="failed"` field is insufficient.
After proving the baseline clean, reconciliation repairs the local marker without
another revert or inference. Previously accepted stages and physical usage are
preserved. Missing, foreign, live, or inconsistent evidence remains a hold.

R2: live completion, native recovery, and Runner's saved-candidate acceptance now
use the same selection-verification routine. Its requested model/effort come from
the original slot configuration and original semantic/transport request, rather
than a newly selected profile. Per-turn native rollout evidence must verify the
model and any requested effort. Mismatches are rejected; missing evidence creates
an explicit hold. `selection.verification.json` durably binds the outcome to the
intent, task, pass, thread, turn, scope, slot, input hash, and saved answer hash.
Rejected/held receipts survive restart even if a subsequent observation appears
to match. Existing completed metadata is checked and cannot replace a receipt.
Runner applies this gate before its semantic-validation retry handling, so a
selection failure cannot become a paid retry or canonical acceptance. Exception
recording preserves the explicit rejection and its complete physical evidence.
Both P0 readiness and consumer acceptance require verified completion.

The source-free codec, schemas, dependency graph, source/chunk boundaries,
frontend application, and legacy checkpoint behavior were not changed.

## Protocol and resource isolation

The official OpenAI developer documentation MCP was consulted before protocol
work. The fetched [app-server documentation](https://learn.chatgpt.com/docs/app-server)
describes lifecycle and terminal status. Undocumented exclusive revert and
replacement-rollout behavior were inspected separately in the matching
[Codex 0.160.0 source](https://github.com/openai/codex/tree/rust-v0.160.0/codex-rs).
The codex-app-server skill's installed-protocol audit passed on `codex-cli 0.160.0`,
with no model turn and no authentication.

Native regression tests use that installed app-server against an ephemeral
loopback Responses/SSE fixture. The fixture asserts that requests contain no
Authorization header; its configured provider requires no OpenAI authentication
and has zero request/stream retries. Genuine native interruption and failure are
combined with a successful native revert whose response is deliberately lost.
Every test gets a fresh project and private native home/SQLite/work directory.
The full suite additionally uses empty private control homes and service state;
`OPENAI_API_KEY` is empty. Temporary files are on disk rather than memory-backed
`/tmp`. Only completed scratch directories created for this corrective validation
were removed to recover disk capacity; logs and measurements were retained.

Each native validation command ran in its own systemd user scope with
`MemoryMax=256M`, `MemorySwapMax=0`, and `CPUQuota=50%`. The cap covers child
processes, including detached app-server processes. The complete Python scope
reported memory peak **268,447,744 bytes**, configured limit **268,435,456 bytes**,
and **0 OOM / 0 OOM kills**. The 12,288-byte accounting overshoot is reported as
measured. The controller's `max` counter was 7,182; this records limit pressure,
not OOM kills. CPU quota was `50000 100000`. Actual measurements and commands are
also retained in [the machine-readable report](validation-p0-p5-recovery-review.json).

## Actual results

| Check | Result |
| --- | --- |
| Corrected R1 regressions before behavior changes | 2 failed, 1 passed, 67 deselected; 7.84 seconds. Both native failure/interruption lost-ack cases reproduced R1; validation-failed cleanup was the successful control. |
| Corrected R2 historical completed-metadata regressions before changes | 6 failed, 64 deselected; 10.46 seconds. P0 and P2 bypassed selection verification for all three reported-selection defects. |
| First post-fix source suite | 69 passed, 1 failed; 81.66 seconds. The synthetic multiple-suffix fixture lacked the now-required complete ownership evidence. The fixture was enriched; its exclusive-boundary/idempotence assertions were retained. |
| Expanded source suite | 85 passed; 107.05 seconds. |
| Source-session/revision/reset/usage subset | 111 passed; 117.14 seconds, under the resource cap. This preceded the final preservation of complete evidence on initial selection rejection; the full run below covers that final change and its assertion. |
| **Complete Python suite on final implementation** | **1,832 passed, 0 failed, 3 warnings; 483.99 seconds.** |
| `node --test --test-concurrency=1 tests/*.cjs` | **31 passed, 0 failed.** |
| `.venv/bin/python -m compileall -q bookpipe translate.py` | Passed. |
| `uv lock --check` | Passed; 35 packages resolved. |
| `git diff --check` | Passed. |

An initial draft regression invocation produced 21 failures in 28.94 seconds;
some were test-harness defects (an incorrect attempt-enumerator import and
incomplete synthetic completed metadata). Those were corrected before the
behavior changes. The two isolated pre-fix runs above distinguish genuine
repository defects from those harness failures.

The final full run contains **87 source-session cases**, including the original
49 and 38 added cases. New coverage includes interrupted/failed/validation-failed
lost revert replies; preserved accepted work and exactly-once physical accounting;
wrong or missing terminal evidence; model/effort mismatch and missing evidence at
P0 and P2; initial rejection followed by restart; pre-verification crashes;
historical `finish_reason="stop"` without verification; Runner's independent
saved-candidate gate; and matching-selection recovery controls. Existing accepted
output lost-ack, foreign suffix, interrupted turn, pagination, acceptance-boundary,
reset, usage, pipeline, runtime, server, and web-production regressions also ran
in the complete suite.

The three warnings are the existing Starlette/httpx and anyio deprecations and
the deliberate duplicate-ZIP-member fixture. They are not test failures.

## Commands and retained evidence

Validation root:
`/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002`.
Stdout is retained as `r1-before.log`, `r2-stop-before.log`,
`first-source-suite.log`, `expanded-source-suite.log`, `focused-final.log`,
`full-final.log`, and `node-final.log`; scope measurements are in
`focused-resources.json`, `full-resources.json`, and `node-resources.json`.

The complete suite's actual command, run from the dedicated worktree, was:

```bash
systemd-run --user --scope --unit=intelitex-p0-p5-review-full --quiet \
  -p MemoryMax=256M -p MemorySwapMax=0 -p CPUQuota=50% \
  env TMPDIR=/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/tmp \
  CODEX_HOME=/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/control-home \
  CODEX_SQLITE_HOME=/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/control-sqlite \
  XDG_STATE_HOME=/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/state \
  OPENAI_API_KEY= \
  .venv/bin/python /home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/resource_runner.py \
  /home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/full-resources.json \
  .venv/bin/python -m pytest -q \
  --basetemp=/home/user/.local/state/intelitex-p0-p5-validation/review-fixes-20261002/tmp/full-suite
```

The private resource runner invokes the supplied command, records its exit code
and its own scope's `memory.max`, `memory.peak`, `memory.events`, `cpu.max`, and
`cpu.stat`, then returns the test command's exit code. It adds no service
operation or model request of its own. The focused pytest arguments were
`tests/test_source_sessions.py tests/test_revision_workflow.py tests/test_usage_observability.py`.
The Node scope used the same memory/CPU cap and resource runner.

## Unrun checks and limits

No paid/native-cloud inference was run for these corrections. The earlier
16-call live authorization was exhausted by the original implementation probe;
its measurements describe that earlier code and are not a live test of these
fixes. The historical extracted-method script was not rerun because it embeds
the reviewed methods rather than importing the corrected repository.

Frontend TypeScript/unit/browser checks were not repeated: frontend application,
styles, scripts, tests, and API contracts are unchanged by this correction.
The previous 64 frontend unit and 17 Chromium browser results remain historical;
the final complete Python suite covers current backend/server/web-production
behavior. Full-book paid inference, provider cache-expiry waiting, and a cloud
hard kill remain unrun. No production listener check or deployment test was run.

Selection rejection and missing native evidence intentionally remain recovery
holds. They retain output, usage, and native evidence for inspection and require
an explicit resolution; they never silently substitute a model or spend another
turn to clear the hold.
