# Executed production web verification

## Work-home read cost (2026-09-23)

The Work page now loads only active workspaces and reads compact backend pipeline
summaries for its prepared cards. The Archive drawer loads archived workspaces only
when opened. A disposable, model-free profile with three 230,000-word books (two
archived) measured 0.069 s for the full list and 0.023 s for the active list. With
1,000 synthetic physical-attempt manifests, the full pipeline projection took
0.238 s and the compact summary 0.027 s under `cProfile`. These are local fixture
measurements, not timings of the user's server or books.

| Executed command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 908 passed, 3 dependency/fixture warnings. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm run test:unit && npm run lint && npm run build` (`web/`) | 19 unit tests passed in 7 files; lint, strict TypeScript and production build passed. Vite reported its existing uncompressed chunk-size warning. |
| `FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu npm run test:browser` (`web/`) | 10 browser tests passed, including production same-origin offline workflow and Work card request assertions. No unexpected console/page errors in checked scenarios. |
| Same browser environment with `npm run test:visual` (`web/`) | 12 Work comparisons against unchanged v33 passed; maximum difference 0.3901% at light 390 px, below the 1% budget; zero console errors and no horizontal overflow. |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and implementation contract untouched. |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual source/route review and blob update. |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed. |

No active translation workspace, live model provider or non-loopback server was
opened. The tested browser flow shows no `/pipeline` request from Work cards; the
16-second idle interval also re-fetches only compact summaries and the active list.
The full endpoint remains available on workspace detail pages. The Library source chip
now says **active workspace** because Work's initial list no longer includes the
archive. The original v33 asset remains unchanged.

## Source inspection correction (2026-09-22)

Inspect now displays bounded real excerpts from distributed reading-order files.
The interface identifies file counts, sampled words, and local language detection
as limited observations. It no longer presents package filenames as chapter titles
or the stopword-score margin as a probability. No model or user workspace was used.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q tests/test_web_production.py -x` | 47 passed, 1 deliberate duplicate-ZIP warning. |
| `uv run --group dev python -m pytest -q tests/test_server_api.py` | 504 passed. An earlier combined run was interrupted by SIGTERM without a test-failure report; both suites passed separately. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm run typecheck && npm run lint && npm run test:unit && npm run build` (from `web/`) | Passed; 13 frontend unit tests. Vite retained its >500 kB uncompressed chunk warning. |
| `FONTCONFIG_FILE=/tmp/intelitex-playwright-libs/fonts.conf LD_LIBRARY_PATH=/tmp/intelitex-playwright-libs/root/usr/lib/x86_64-linux-gnu node --test tests/library-setup.test.mjs tests/library-rows.test.mjs` (from `web/`) | 2 passed; no unexpected console/page or network errors, no mobile horizontal overflow. The Save-retry scenario intentionally injects HTTP 503. |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual inspection and source-hash update. |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and contract untouched. |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed. |

The original v33 has no Library Inspect drawer. The production drawer was compared
against v33's right-drawer width, spacing and mobile behavior. Captures at
`/tmp/intelitex-library-setup-evidence/inspect-dark-1440.png`,
`inspect-dark-390.png`, and `inspect-light-390.png` were visually reviewed.

## Library setup/Prepare change (2026-09-22)

This focused follow-up used disposable pytest sources/workspaces and a fully
intercepted Chromium origin. It did not start or stop the user's server and made no
model-generation request. The Library card, Inspect, Cancel, Save, second workspace,
unknown Save acknowledgement across reload, unprepared Workspace and explicit
Prepare control were exercised in the browser. The test injected one expected HTTP
503 for Save; there were no unexpected console/page errors or failed requests.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 831 passed, 3 warnings on final full run. An earlier run had 1 timing-sensitive failure in `test_concurrency_conflict_and_isolated_cancellation`; that test passed alone and the full rerun passed. |
| `uv run --group dev python -m pytest -q tests/test_web_production.py tests/test_server_api.py` | 549 passed, 1 deliberate duplicate-ZIP fixture warning. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm run typecheck && npm run lint && npm run test:unit && npm run build` (from `web/`) | Passed; 13 component/unit tests. Vite reported its existing >500 kB uncompressed chunk warning. |
| `FONTCONFIG_FILE=/tmp/intelitex-playwright-libs/fonts.conf LD_LIBRARY_PATH=/tmp/intelitex-playwright-libs/root/usr/lib/x86_64-linux-gnu node --test tests/library-setup.test.mjs tests/library-rows.test.mjs` (from `web/`) | 2 passed; offline browser interaction and responsive Library paging. |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; immutable v33 and implementation contract hashes unchanged. |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual source re-audit. |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -v` | 31 passed. |

Original v33 was rendered separately at 1440×1000 and 390×844. The production
Work capture retains the header, list, cover grid and responsive geometry; it shows
one offline source and no seeded workspaces. Setup is an intentional additional
dialog absent from v33. Dark desktop/mobile and light mobile setup captures were
visually inspected in `/tmp/intelitex-library-setup-evidence/`; no mobile horizontal
overflow appeared. The source-cover/derived SQLite index item in issue #1 remains
separate from this focused setup flow.

Run on 2026-09-22 in `/home/user/DEV/intelitex`, with `uv 0.12.17`, Python 3.14,
Node 22.22.1 and Playwright Chromium 153.0.8010.12. Fixture workspaces, sources,
registry and offline provider live under `/tmp/intelitex-browser-*` or pytest's
temporary directories. No active user workspace or live model provider was used.
The Browser plugin was unavailable; Playwright used its installed headless Chromium.

| Reproducible command from repository root | Observed result |
| --- | --- |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 143,061 bytes/SHA-256 `c50fe95eb761ae332171759cfb2ba95fcc996baaa98d9a1471a570f6f54a26a5`, contract 92,586 bytes/SHA-256 `5cb7d0ed9520517d8e2a6f29239b0d0f217b7e2ea189d748b81f035c4503af91` |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | Passed, `baseline_matched` for manually audited symbols and source blobs |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -v` | 31 passed |
| `uv run --group dev python -m pytest -q` | 825 passed, 3 warnings (2 dependency deprecations and a deliberate duplicate-ZIP fixture) |
| `node --test tests/*.cjs` | 31 passed |
| `npm --prefix web run typecheck` | Passed with strict TypeScript |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run test:unit` | 13 passed in 4 files; stream ordering/isolation, Unicode ranges, UI primitives |
| `npm --prefix web run build` | Passed; production assets emitted into ignored `web/dist` |
| `npm --prefix web run test:e2e` | 5 passed; full offline workflow, real Vite proxy/origin/SSE, bounded Library pagination/Refresh/packed EPUB Prepare, 1,000 sections and 2,000 terms at 100 events/s |
| `(cd web && node --test tests/library-rows.test.mjs)` | 1 passed; offline Chromium verified complete initial rows at 7/5/2 columns, row completion after resizing and scrolling, bounded Refresh, and zero console errors; no server process |
| `npm --prefix web run test:visual` | 12 unmasked Work comparisons passed; 6 widths × dark/light, maximum differing pixels 0.3282% at pixelmatch threshold 0.15 |
| `CHROMIUM_PATH=/home/user/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell uv run --with playwright python tests/browser_review_smoke.py` | Passed legacy Review browser regression |
| `uv run --with playwright python .agents/skills/intelitex-web/scripts/web-smoke-test.py --base-url http://127.0.0.1:40203 --ui-path /work --ready-selector '.book-card' --fixture --browser-executable /home/user/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell --output-dir /tmp/intelitex-guarded-smoke-final-2` | Passed read-only API/browser smoke, zero browser/HTTP/console errors |

Browser commands used `FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf` and
`LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu` in the
shell environment because the sandbox lacked system Chromium libraries/fonts.
The smoke URL was a disposable loopback server, not a durable installation. The
production E2E asserted zero unexpected console/page/HTTP errors. The recovery test
excluded the browser's expected offline network error and asserted no other console
or page error. The Library test intentionally injected one HTTP 503 and asserted no
other console/page/HTTP failure. It held the page idle for 16 seconds to cover the
healthy polling interval and verified that hover, source open/return and packed EPUB
Prepare did not rescan Library. Guarded smoke asserted zero console, page, HTTP and
request failures.

Visual evidence is in `/tmp/intelitex-visual-evidence` and
`/tmp/intelitex-browser-evidence`. The unmasked automated comparison covers matched
Work data at 1440×1000, 1920×1080, 1024×1000, 820×1000, 720×1000 and 390×844
for both themes; the v33 bytes/CSS were never edited. Work geometry (64 px header,
cover alignment/size, cards and responsive overflow) was asserted. Original and
production Workspace, drawer, Review, Prepare, Analyse, Translate, Publish, Reader,
Archive and Settings captures were manually compared, including 390 px mobile Review,
Reader, Archive and Settings. Their data differs by design: the
mock uses seeded fiction and simulated figures; production displays real fixture
state, checked counts, available usage and publication results. Reader controls and
safe Settings also add supported production behavior. Mobile widths had no page
horizontal overflow. The optional build warning about a 500 kB uncompressed JS
chunk remains; its compressed size is about 152 kB.

The security/API matrix in `tests/test_server_api.py` ran against both compatibility
HTTP and production ASGI adapters. It covered every new route with foreign Host,
Origin and Fetch Metadata, mutation body framing/size/unknown fields, encoded path
segments, secret redaction, SSE replay and historical event sanitization. Production
tests additionally covered CSP, static route isolation, duplicate JSON keys,
nonfinite numbers, draft archive revision, preserved dormant evidence, approval
freshness, idempotency receipts and publish-only download currency. `git diff --check`
passed. No mock state, timer-based progress, CLI-output state parser or project truth
in localStorage appears in production code.

Source limitations are documented in [intentional differences](differences.md).
No live provider or active translation workspace was accessed.

## 2026-09-23: chapter boundaries, same-workspace Prepare rebuild, P1 visibility and F/T/E response

The current change used disposable pytest projects and offline browser route fixtures;
no model was contacted and no active translation workspace was mutated. A read-only
inspection of the affected source and workspace confirmed sparse TOC entries, numbered
`h4` chapter headings and no persisted P1 attempt. The user's running server was not
started, stopped or restarted by these checks.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 851 passed; 3 existing dependency/ZIP-fixture warnings |
| `uv run --group dev python -m pytest -q tests/test_web_production.py -k 'd02_membership or p1_attempt_manifest or reprepare'` | 12 passed; 52 deselected |
| `node --test tests/*.cjs` | 31 passed |
| `npm --prefix web run test:unit` | 15 passed in 5 files |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run build` | Typecheck and production build passed; existing >500 kB chunk advisory |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf npm --prefix web run test:browser` | 8 passed, including workflow, SSE, Library, setup, and rebuild/F/T/E; zero unexpected console/page/request errors |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf node --test tests/reprepare.test.mjs` (from `web/`, after mobile CSS change) | 1 passed; no page/console/request errors or page horizontal overflow |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and supplied contract hashes unchanged |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual source re-audit |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed |
| `git diff --check` | Passed |

Offline Chromium captures are in `/tmp/intelitex-reprepare-evidence/` at dark
1440×1000 and light 390×844. Manual comparison with the original v33 Prepare page
found matching page/panel geometry and typography; the prepared page intentionally
adds a guarded rebuild control, while the fixture's live source/metadata differs
from the mock's seeded book. The mobile source table scrolls inside its card so
column headings remain separate without widening the page. The browser test also
confirmed an acknowledged F/T/E change appears in under 1.2 seconds while two
authoritative background reads were delayed by 3.5 seconds.

## 2026-09-23: targeted Processing reconciliation and visible Prepare rebuild

The F/T/E response path now refetches only the selected workspace's resources and
marks the global workspace list stale without refetching it immediately. Complete
workspace ID matching prevents `w-1` from also invalidating `w-10`. The pipeline
HTTP projection uses one validated `book.json` read and reuses its usage/plan
snapshot for section, metadata and model
provenance. On a disposable synthetic 32.07 MB book, a local pipeline read measured
0.488 s after this change, versus about 0.85 s before it; the PATCH measured 0.685 s.
These are isolated local measurements, not timings from the user's running server.
The server process was left untouched, so it must be restarted by its owner to serve
the new backend and built frontend. No active workspace was mutated and no model was
contacted.

The Prepare phase now places the guarded rebuild action at the right edge of the
Checks card, with a divider and filled primary button style. Offline Chromium
captures at dark 1440×1000 and light 390×844 in
`/tmp/intelitex-reprepare-evidence/` were inspected against the original v33 Prepare
page. Its page geometry remains aligned; the rebuild action is an intentional
production addition. The mobile page has no horizontal overflow, and its source
table scrolls within the card.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 853 passed, 3 existing dependency/ZIP-fixture warnings |
| `node --test tests/*.cjs` | 31 passed |
| `npm --prefix web run test:unit` | 16 passed in 6 files |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run build` | Strict TypeScript and production build passed; existing >500 kB chunk advisory |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf node --test tests/reprepare.test.mjs` (from `web/`) | 1 passed; button style, alignment and spacing, responsive capture, no console/page/request failures |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and contract unchanged |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual audit |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf npm --prefix web run test:browser` | 8 passed; offline workflow, same-origin integration, Library, SSE, responsive and Prepare rebuild; no unexpected console/page/request failures |
| `git diff --check` | Passed |

The browser test asserts the rebuild button's primary style, right alignment and
spacing from the description on desktop, and captures the complete mobile page.

## 2026-09-23: Prepare row preview and immediate Processing feedback

This change used source code and disposable offline fixtures only. It did not read or
mutate the user's workspace directories, contact a provider, or start/stop the user's
server. The Browser plugin was unavailable, so the repository's Playwright-based
offline browser suite exercised the real production bundle. The flow was Workspace
→ choose F/T/E → Prepare → choose source row → inspect the opening text.

The Prepare table and preview were checked at dark 1440×1000 and light 390×844.
Captures are in `/tmp/intelitex-reprepare-evidence/prepare-dark-1440.png` and
`prepare-light-390-full.png` beside it. Compared with the immutable v33 Prepare
capture, the phase header, metric cards and panel styling remain consistent; the
table is intentionally narrower, the selected row drives a new adjacent preview,
and metadata/checks follow that preview. At 390 px, Section, Content type,
Processing and P1 remain visible without page or table horizontal overflow.
The P1 cell says `in` for membership rather than showing a completion check.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q tests/test_web_production.py::test_preview_and_progress_are_application_facts tests/test_server_api.py::test_pipeline_reuses_one_validated_book_for_sections_metadata_and_usage` | 4 passed in disposable projects |
| `node --test tests/*.cjs` | 31 passed |
| `npm --prefix web run test:unit` | 18 passed in 7 files, including UTF-8 boundary tests |
| `npm --prefix web run lint` | Passed |
| `npm --prefix web run build` | Strict TypeScript and production build passed; existing >500 kB chunk advisory |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf node --test tests/reprepare.test.mjs` (from `web/`) | 1 passed; lazy read, row selection, 1-KiB bound, HTML escaping, immediate saving intent, conflict rollback, rebuild and responsive layout |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf npm --prefix web run test:browser` | 8 passed; offline integration and responsive smoke |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 unchanged |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched`; no backend code or API contract changed |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed |
| `git diff --check` | Passed |

The browser suite reported no unexpected page, console or network errors. The
Processing conflict test deliberately returns one HTTP 409, which Chromium logs;
the UI displays the conflict and restores the server-confirmed mode. Canceled GETs
from query reconciliation are expected and ignored only for the known workspace and
pipeline paths. Source preview text comes from the existing read-only section API;
only its first 1 KiB of UTF-8 is rendered. The test did not run Analyse or any model.

## 2026-09-23: Processing controls in Prepare and guarded P1 reset

Processing F/T/E is edited in Prepare beside the selected source excerpt; the
workspace overview and its section drawer display the saved value read-only. The
Prepare row and page scroll positions remain stable when a section is selected.
Analyse shows an explicit empty state before P1 and offers a confirmed, revisioned
reset of saved P1 when the Python application finds no dependent work. This run used
only disposable pytest projects and intercepted/offline browser fixtures; no active
workspace, running server, or model provider was used.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 903 passed, 3 existing dependency/ZIP-fixture warnings, including Host/Origin and request-body checks for the new routes. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm --prefix web run lint` | Passed. |
| `npm --prefix web run test:unit` | 18 passed in 7 files. |
| `npm --prefix web run build` | Strict TypeScript and production build passed; existing >500 kB chunk advisory. |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf node --test --test-concurrency=1 tests/*.test.mjs` (from `web/`) | 9 passed; offline workflow, real Vite proxy/SSE, Library, Prepare F/T/E, and P1 reset. Browser assertions found no unexpected console/page/request errors or mobile page overflow. |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and supplied contract unchanged. |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after manual source re-audit. |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed. |
| `git diff --check` | Passed. |

The default parallel browser run first passed 8 of 9 tests; the Library setup
fixture recorded one failed `compatibility` request during that run.
That test passed alone and all 9 passed when run serially. At 1440 px dark and
390 px light, the Prepare captures in `/tmp/intelitex-reprepare-evidence/` were
compared with `/tmp/intelitex-visual-evidence/original-phase-prepare-dark-1440.png`.
The header, metrics, typography and card treatment remain aligned with v33. The
narrower structure table, adjacent real excerpt, editable F/T/E, metadata/checks
below, and Rebuild action are intentional differences. The injected 409 conflict
is visible in the test capture; it verifies that a rejected Processing change
returns to the authoritative saved value. The mobile capture has no horizontal
overflow. Empty Analyse evidence is in `/tmp/intelitex-analysis-reset-evidence/`.

## 2026-09-23: bounded Ctrl-C HTTP drain and stable Prepare preview

A disposable loopback server with an open SSE stream and a deliberately slow HTTP
read reproduced the reported `Cancel 1 running task(s)` traceback under the former
one-second Uvicorn drain. With a 30-second bounded drain, the read completed, SSE
closed, the process exited 0, and stderr stayed empty. Existing offline worker
shutdown tests still verified supervised cancellation and registry cleanup. No
active user workspace, running server, or model provider was used.

The Prepare preview now keeps the same panel height while changing sections or
waiting for source text; long excerpts scroll within it. Browser assertions check
that metadata stays at the same document position before selection, during a
delayed read, after a short excerpt, and after changing back to a long excerpt on
mobile. Workspace overview shows only F/T/E rather than duplicating each letter
with its full name; the full meaning remains available to assistive technology.

| Command | Result |
| --- | --- |
| `uv run --group dev python -m pytest -q` | 904 passed, 3 existing dependency/ZIP-fixture warnings. |
| `node --test tests/*.cjs` | 31 passed. |
| `npm --prefix web run test:unit` | 18 passed in 7 files. |
| `npm --prefix web run lint` | Passed. |
| `npm --prefix web run build` | Strict TypeScript and production build passed; existing >500 kB chunk advisory. |
| `LD_LIBRARY_PATH=/tmp/intelitex-browser-libs/root/usr/lib/x86_64-linux-gnu FONTCONFIG_FILE=/tmp/intelitex-browser-libs/fonts.conf node --test --test-concurrency=1 tests/*.test.mjs` (from `web/`) | 9 passed; zero unexpected console/page/request errors. |
| `uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py` | Passed; original v33 and contract unchanged. |
| `uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .` | `baseline_matched` after inspecting the ASGI timeout and runtime documentation. |
| `uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -q` | 31 passed. |

The Browser plugin was unavailable; the repository's Playwright tests used its
offline Chromium. Dark desktop and light mobile Prepare captures were inspected at
`/tmp/intelitex-reprepare-evidence/` against the immutable v33 Prepare capture in
`/tmp/intelitex-visual-evidence/`. The compact preview is an intentional production
addition; header, metric cards, typography, and card treatment remain aligned.
The test capture includes an intentionally injected 409 Processing conflict.
A workspace screenshot with the compact Processing letter is at
`/tmp/intelitex-browser-evidence/workspace-dark-1024.png`.
