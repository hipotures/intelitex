# Executed production web verification

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
