# Architecture and source map

> Current product authority: D02, D03, D04, D05 and D08 were explicitly approved by the task owner on 2026-09-22. See [approved decisions and provenance](decisions-and-provenance.md). Historical baseline limitations below are implementation history, not unresolved product policy. Current bindings and validation are in `docs/web/contract-map.md`.

B: inspected at `2bb9aa2df178355502a7529e460fca12804ef377`. The primary code/doc sources are:
`bookpipe/server/http.py`, `server/service.py`, `server/serialization.py`,
`application/workflow.py`, `runtime/`, `docs/server-api.md`, `docs/server-runtime.md`.
Read the corresponding current files, not only this summary.

## Ownership

```text
Browser / React
  typed HTTP client + query cache + one EventSource per tab
      |
Single serve process
  HTTP delivery adapter -> application queries / short commands
  JobSupervisor + registry + broker
      | worker A -> workspace A / one imported book
      | worker B -> workspace B / one imported book
```

A workspace is a root-confined project identified by a directory ID, not a remote
VM or a separate HTTP server. One book/project contains ordered sections, analysis
units, translation chunks and checkpoints. A job is one operation on a workspace,
not the book or the whole pipeline. Aliases resolving to one project share exclusion.

The main server currently supports import, analysis, translation, publication,
short Review operations and Reader queries/mutations. Long work runs in separate
processes; HTTP requests and SSE do not own their lifetimes. There is no GPU/provider
scheduler: concurrency support does not guarantee a provider can handle every load.

Project `state.sqlite3`, validated receipts, immutable attempt artifacts and atomic
JSON remain domain truth. Separate runtime jobs/events SQLite is supervision/history,
not another checkpoint database. Keep read snapshots short, read-only and request-local.
No provider construction or schema migration from ordinary status queries.

`ProjectReadScope`/`ReadStore` provide read-only checkpoint snapshots. `WorkflowQueries`
assembles the pipeline/settings read models. Review checks revision while holding
its application project lock. API marker mutation uses `.reader.lock` per request.
Standalone Reader retains its lifetime lock, so concurrent API marker writes can
legitimately return `workspace_busy`; don't bypass it to make a test pass.

Dependency direction: HTTP -> application -> domain/ports. Framework request objects,
HTTP statuses, SSE syntax, React state and HTML do not belong in core application logic.
Use deliberate public serializers, not arbitrary dataclass dumps exposing paths.

## Current versus intended stack

Production `serve` now uses `server/asgi.py::ASGIServer` (FastAPI/Starlette + Uvicorn),
serving vetted `web/dist` assets and deep SPA routes alongside the existing API/SSE.
`server/http.py` remains a compatibility adapter for regression coverage, not a
second production server. Both use `server/routes.py::dispatch`. One supervisor,
one ASGI worker, no reload owner. See `docs/web/contract-map.md` for current evidence.

Agreed frontend: React + strict TypeScript + Vite + Tailwind 4 + shadcn primitives
+ TanStack Router/Query under `web/`. Reuse the actual package manager/lockfile.
Specialized Reader CSS is appropriate. Do not substitute Next.js or a Node backend.
Vite may run in development; production serves the build and API on one origin.
No blanket CORS/Host exceptions to make development work. Test the real proxy's
forwarded Host/Origin and unbuffered SSE. API 404 and asset 404 must not become SPA HTML.

## Preservation requirements

Keep standalone CLI/Review/Reader compatible until explicitly retired. Do not remove
working reading/marker/context behavior because v33's Reader is a placeholder.
Preserve the user's configured Codex transport/auth flow; no demand for an OpenAI
API key merely because a UI profile was selected. No live generation during tests.
