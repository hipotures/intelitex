# P0-P5 persistent source sessions: implementation validation

Date: 2026-10-02. Dedicated checkout: `/home/user/DEV/intelitex-p0-p5`.
Branch: `work/p0-p5-persistent-sessions`. Implementation parent: `dc6052b`;
PRD reviewed baseline: `e4b622fc447c1df71028aa114cff6fdbd41d7204`.
This report and the implementation are committed together. Nothing was merged,
deployed, or restarted in the production checkout.

## Delivered behavior

The explicit new execution mode implements P0 and all five semantic stages.
New workspaces select it automatically. Existing Start operations prepare missing
P0 within Runner, with unchanged frontend source and preserved review gates.
Exact source scopes share compatible durable native sessions across P1/P2;
split scopes remain distinct. Every validated or durably failed P1-P5 attempt
returns history to P0 with an exclusive native revert. Accepted canonical
P2/P3/P4 artifacts, including repairs, are supplied explicitly downstream.
There is no fork, pair retention, ephemeral child, source retransmission or
fresh-root fallback in this mode.

Authoritative Store inventory, immutable source/maps, chapter-first artifacts,
private stable homes/SQLite/cwd, slot leases, compatibility generations,
turn-correlated events and outcome-first recovery are implemented. Lost start
and revert replies, terminal output before validation, acceptance before cleanup,
source corruption, missing runtime and foreign/live history are handled without
blind duplicate inference or destructive cleanup. P1 snapshots freeze predecessor
receipts; accepted merges are idempotent. P1 reset reconciles its pending native
suffix before archiving its evidence. Both usage enumerators retain archived
physical P1 attempts exactly once, without allowing archived tasks to be recovered
as current analysis. Read-only operations and accepted
checkpoints never prepare P0. Explicit archive/rebuild/purge is available through
`source-sessions`. Legacy codecs/layouts and standalone other-provider source
semantics remain available.

The fixed transport schema and generic baseline support P0-P5. Only active-turn
instructions carry editorial stage rules; P0 contains no analysis memory or review.
Exact Unicode sentence ranges reconstruct P2/P4 semantics without source copies.
Full retained context, dynamic data, schema/contract, output reserve and safety
margin are checked. Missing usage remains unknown; P0 and failed attempts are
counted separately. Phase timings and native/logical identity evidence are retained. An unsent
durable intent is recovered only when the complete RPC log proves no turn/start
was sent. Such consumer preparation failures/stops do not falsely count as
submitted P1/P2 usage. New continuation workspaces adopt the new mode without
changing a legacy predecessor.

## Environment, protocol and isolation

Python 3.14 in the dedicated `.venv`; Node 22; Codex CLI `0.160.0`.
The official OpenAI developer documentation MCP was consulted first. Public
[app-server documentation](https://developers.openai.com/codex/app-server/)
and the installed generated experimental schemas informed supported protocol
calls. Version-specific revert/materialization behavior was checked against
[pinned 0.160.0 source](https://github.com/openai/codex/tree/rust-v0.160.0/codex-rs).
The codex-app-server skill's installed-protocol audit passed. Runtime feature
checks regenerate schemas for persistent start/resume, paginated turn/items,
exclusive `beforeTurnId`, strict output schema and interrupt.

Offline native integrations run the installed app-server against a loopback SSE
mock requiring no authentication and no paid inference. Every test uses a fresh
pytest project/private runtime; HTTP ports are ephemeral. Browser tests use
repository fixture servers and mock routes. The Browser plugin is unavailable,
so repository Playwright tests were used. No production checkout, project,
settings or lock was edited, and no production stop/kill/start/restart/deploy command
was issued. This did not prevent the resource incident described below.
Scratch cleanup was limited to finished validation directories created during
this run. An initial standard Playwright install also pruned an unused older
shared browser cache before subsequent browser setup used a private cache. Raw live/native/auth material stays outside Git.

## Regression results

| Check | Actual result |
| --- | --- |
| `uv lock --check` | Passed. |
| `uv run python -m compileall -q bookpipe translate.py` | Passed. |
| Complete Python suite before the final P1-reset changes | 1,793 passed, 3 warnings, 401.29 seconds. |
| Latest complete Python suite | 1,792 passed, 2 failed, 3 warnings, 398.64 seconds. Both failures were the reset projection defect fixed afterward. |
| Final affected regressions, under the resource cap | **141 passed**, 1 warning, 73.76 seconds: all 84 web-production tests, all 49 source-session tests and all 8 revision-workflow tests. |
| Source-session tests before the final projection fix | 49 passed, 50.00 seconds. |
| Source-session/reset/usage regression before that fix | 73 passed, 53.76 seconds. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm --prefix web ci` | Passed: 280 packages, zero reported vulnerabilities. |
| `npm --prefix web test` with private Chromium/library/font paths | Typecheck/build passed; 64 unit tests across 16 files passed; 17 browser tests passed (187.58 seconds for browsers). |
| `git diff --check` | Passed. |

The final full Python suite was **not rerun** after the small ReadStore/workflow
projection fix, to avoid another broad shared-host load. All directly affected
tests were rerun and passed under enforced resource limits. The earlier full-suite
failure is recorded rather than represented as an all-green final full run.
Three original full-suite warnings concern Starlette/httpx, the anyio portal alias
and an intentional duplicate-ZIP-member fixture.

Exact final targeted command, in the dedicated worktree and its own virtualenv:

```bash
systemd-run --user --scope --unit=intelitex-p0-p5-final-validation --quiet \
  -p MemoryMax=256M -p MemorySwapMax=0 -p CPUQuota=50% \
  env TMPDIR=/home/user/.local/state/intelitex-p0-p5-validation/tmp \
  .venv/bin/python -m pytest -q \
  tests/test_web_production.py tests/test_source_sessions.py \
  tests/test_revision_workflow.py
```

The full-suite command was `uv run --group dev python -m pytest -q`, with the
same disk-backed TMPDIR for its latest run. The successful frontend command was
`npm --prefix web test`, with `PLAYWRIGHT_BROWSERS_PATH` pointing to the private
Chromium cache, `LD_LIBRARY_PATH` to the privately extracted libraries, and
`FONTCONFIG_FILE` to the private font configuration. These paths were test-only;
no production environment was changed. Final targeted stdout is retained outside
Git in the disk-backed validation directory. One initial targeted invocation
included a nonexistent test filename and collected no tests; the corrected
command above is the reported successful run.

The last complete Python run had two failures in the compatibility and production
HTTP variants of `test_analysis_reset_is_revisioned_and_keeps_versioned_evidence`.
Historical P1 attempts had correctly remained billable after reset, but the
workflow projection used that historical usage to freeze active membership.
The final change uses the existing current-evidence predicate through ReadStore;
archived costs remain counted while reset unlocks current P1 membership. Existing
reset assertions were preserved. Final targeted results are recorded above.

The untouched baseline ran before implementation: 1,733 passed and 11 failed in
242.25 seconds. Failures included stale command inventory, legacy default-wire
expectations, a production-dependent profile fixture and prompt fingerprint data.
Legacy wire fixtures now freeze the original prompt bytes instead of rewriting
historical golden hashes. The production-dependent test now constructs fresh
settings locally. Legacy inheritance fixtures explicitly omit the new execution
contract, and current prompt fingerprint fixtures reflect intentional source-binding
wording. Existing canonical validators remain unchanged.

Browser setup initially failed: `--with-deps` required unavailable `su` authentication;
Node's browser download timed out. The matching Chromium shell was downloaded
from the Playwright CDN and extracted into a private `/tmp` cache. Missing libraries
and DejaVu fonts were downloaded as packages and extracted privately, without
installing system packages. Fontconfig uses a private configuration/cache. A
concurrent run hit memory pressure and Vitest worker timeouts; serial units passed,
and a later ordinary unit run also passed. A later `/tmp` quota failure affected
fixture setup; final Python scratch state uses a dedicated disk-backed TMPDIR. Initial browser runs revealed stale
`Toggle theme` selectors and 15-second expectations for the existing 30-second
healthy-stream reconciliation. Tests now match existing three-theme controls and
bounded 30-second reconciliation, preserving the checks. The `npm test` script
now performs its required typecheck/build before browser tests on clean checkouts.
The fixtures also provide the existing P1 preview endpoint, scope selectors
to the current completed-translation/publish layout, and allow only explicitly
expected read cancellations during P1 reset/compatibility changes. Worker tests
wait for ownership release after the terminal progress event before launching
another writer. Frontend application source/styles were not edited.

## Shared-host resource incident

Initial regressions shared a host with approximately 3.9 GiB RAM, no swap and a
memory-backed `/tmp`. Concurrent testing and accumulated private scratch data
created severe memory pressure and a temporary-directory quota failure. The
production listener was subsequently observed unavailable. I did not intentionally
stop or restart it, but these tests may have caused its loss; workspace/runtime
isolation was insufficient to isolate host resources. The session cgroup later
reported `oom_kill=1`. Kernel logs were inaccessible, so neither the killed process
nor the causal timeline could be confirmed. A new production process was later
observed responding HTTP 200; it was not started by this implementation.

Following the user's instruction to leave the running server alone, no further
production inspection or control was performed. Further browser/full-suite runs
were suspended. The implementation's own 261 MiB Chromium cache was moved from
`/tmp` to disk-backed validation storage. Final targeted offline tests use a fresh
disk-backed TMPDIR inside an isolated systemd user scope with `MemoryMax=256M`,
`MemorySwapMax=0` and `CPUQuota=50%`. Those limits were verified on the active test
scope (`MemoryMax=268435456`, `CPUQuotaPerSecUSec=500ms`). The cap applies to its
test descendants, not to any existing production process.

## Bounded live validation

The user explicitly authorized isolated reuse of configured Codex authentication
and at most 16 live model calls using `gpt-6.1-sol`, effort `high`. Command:

```bash
uv run python experiments/p0_p5_live_validation.py \
  --scratch /tmp/intelitex-p0-p5-live-20261002c \
  --authorize-auth --max-calls 16
```

The scratch path must not exist; every invocation creates a new three-chapter
HTML book, project and external runtime. This script is explicit and is not
part of pytest or CI. The bound is enforced before each `turn/start`; no more
paid turns were submitted after the 16-call run. Earlier fixture/configuration
setup failures submitted zero turns and consumed zero model tokens.

**Actual result: passed, exactly 16 submitted model turns, four source scopes.**
All reported model/effort fields were `gpt-6.1-sol` / `high` on CLI `0.160.0`.
P0 was stopped at its accepted boundary and resumed in a fresh application;
ordered P1 ran for all chapters; translation correctly stopped at review and
proceeded only after deliberate scratch review approval. Chapter 1 P1/P2-P5
shared one P0/native thread. A fresh process resumed after accepted P2. One P3
completion was deliberately rejected by local validation, then retried once
from P0. Accepted P4 was interrupted before revert; P5 reconciled cleanup without
another P4 inference. Chapter 3 used different analysis/translation scopes and
completed P2-P5 for its first translation chunk. Every final native history had
exactly its accepted P0 turn. All P1-P5 source bytes added were zero.

Reported totals: **104,419 input; 57,344 cached input; 0 cache-write input;
2,252 output; 1,050 reasoning output; 106,671 total tokens**. Cached input and
reasoning output overlap parent categories and are not added again. These are
correlated native `last` usage measurements, not cumulative resume snapshots or
subscription invoices. P0 and the failed P3 attempt are included. The first
chapter's initial P1 was a cache miss; correctness and persistence passed anyway.

All sizes below are UTF-8 bytes. The full context bound includes input byte upper
bound, the 16,000 consumer/256 P0 output reserve and 2,048 safety margin, using the
configured profile capacity of 1,050,000. This is a conservative estimate, not a
provider token-count RPC. The observed native context window was 258,400, below the configured
capacity; every measured full bound fits that window as well. The implementation
now narrows subsequent consumer preflight to a smaller window reported by P0.
The initial admission uses the configured profile capacity until native usage
provides this observation. Raw usage is preserved in private evidence. Native thread, turn, scope IDs, retained source sizes and complete
per-call context objects are in [the committed evidence](validation-p0-p5-live.json).

| Call | Scope | Pass | Input | Cached | Output | Reasoning | Seconds | Source added | Suffix bytes | Full context bound |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | ch0001 | P0 | 5767 | 0 | 33 | 0 | 7.16 | 945 | 0 | 14860 |
| 2 | ch0001 | P1 | 7146 | 0 | 228 | 95 | 15.52 | 0 | 6416 | 37020 |
| 3 | ch0002 | P0 | 5770 | 0 | 33 | 0 | 7.28 | 935 | 0 | 14850 |
| 4 | ch0002 | P1 | 7186 | 5632 | 139 | 57 | 7.65 | 0 | 6492 | 37086 |
| 5 | ch0003 analysis | P0 | 5803 | 0 | 33 | 0 | 6.17 | 1056 | 0 | 14971 |
| 6 | ch0003 analysis | P1 | 7216 | 5632 | 316 | 171 | 16.09 | 0 | 6492 | 37207 |
| 7 | ch0001 | P2 | 6634 | 5632 | 131 | 71 | 9.58 | 0 | 3440 | 34044 |
| 8 | ch0001 | P3 | 6580 | 3328 | 257 | 173 | 16.49 | 0 | 3195 | 33799 |
| 9 | ch0001 | P3 | 6658 | 5632 | 270 | 186 | 16.55 | 0 | 3534 | 34138 |
| 10 | ch0001 | P4 | 6676 | 5632 | 108 | 48 | 11.78 | 0 | 3284 | 33888 |
| 11 | ch0001 | P5 | 6557 | 3328 | 117 | 33 | 9.75 | 0 | 2810 | 33414 |
| 12 | ch0003 chunk 1 | P0 | 5772 | 0 | 33 | 0 | 5.69 | 935 | 0 | 14850 |
| 13 | ch0003 chunk 1 | P2 | 6652 | 5632 | 265 | 129 | 11.98 | 0 | 3396 | 33990 |
| 14 | ch0003 chunk 1 | P3 | 6679 | 5632 | 120 | 44 | 9.52 | 0 | 3394 | 33988 |
| 15 | ch0003 chunk 1 | P4 | 6759 | 5632 | 95 | 43 | 9.13 | 0 | 3441 | 34035 |
| 16 | ch0003 chunk 1 | P5 | 6564 | 5632 | 74 | 0 | 8.16 | 0 | 2736 | 33330 |


The live run preceded final offline refinements to empty-thread recovery,
acceptance leases, maintenance generations, frozen-input checks phase timing, P1 reset reconciliation and archived physical accounting.
Those refinements were checked by the final offline suite without spending more
paid calls. Source-free wire format and fixed contract/schema were unchanged.

## Unrun cases and practical limits

Live profile/model switching was not run within the exhausted 16-call budget;
distinct slots, same scope and preserved checkpoints are covered offline. A live
hard kill during model generation was not run; live interruption was after durable
P4 acceptance, while genuine native turn interruption and lost-RPC recovery use
installed app-server plus token-free loopback inference. The split chapter's second
translation chunk and whole-book publication were not generated live; existing
export/publication regressions ran offline. Cache expiry was not tested by waiting
for an actual provider expiry; zero-cache acceptance is covered offline and an
actual live P1 cache miss was observed. No unattended live stress loop or concurrent
paid calls were run. Browser coverage is Chromium only. These limits are not claims
that the corresponding live experiments passed.

Recovery refuses uncertain submissions, surviving owners, missing private runtime,
foreign turns or compaction; it does not reconstruct native identity from a text
export. Restore complete private state or deliberately rebuild a new generation.
No provider prompt-cache hit ratio is guaranteed. Native state and authentication
must remain outside repository/web-served artifacts.

## Start a fresh test workspace

Use a new destination and source fixture, never an active book:

```bash
uv run intelitex import /path/to/new/html-fixture \
  --project /tmp/intelitex-new-book-unique --profile codex-sol-high
uv run intelitex profiles --project /tmp/intelitex-new-book-unique
uv run intelitex doctor --project /tmp/intelitex-new-book-unique
uv run intelitex analyze --project /tmp/intelitex-new-book-unique --profile codex-sol-high
```

Before authorized live analysis, configure that workspace's selected profile
`runtime_root` to a new external scratch directory and `options.auth_source`
to the explicitly authorized auth file. Import/profile/doctor do not submit a
model turn. Normal terminology review is required before translation. For the
exact bounded fixture/recovery reproduction use the explicit experiment command
above with a new scratch path; do not rerun against its completed project.
