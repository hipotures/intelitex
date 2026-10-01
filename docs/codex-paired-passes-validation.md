# Paired-pass deployment verification

Starting main HEAD: `3191fb870ba9c76cbb7148ba3554570ec4f41909`.
Installed Codex audited: `codex-cli 0.160.0`.

The supported native path is `thread/resume` with `threadId`, an unchanged copied
native `path`, `excludeTurns=true`, and the ordinary model/base/developer/config
and sandbox overrides. Exact fields were checked in the installed experimental
JSON schema and matching `rust-v0.160.0`
[ThreadResumeParams](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs#L354)
and [thread processor](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/request_processors/thread_processor.rs#L557).
The [official app-server documentation](https://learn.chatgpt.com/docs/app-server#api-overview)
was consulted first for the supported public lifecycle. No Codex patch/build or
provider header is involved; no exact patch-version eligibility rule was added.

## Activation

Registry before: `fresh-root`, `p2-parent-ephemeral-fork-v1` (default).
Registry after: `fresh-root`, `p2-parent-ephemeral-fork-v1`, `paired-passes-v1`
(default). Both wire defaults remain `cache-shared-v2`.

Actual salvation-03 resolution includes its web assignment overrides, not just
settings.json. Both before/after configurations were resolved read-only using
ProviderPool against the starting repository export and the modified repository.

| Pass | Profile | Provider | Model | Effort | Wire | Strategy |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | codex-astra-high | codex | gpt-6-astra | high | cache-shared-v2 | independent P1, unchanged |
| P2 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | paired-passes-v1 |
| P3 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | paired-passes-v1 |
| P4 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | paired-passes-v1 |
| P5 | codex-sol-high | codex | gpt-6.1-sol | high | cache-shared-v2 | paired-passes-v1 |

No external project files required edits: the strategy inherits the central
missing-option default. settings.json and web.config.json byte hashes remained
identical, as did model/effort/profile/wire selection. No project Store was
opened for writing; accepted artifacts, review state and checkpoints were not
modified. No server restart or translation was performed. A normal restart
loads the new central defaults without data migration.

## Verified behavior

The installed app-server ran against an unauthenticated local Responses mock,
with private per-attempt homes and no book data. Actual outgoing requests prove:

- P3 preserves P2 thread/session/cache identity and sees its accepted conversation.
- P4 starts a fresh root; P5 preserves that P4 identity.
- P4/P5 have no conversational P2/P3 turns or assistant answers.
- Parents stop before continuations start in new runtimes; native snapshot bytes
  stay unchanged across retries and repeated reconstruction.
- Failed P2/P4 never become parents. P3/P5 retries restore the accepted snapshot,
  containing no failed child answer; every attempt has one provider inference.
- Canonical audit/ledger dependencies remain explicit even after quote repair.
- Missing, incompatible, old-wire or incomplete evidence falls back without
  rerunning accepted upstream work. Valid older persistent v2 roots can resume.
- Extra history is included in preflight. If the pair cannot fit the configured
  conservative bound, it falls back to the complete standalone task.
- Usage is per call from .last; cumulative .total is separate. Restored-parent
  usage cannot masquerade as missing current-turn telemetry.
- Requested models/efforts remain authoritative, including native trusted effort
  updates. Provider-level request effort is not inferred from baseline intent.

The cache-shared-v2/shared codec files and codex_effort.py are byte-identical to
starting HEAD. All three strategy selections produce identical codec bodies;
canonical fingerprints/recovery behavior and non-Codex transports are unchanged.
P1 remains outside the paired lifecycle.

## Executed checks

All checks below were executed offline;
no paid/live model request or browser test was made.

- Installed skill audit: `python3 /home/user/.codex/skills/codex-app-server/scripts/audit_installed_protocol.py` passed.
- `uv run pytest -q tests/test_codex_pair.py`: 44 passed before adding the final
  restored-usage regression; that additional test passed separately (1 passed).
- `uv run pytest -q tests/test_codex_pair.py tests/test_codex_parent.py tests/test_codex_cache_shared_v2.py tests/test_codex_cache_shared_v2_app_server.py tests/test_codex_effort.py tests/test_profile_wire_defaults.py tests/test_transports.py tests/test_pipeline.py`: 346 passed.
- `npm run test:unit` (web/): 16 files, 64 tests passed.
- `npm run typecheck` and `npm run lint` (web/): passed.
- `uv lock --check`: passed, 35 packages.
- `python3 -m compileall -q bookpipe experiments`: passed.
- `git diff --check`: passed.

Full Python baseline was executed using a `git archive` export of starting HEAD,
with the same installed Codex and Python environment: 10 failed, 1653 passed,
28 skipped, 3 warnings. The 28 installed-app-server tests were skipped by the old
0.159.3-only test guard; the new tests run against installed 0.160.0 instead.

Final `uv run pytest -q`: **10 failed, 1726 passed, 3 warnings in 238.45s**.
The failure IDs are exactly the same as baseline: zero new failures, zero skipped
tests. All 45 new paired tests and 28 newly enabled installed-app-server tests
passed. Baseline took 169.38s. No unrelated assertions were weakened to get green.

Existing baseline failures (not weakened or edited to pass):

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

Execution logs, before/after configuration resolution and byte snapshots are
retained under `/tmp/intelitex-paired-verification`. No translation quality,
latency gain or real provider cache percentage has been measured in this task.
