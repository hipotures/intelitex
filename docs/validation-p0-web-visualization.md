# P0 web visualization validation

Date: 2026-10-02. Branch: `work/p0-p5-persistent-sessions`.
Implementation base: `f7649b8`; recovery architecture includes `4bed8f2`.

## Delivered behavior

The workspace has six workflow tiles and eight section columns, including P0.
P0 opens before any attempt or saved P1 plan and displays exact locally planned
source ranges, inherited model/effort assignments, source previews, readiness,
and physical P0 accounting. Source-only Run prepares one target, stopping before
P1–P5. Query projections do not instantiate providers, discover Codex, persist a
plan, create a session, reconcile a native thread, or manufacture evidence.

User follow-up corrections are included:

- Main P0 rows show chapter/range, word count and UTF-8 source-text bytes.
  Hashes and consumer IDs are in folded session diagnostics. These byte counts
  describe source text, not billed tokens or the complete serialized request.
- Rows say “For analysis”, “For translation”, or “Shared by analysis and
  translation”. “Preload source” executes P0 only. Different source ranges or
  model/effort configurations still require distinct compatible baselines.
- A chapter with no targets has an applicability explanation instead of an
  unexplained dash. Contents has no special exemption: eligible source is
  included according to the chapter's processing mode and inherited provider.
- P0 may run for any chapter. P1 retains its preceding-unit dependency. After
  source-only preload, the initial P1 page can start just its first unit without
  a pre-existing saved plan; subsequent units use the existing P1 controls.
- All manual P2–P5 controls require completed whole-book P1 and Review approval.
  The application checks whole-book P1 before provider construction, including
  when a stale approval flag exists. Preview navigation stays available.
- Global execution remains lazy: chapter 1 P0/P1, chapter 2 P0/P1, then Review,
  then chapter 1 P2/P3/P4/P5, chapter 2 P2/P3/P4/P5. Compatible earlier P0 is
  reused; a different translation source/configuration can require another P0.

Readiness describes saved accepted evidence and native verification on use. It
does not promise cache residency. Cache metrics report measured provider usage;
the loopback fixtures report zero cached tokens, so this validation establishes
session reuse and accounting, not a real provider cache hit.

## Isolation and resources

All work and commits stay in `/home/user/DEV/intelitex-p0-p5`. No production main
checkout, listener, service, active translation workspace, or native home was
opened, changed, restarted, or deployed. No push or merge was performed.

Private validation root:
`/home/user/.local/state/intelitex-p0-p5-validation/web-visualization-20261002`.
Its scratch, service state, evidence, control Codex home and SQLite home are
separate from production. Native fixtures use disposable per-test homes and
loopback Responses servers with authentication disabled. No configured user
authentication was copied. Cloud inference calls: **0**.

Heavy checks run sequentially through private `run_check.py` and
`resource_runner.py` wrappers in `systemd-run --user --scope` units:

- Backend/native: `MemoryMax=256M`, `MemorySwapMax=0`, `CPUQuota=50%`.
- Complete Python suite retry: `MemoryMax=512M`, same zero swap and 50% CPU.
  The accumulated full-suite process exceeded the 256 MiB cap; separate native
  and recovery regressions retain the established 256 MiB cap.
- Build/browser: `MemoryMax=768M`, `MemorySwapMax=0`, `CPUQuota=50%`,
  `NODE_OPTIONS=--max-old-space-size=256`.
- `TMPDIR`, `XDG_STATE_HOME`, `CODEX_HOME`, and `CODEX_SQLITE_HOME` point into
  the private root; `OPENAI_API_KEY` is empty.
- Playwright uses the already installed isolated browser cache and private
  extracted browser libraries/font configuration. No shared installation or
  production browser/server was required. The Browser plugin was unavailable,
  so the existing Playwright harness was used.

Logs and per-command cgroup peak/event counters are saved as `NAME.log` and
`NAME-resources.json` in that root. Evidence stays outside Git. Only completed
scratch directories created by this validation or its earlier recovery run were
removed for disk space; no application workspace was used as scratch.

## Final executed checks

Commands use this worktree's locked `.venv` and npm dependencies. Each row's log
name refers to the private validation root above; the wrapper supplies limits and
isolated environment variables rather than changing the command's test selection.

| Command | Actual result | Evidence |
| --- | --- | --- |
| `.venv/bin/python /home/user/.codex/skills/codex-app-server/scripts/audit_installed_protocol.py` | Passed for installed Codex CLI 0.160.0; startup/schema/initialize/thread-start/skills checks; no model turn or authentication. | `audit.log` |
| `npm ci` in `web/` | 280 packages installed, 281 audited, zero reported vulnerabilities. Existing ESLint deprecation warning; no dependency upgrade. | `npm-ci.log` |
| `.venv/bin/python -m pytest -q --basetemp=…/tmp/backend-release` | **1878 passed**, 3 warnings, **507.87 s**. Complete final Python regression. | `backend-release.log` |
| `.venv/bin/python -m pytest -q tests/test_source_sessions.py tests/test_source_preload.py --basetemp=…/tmp/native-release` | **133 passed**, **152.24 s**, under the separate 256 MiB native cap. Includes all R1/R2 and new P0 command/read/ordering tests. | `native-release.log` |
| `.venv/bin/python tests/preload_web_fixture.py --output …/p0-browser-fixture.json` | Passed. Fresh real application DTOs from exactly **2 native-loopback P0 turns** for two different inherited model configurations on one source; 200 input/0 cache/0 reasoning/10 output tokens and `$0.0003 · partial` recorded estimate. Cloud calls: 0. | `fixture-release.log`, `p0-browser-fixture.json` |
| `npm run build` in `web/` | TypeScript and Vite production build passed. Existing >500 kB chunk warning. | `build-final-ui.log` |
| `npm run test:unit` in `web/` | **69 passed** in 17 files. | `unit-final.log` |
| `npm run lint` in `web/` | Passed; no lint errors or baseline lint exceptions. | `lint-final.log` |
| `npm run test:browser` in `web/` | **20 passed**, zero failures/skips, **233.208 s**, using the final regenerated native-backed DTO fixture. | `browser-release.log` |
| `npm run test:visual` in `web/` | **12 implemented P0/P1 comparisons passed**, 36 captures at 1440/1024/390 px × dark/light × debug on/off, Chromium 153.0.8010.12. Legacy v33 comparisons explicitly **unrun**. | `visual-release.log`, `evidence/intelitex-visual-evidence/report.json` |
| `node --test --test-concurrency=1 tests/*.cjs` | **31 passed**, zero failures/skips. | `node-release.log` |
| `.venv/bin/python -m compileall -q bookpipe translate.py` | Passed. | `compile-release.log` |
| `uv lock --check` | Passed; 35 locked packages resolved, no lockfile change. | `lock-release.log` |
| `git diff --check` | Passed, including a final check after completing this report. | `diff-release.log` and final staged-diff check |

The Python warnings are two dependency deprecations in FastAPI/Starlette/AnyIO
and the deliberate duplicate-ZIP-member fixture warning. There were no failed or
skipped Python tests in the complete run.

Final cgroup measurements (MiB, rounded to one decimal):

| Check | Memory cap | Peak | `memory.events max` | OOM / OOM kills |
| --- | ---: | ---: | ---: | ---: |
| Full Python regression | 512 | 512.0 | 2686 | 0 / 0 |
| Native/session/recovery regression | 256 | 256.0 | 2214 | 0 / 0 |
| Native-backed browser fixture export | 256 | 110.1 | 0 | 0 / 0 |
| Final production build | 768 | 625.8 | 0 | 0 / 0 |
| Frontend unit tests | 768 | 371.1 | 0 | 0 / 0 |
| Lint | 768 | 179.4 | 0 | 0 / 0 |
| Browser regression | 768 | 768.0 | 145 | 0 / 0 |
| Visual regression | 768 | 450.1 | 0 | 0 / 0 |
| Node regression | 256 | 68.7 | 0 | 0 / 0 |

`max` events reflect memory pressure/reclaim at the configured cap; they are not
OOM kills. Every final scope exited zero and reported zero OOM events. All used
`cpu.max = 50000 100000` and zero swap. The earlier dependency installation used
a 640 MiB cap, peaked at that cap, and reported 771 max events with zero OOM.
Completed full-suite scratch was removed after its logs and metrics were saved
to recover disk space; the native fixture JSON, screenshots and result logs remain.

## Acceptance evidence

| Requirements | Evidence |
| --- | --- |
| UI-01–04 | `preload.test.mjs`: rail/columns, pristine page, draft shell, archived/busy/offline navigation, selected chapter, reload/back/forward, stable selection and focus. |
| UI-05–08 | Actual P0/P1 pages compared at 1440, 1024 and 390 px, dark/light, debug on/off; five metrics, shared panels and scroll behavior, source language, escaped source, keyboard labels. No P0 glossary/Clear panel. |
| DATA-01–02 | Pure planning versus subsequently saved execution plan, Unicode long-block splits, saved-plan precedence, nonlocal unresolved planning with known translation targets. |
| DATA-03–05 | Compatible aliases deduplicated; separate effort/model/source targets; full/translate/excluded/legacy/mixed eligibility; saved consumers avoid unnecessary P0; accepted coverage survives complete P5. |
| DATA-06–07 | Accepted P0 plus cleanup warning; missing/corrupt/foreign receipt, source, manifest and history; generation mismatches and ambiguous native contracts are localized as unknown, never green. |
| SAFE-01–03 | Repeated inventory/pipeline/preview snapshots, provider/manager/subprocess/discovery forbidden; both adapters validate targets/pages; evidence paths reject symlinks/traversal; foreign Store result rejected before reading. DTOs whitelist safe fields. |
| USAGE-01–05 | Real native-loopback P0 ledger exported into browser fixtures; two configurations for one source, physical deduplication, applied versus requested identity, historical/unassigned usage, partial/missing prices and currencies, measured zero, failed/unknown/unsubmitted attempts and cache writes. |
| RUN-01–04 | Targeted later-chapter P0 without P1; accepted reuse without another turn; HTTP request receipts, lost reply, reload and double click; stale assignment/mode/prompt/archive/generation/hold refusal; worker recovery after completed P0 before acceptance. |
| RUN-05 | Two-chapter installed-runtime tests for global P0/P1 ordering and manual later-chapter P0, refusal of out-of-order P1, refusal of P2–P5 after only one P1 even with stale approval, Review gate, then ordered P2–P5. Maximum twelve loopback turns per scenario. |
| RUN-06 | Full `test_source_sessions.py` regression: R1 lost revert acknowledgements, failed suffix cleanup; R2 durable selection refusal/recovery; acceptance-boundary crashes, interruption, lost start reply, cold resume, Stop/reload and checkpoint reuse. |
| LIVE-01–03 | Both adapters correlate nested/standalone P0 by job/chapter/scope/slot; cleanup does not light P0; numeric pass zero survives metadata projection; duplicate/out-of-order/foreign events and provisional-to-persisted usage converge without summation on every tick. |
| COMPAT-01–02 | Complete Python/Node/frontend suites plus lint/build and browser coverage of P1/reset, translation, Review, Reader, publishing, model assignments, source inspection and compact Work queries. See actual results and unrun checks below. |

## Review corrections and development failures

A second review checked the final diff after the user's model switch. It added
confinement before Store result reads, refused ambiguous current native contracts,
fixed preview positioning after asynchronous data loading, and addressed manual
translation prerequisites and the P0 labels/source-size feedback above.
It also checked reported model/effort against the durable verification receipt,
kept a targetless selected chapter from falling back to another chapter's source,
and prevented equal chapter titles from merging historical chapter totals.
Public P0 physical-attempt IDs also use opaque identifiers instead of internal
relative artifact paths; accepted-attempt references use the same identifiers.

Intermediate checks exposed and corrected mutable-versus-read Store usage,
an unnecessary executable lookup on GET, an uncommitted fixture transaction,
missing panel debug metadata, stale browser selectors, an asynchronous pagination
assertion, and a test assertion using the wrong cache field name. The ordering
assertions already passed in that last failing run; the cache assertion is now
`cached_input_tokens`. These intermediate runs are not reported as final passes.
Visual harness fixes distinguish the Settings footer Close button and the hidden
mobile Live status from visible controls. Production connection behavior was not
changed to satisfy those selectors.

An earlier build was interrupted with exit 143 during the user/model interruption;
it did not supply resource metrics. It was retried successfully under 768 MiB.
Earlier 640 MiB builds had memory reclaim events without OOM; the final web cap
was raised to 768 MiB. No OOM is inferred from the interrupted command.

The first final complete Python run (`backend-final`) stopped at about 49%.
The wrapper saw SIGTERM and did not write final resource counters. Inspection of
its own `intelitex-p0-web-backend-final.scope` established `Result=oom-kill` and
`MemoryPeak=268460032`; this was the validation scope's 256 MiB cap. That run is
incomplete and is not counted as passing. The full suite was retried with the
bounded 512 MiB limit rather than removing resource isolation.

A combined delivery command was also interrupted with exit 143 after its frontend
unit/lint checks and nineteen browser cases, without a browser summary or resource
report. Its browser scope did not report OOM. The final sequence runs in a separate
test process with a saved PID and log so a long tool-command lifetime does not
interrupt the bounded child scopes. This interrupted sequence is not counted as a
complete browser pass.

The first 512 MiB retry (`backend-complete`) was deliberately interrupted at
608 passing tests after the final P0 public-ID correction. The process had already
imported the earlier code. Only its exact private pytest PID was signalled; fixture
cleanup completed, with exit 2 and zero cgroup OOM events. `backend-release` runs
the complete final code afresh; the interrupted subset is not its substitute.

## Unrun checks and limits

- **Live/cloud model and real cache-hit validation: not run**, as prohibited by
  this task. Native-loopback tests use the installed Codex executable and mock
  provider responses; they do not spend model tokens.
- The immutable v33 HTML and `.agents/skills/intelitex-web` verification scripts
  are absent from this worktree. Legacy v33 pixel comparisons and those external
  skill integrity/API-baseline/unit scripts were **not run**. The visual command
  explicitly reports this limitation and still checks implemented P0/P1 pages.
  Set `INTELITEX_V33_REFERENCE` to the approved asset to enable the old pixel
  comparisons. No reference was fabricated or fetched from production.
- Production smoke tests, active-book migration, deployment, restart and merge
  were not performed. Validation uses synthetic workspaces only.
