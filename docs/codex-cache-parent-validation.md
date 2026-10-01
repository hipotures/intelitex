# Cache-parent deployment verification

Verified on installed `codex-cli 0.159.3`, starting repository HEAD
`32aa538059697898eb87c6954ff71d3e42761ac2`. No live model calls, Codex changes,
global configuration changes or server restart were performed.

## Executed checks

```bash
uv run pytest -q tests/test_codex_parent.py tests/test_codex_cache_shared_v2.py \
  tests/test_codex_cache_shared_v2_app_server.py tests/test_codex_effort.py \
  tests/test_transports.py tests/test_pipeline.py tests/test_profile_wire_defaults.py
uv run pytest -q
uv lock --check
python3 -m compileall -q bookpipe experiments
git diff --check
```

The focused suite passed **302 tests**. The final full Python run passed
**1,681 tests**, with **10 pre-existing failures** and three warnings in
194.84 seconds. A full run of an isolated `git archive` export of the starting
HEAD passed 1,655 tests and failed 11. Every final failure also failed on that
export; the additional baseline Ctrl+C test failure did not recur in the final
run. Existing unrelated assertions were not weakened to obtain a green result.

These exact final failures predate the feature:

```text
tests/test_application_baseline.py::test_cli_command_inventory_and_shared_flags
tests/test_codex_cache.py::test_profile_resolution_records_both_wire_formats
tests/test_codex_cache.py::test_existing_project_profiles_use_cache_default_and_respect_explicit_rollback[None]
tests/test_codex_cache.py::test_existing_project_profiles_use_cache_default_and_respect_explicit_rollback[canonical]
tests/test_codex_cache.py::test_existing_project_profiles_use_cache_default_and_respect_explicit_rollback[cache-v1]
tests/test_codex_cache.py::test_existing_project_profiles_use_cache_default_and_respect_explicit_rollback[cache-v2]
tests/test_codex_cache.py::test_existing_project_profiles_use_cache_default_and_respect_explicit_rollback[cache-shared-v1]
tests/test_codex_cache_shared.py::test_options_fail_closed_and_profiles_default_to_shared
tests/test_codex_cache_v2.py::test_global_shared_default_and_p1_v2_rejected
tests/test_p1_compact.py::test_p2_p5_canonical_fingerprint_fixtures
```

They concern the already changed CLI inventory, old wire-default expectations
and stale fingerprint fixtures. Baseline-only failure:
`tests/test_cli_entrypoint.py::test_ctrl_c_closes_sse_without_traceback`.
Local raw logs and the machine-readable failure-set comparison are retained in
`/tmp/intelitex-cache-parent-verification/`; the starting-HEAD full-suite log is
`/tmp/intelitex-cache-parent-baseline-32aa538-tests.log`.

Also executed in `web/`:

```bash
npm run test:unit
npm run typecheck
npm run lint
```

All 16 unit-test files / 64 JavaScript tests passed; typecheck and lint passed.
Browser tests were not run, following the user's earlier constraint.

The installed-protocol audit passed. Native app-server integration tests use
only an unauthenticated loopback Responses mock. They verify cold-runtime
rollout import, one inference per attempt, distinct ephemeral sibling identities,
the exact accepted P2 cutoff, inherited native cache routing, no previous
assistant answers, complete canonical artifacts, independent retries, safe
fallback and unchanged effort/model selection. These tests do not measure
production provider cache hits or latency.

## Effective salvation-03 activation

Resolved through the actual `ProviderPool`, including `web.config.json` pass
overrides. Compared with the same read-only resolution under starting HEAD:
models, efforts, profile names, context sizes, timeouts and wire values are
unchanged. Settings/web-configuration byte hashes remain unchanged; no external
project file was edited.

| Pass | Profile | Provider | Model | Effort | Wire | Thread strategy |
|---|---|---|---|---|---|---|
| P1 | codex-astra-high | codex | gpt-6-astra | high | cache-shared-v2 | independent root (unchanged) |
| P2 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | p2-parent-ephemeral-fork-v1 |
| P3 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | p2-parent-ephemeral-fork-v1 |
| P4 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | p2-parent-ephemeral-fork-v1 |
| P5 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | p2-parent-ephemeral-fork-v1 |

The book inherits the new global execution default. A normal server restart
reloads it; no project migration or rerun of accepted work is needed. Missing
or incompatible historical parent evidence causes a complete fresh-root
fallback. P1 never uses this translation lifecycle.
