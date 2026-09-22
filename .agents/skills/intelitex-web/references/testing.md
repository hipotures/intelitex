# Validation and completion evidence

The skill's tools are verification helpers, not a replacement for implementation
acceptance tests. No script starts jobs, imports a book, approves terms, runs a
model, changes the production server, or claims missing functionality is present.

## Scope-specific checks

From the repository root, using the existing uv environment:

```bash
uv run python .agents/skills/intelitex-web/scripts/verify-mockup.py
uv run python .agents/skills/intelitex-web/scripts/check-api-contract.py --repo .
uv run python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -v
```

verify-mockup checks both original v33 and the supplied handoff prompt against
pinned byte lengths and SHA-256, including manifest consistency. It never updates
them. Exit 0: matched; 1: content mismatch; 2: invalid/missing input.

check-api-contract checks recorded source blob identities only. Exit 0: inspected
files match the recorded source snapshot; 3: changed/missing, manually re-audit;
2: malformed configuration/path. Changed source isn't proof of a bug. Matching
source isn't proof of API correctness or current test success. Do not auto-refresh
baseline hashes to make this green.

## Read-only API smoke

Start an isolated server fixture using the repository's existing fixture tooling,
with temporary state and no live model configuration. The helper does not start it.
Then supply its actual loopback origin:

```bash
uv run python .agents/skills/intelitex-web/scripts/web-smoke-test.py \
  --base-url http://127.0.0.1:8780
```

It GETs health, capabilities, workspace list, job snapshot, and profile list. It
checks response types/basic shape and does not follow redirects. A passed API-only
report explicitly says the browser was not requested. It is not frontend validation.
Non-loopback requires explicit --allow-non-loopback; do not target production by default.

## Guarded browser smoke

When a real frontend exists, use a ready selector observed in that implementation,
not an invented selector. The following `.app` example must be replaced if the actual
root differs. A visible root alone is only startup smoke, not functional completion.

```bash
uv run --with playwright python .agents/skills/intelitex-web/scripts/web-smoke-test.py \
  --base-url http://127.0.0.1:8780 \
  --ui-path /work --ready-selector '.app' --fixture \
  --output-dir /tmp/intelitex-browser-smoke-unique
```

Use the already established Playwright environment when available; `uv run --with`
is a fallback without modifying project dependencies. A Chromium installation is
required; pass --browser-executable with its real path if not using Playwright's
installed browser. Missing browser/dependency is a failure, never a passing skip.
The output directory must be new. Reports/screenshots are local test artifacts,
not public fixtures containing book text or credentials.

The helper uses a fresh browser context, blocks service workers, external requests
and methods other than GET/HEAD, checks the explicit ready selector, console errors,
page errors, HTTP errors and request failures, then closes the context/browser. It
performs no clicks or mutations. Any page trying a mutation on mount fails the guard.
This means it is appropriate for read-only startup screens, not a full Review prepare
or translation action test. Write tests belong to the repository's isolated E2E suite.

## Required validation for actual web changes

For every visual/behavior change, run a real browser test of affected interactions
and inspect console/page errors plus network failures. Verify focus/caret, empty/error/
dirty/conflict states and dark/light/mobile behavior. A screenshot of original v33
only confirms reference rendering, not production implementation.

Run affected Python tests, all existing JS tests, frontend typecheck/lint/build,
component tests and relevant E2E. Before a complete UI delivery run the full regression:

```bash
uv run --group dev python -m pytest -q
node --test tests/*.cjs
```

Inspect actual package scripts for frontend commands; do not claim guessed npm targets
exist. Browser mocks and offline providers are allowed for deterministic fixtures;
no live Codex/LLM quota and no active user translation directory.

Required scenario groups: two independent workspaces; start response lost; isolated
Stop and cleanup; global P1 then Review/approval; stale revision and competing writers;
P2-P5 checkpoint resume; automatic publish versus retry; out-of-order/replayed/pruned
SSE; existing-reader marker lock; Unicode canonical offsets; provenance/unknown usage;
archive/import/config gaps kept honest; no dummy success; security/path guards.

Visual comparison: original v33 and production in the same browser/fonts at 1440x1000,
1920x1080 and 390x844, dark/light. Compare affected Work/Workspace/drawer/phase/Review/
Reader/settings/archive states. Use deterministic fixtures; mask only genuine dynamic
fields, not entire components. Keep the original untouched. Review differences manually;
don't approve new snapshots merely to silence a failure. See supplied contract section 20.

## Skill activation checks

Positive prompts: implement Intelitex Review, debug stale SSE status, fix v33 section
layout, map an Intelitex API payload, review marker offset handling.
Negative prompts: a different website, GPU shopping, standalone mathematical research,
translation prompt experimentation unrelated to web behavior.

The YAML/frontmatter/link checks validate package structure, not probabilistic Codex
activation. Verify discovery with /skills or an explicit $intelitex-web in the user's
Codex session. Do not claim such a session was tested if only local packaging ran.

## Report truthfully

Record command, environment, observed result/count, and failure or unavailable scope.
Distinguish reference browser test, helper fixture test, actual backend regression,
and production E2E. Report missing test infrastructure instead of repeating previous
278/31 results as a current run. Commit/push claims require real Git evidence.
