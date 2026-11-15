# Intelitex production web interface

React, strict TypeScript, Vite, Tailwind CSS 4, source-owned UI primitives, TanStack
Router/Query and one shared EventSource. The Python application owns all durable state.
The immutable original v33 supplies visual tokens and interaction structure.
Settings → Debug shows stable panel IDs; [the ID map](../docs/web/debug-ids.md) links
each ID to its source component. This preference stays in the local browser.

From the repository root (Node 22.12+ and `uv`):

```bash
uv sync --locked --group dev
npm --prefix web ci
npm --prefix web run build
uv run intelitex serve --workspace-root /path/to/workspaces --import-root /path/to/sources
```

Open `http://127.0.0.1:8780/work`. The single Python FastAPI/Uvicorn server serves
`web/dist`, API, SSE and vetted current EPUB downloads. Production needs no Node
server. Do not use ASGI reload or multiple workers against one supervisor/registry.
Sources are supported HTML/XHTML directories, extracted EPUB packages or packed `.epub`
files, not uploads.
Settings → Library can show a **Project Gutenberg** checkbox before *Sort by* in the Work
Library header. It lists only books whose OPF `dc:identifier` or `dc:source` names
`gutenberg.org` (never the file name), which keeps documentation screenshots free of
books with unclear rights. The option and the checkbox state are browser preferences. Library loads at least 12 sources, rounded up to complete rows
at the current grid width, then more as the user scrolls. Refresh explicitly
restarts discovery from the first page.

For frontend development, keep that Python server running and run
`npm --prefix web run dev`. Vite binds `127.0.0.1:5173`, proxies `/api` to port 8780,
and rewrites only that exact development Origin. Host/Origin/Fetch Metadata checks
remain enforced by the backend. Vite preview alone is not a production application.
The earlier `bookpipe.web` health-only adapter remains a compatibility module; it is
not the product server.

```bash
uv run --group dev python -m pytest -q
node --test tests/*.cjs
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run test:unit
npm --prefix web run build
npm --prefix web run test:e2e
npm --prefix web run test:visual
npm --prefix web run screenshots   # regenerates docs/images, see the root README
```

Browser tests need Playwright Chromium and its system libraries/fonts. They launch
a disposable Python server with an in-process offline provider and temporary projects.
They never use the user's translation workspaces or contact a real model service.
`test` runs unit/component and browser suites; `test:browser` aliases `test:e2e`.
`test:visual` compares the current Work page (six widths × dark/light, Inter embedded,
reduced motion, settled-state waits) with the approved images in `web/tests/visual-baseline/`
at an unmasked 1% pixel budget, and runs the 12 P0 preload layout checks. Every comparison
runs before failures are reported together; a missing baseline is a failure. The suite never
writes baselines: `INTELITEX_VISUAL_CANDIDATES=1` also saves each screenshot as a candidate
under the evidence directory, and adopting one is a reviewed copy into `visual-baseline/`.
`test:visual:v33` is a separate, explicitly invoked diagnostic against the historical v33
mockup (`INTELITEX_V33_REFERENCE=<path>`, recoverable from commit `b0c40df`); the current UI,
not v33, is authoritative, so it is not part of the standard suite.
`ui:snapshot` guards CSS refactoring beyond the approved Work baselines: `record` drives the
offline pipeline once and saves its API responses, `capture <dir>` replays them against the built
`dist/` for 29 representative views (Workspace, phases, Review, Reader in three themes, dialogs and
drawers, desktop and mobile) and stores screenshots plus every computed style, and
`compare <before> <after>` reports differences. A behavior-preserving change shows none.
Artifacts go under `$INTELITEX_TEST_EVIDENCE_ROOT` (default: the system temp directory; `intelitex-browser-evidence`,
`intelitex-visual-evidence`, `intelitex-visual-v33-evidence`).

See [contract map](../docs/web/contract-map.md), [verification](../docs/web/verification.md)
and [intentional differences](../docs/web/differences.md). Do not copy mock execution
or localStorage project state into the interface.
