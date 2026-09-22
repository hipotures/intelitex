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
files, not uploads. Library loads at least 12 sources, rounded up to complete rows
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
```

Browser tests need Playwright Chromium and its system libraries/fonts. They launch
a disposable Python server with an in-process offline provider and temporary projects.
They never use the user's translation workspaces or contact a real model service.
`test` runs unit/component and browser suites; `test:browser` aliases `test:e2e`.
Visual tests read the unchanged original v33, render matching test-only data, and
assert an unmasked 1% pixel budget for the stable Work fixtures at six widths/two themes.
Additional screen screenshots are manually reviewed against the original.
Artifacts are under `/tmp/intelitex-browser-evidence` and `/tmp/intelitex-visual-evidence`.

See [contract map](../docs/web/contract-map.md), [verification](../docs/web/verification.md)
and [intentional differences](../docs/web/differences.md). Do not copy mock execution
or localStorage project state into the interface.
