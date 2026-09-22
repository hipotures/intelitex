# Web development foundation

`web/` is an independent npm workspace for React, strict TypeScript, Vite,
Tailwind CSS 4, shadcn source-owned primitives, TanStack Router and TanStack Query.
It currently renders an empty root route. There are no product pages or API calls.
Use Node.js 22.12+ (Node 22 LTS is used in CI), npm, and the repository's `uv` environment.

## Install and run

From the repository root:

```bash
uv sync --locked --group dev
npm --prefix web ci
npm --prefix web run dev
```

Vite listens on `http://127.0.0.1:5173` and proxies `/api` to
`http://127.0.0.1:8000` without rewriting paths. In another terminal:

```bash
uv run --locked uvicorn bookpipe.web:create_app --factory --host 127.0.0.1 --port 8000
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:5173/api/health
```

Both requests return `{"status":"ok"}`. This is process liveness only, not
project/provider readiness. The scaffold starts no supervisor or workers, opens
no projects, and adds no CORS middleware. Both development servers bind to loopback.
The existing Reader, Review and workspace servers remain separate and unchanged;
the proxy deliberately does not target them.

## Build and validate

```bash
uv lock --check
uv run --locked python -m compileall -q bookpipe translate.py
uv run --locked --group dev python -m pytest -q
node --test tests/*.cjs
npm --prefix web run typecheck
npm --prefix web run build
cd web
npx playwright install --with-deps --only-shell chromium
npm test
```

The browser installation is needed once per Playwright version. On Linux,
`--with-deps` may require administrative access to install system libraries;
omit it when those libraries are already installed. `npm test` builds and uses
Node's existing test runner plus Playwright to load the production bundle in
Chromium, verify the empty React route, check computed Tailwind CSS, and reject
browser errors or unexpected API requests. CI runs these checks too. No separate
Python linter/typechecker was configured in the repository at bootstrap time.

`npm --prefix web run preview` serves the built files on `127.0.0.1:4173` for local
inspection. `dist/`, `node_modules/`, and TypeScript build metadata are ignored.
Production hosting is deferred; Vite preview is not a production server.

## Configuration and boundaries

- `src/main.tsx` owns the single `QueryClient` and providers. `src/router.tsx`
  defines only `/` using code-based routing; no route generator is needed yet.
- `vite.config.ts` and `tsconfig.json` both resolve `@/` to `src/`.
- `src/styles.css` uses the official Tailwind 4 Vite integration and restricts
  source scanning to `src/`. No Intelitex theme or design tokens are defined.
- `components.json` configures shadcn's CLI; `src/lib/utils.ts` supplies `cn`.
  Class variance, class merging, icons and animation prerequisites are installed.
  No UI primitives have been generated. `new-york` / `neutral` are CLI registry
  defaults only; `cssVariables: false` avoids requiring an unimplemented token
  theme. Revisit these generation choices when designing the first components.

Read-only shadcn configuration verification (from `web/`):

```bash
npm exec --yes --package=shadcn@4.21.0 -- shadcn info
```

This checks framework, Tailwind and alias discovery without generating components;
the CLI is an on-demand development tool, not a production dependency. The config
also validates against its linked official JSON schema.

Future dependency direction:

```text
Browser → React → HTTP / future SSE → FastAPI adapter
                                      → application services
                                      → domain / infrastructure / runtime
```

`bookpipe/web.py` is an inert ASGI factory at the delivery edge. Future routes
must decode HTTP input into application commands and call public application
services composed through `bookpipe.bootstrap.create_application`. Framework
request/response objects must never enter application, domain or pipeline code.
FastAPI supplies Starlette transitively; Uvicorn is the minimal ASGI runner.

Reader migration, existing server integration, supervisor endpoints, SSE,
authentication, production serving and all product UI are deliberately deferred.
See [architecture](../docs/architecture.md) for the maintained application boundary.

Setup references: [Tailwind Vite integration](https://tailwindcss.com/docs/installation/using-vite),
[shadcn configuration](https://ui.shadcn.com/docs/components-json),
[TanStack code-based routing](https://tanstack.com/router/latest/docs/framework/react/routing/code-based-routing).
