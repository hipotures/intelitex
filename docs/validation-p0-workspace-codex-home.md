# Workspace-local Codex homes and P0 follow-up validation

Date: 2026-10-02. Branch: `work/p0-p5-persistent-sessions`.
Implementation baseline: `7cb8813`. Historical validation reports are unchanged.

## Runtime contract

The old layout was `<runtime_root or XDG_STATE_HOME/intelitex/codex>/<project UUID>/<chapter>/<full scope>/<full slot>/`.

The corrected canonical layout is:

```text
PROJECT/artifacts/<chapter_id>/codex-home/<scope_id[:12]>/<slot_id[:12]>/
  home/       CODEX_HOME
  sqlite/     CODEX_SQLITE_HOME
  work/       stable isolated cwd
  owner.lock
```

The user subsequently selected **short identifiers instead of full hashes**. Each path component is therefore 12 hexadecimal characters, while all full scope/slot/thread/session/P0 identities remain unchanged in persisted records. `runtime_layout_version=2`, `runtime_path_encoding=short-12`, and a private full-identity binding prevent short-name collisions from sharing a runtime. An earlier full-length workspace-local layout is also migratable. Legacy transports retain their existing behavior; `runtime_root` never relocates a new source-session-v1 runtime.

## Migration and confinement

`bookpipe/source_runtime.py` validates the project UUID, chapter inventory, immutable scope, compatibility digest, generation and full slot against the manifest's exact artifact location. Paths containing traversal or symlink directory components are rejected. After the user explicitly corrected credential sharing, the sole private-file symlink exception is `home/auth.json`, whose absolute target must exactly match the configured owner-private regular auth source. Every other native symlink remains forbidden. The old external root must agree with the original recorded profile, an explicitly supplied maintenance root, or an allowed default XDG root; a matching suffix alone is insufficient. Native thread paths must remain inside their bound home.

Migration holds a stable manifest maintenance lock and the old ownership lease, refuses live or unverifiable app-server owners, and copies into a deterministic private target sibling. This copy path is used on both same-device and different-device moves. It preserves and verifies file bytes and permissions, fsyncs files/directories, atomically publishes the destination, commits the manifest, and only then retires/removes the old slot. The journal, verified target marker and original directory device/inode support restart at partial-copy, complete-target, manifest-commit and old-retirement boundaries. Unknown, changed, linked or unowned state fails closed; no session is recreated.

Codex 0.160.0 paginated history resolves its selected immutable rollout through `state_5.sqlite`. Simply moving its directory would leave an unusable absolute path. The destination copy preserves the original index and any WAL/SHM in a private backup and rebases only `threads.rollout_path`, `threads.cwd` and matching `project_roots.path`. Other native files, thread/P0 IDs, rollout contents, checkpoints, selection evidence and usage remain unchanged. The selected native rollout can differ from the manifest after a lost revert acknowledgement; migration preserves that distinction for R1 reconciliation. Unknown native versions are refused rather than guessed.

Public protocol documentation was consulted first through the OpenAI developer documentation MCP. Native storage behavior was checked separately against matching Codex `rust-v0.160.0` source: [selected-rollout resolver](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/thread-store/src/local/thread_rollout_resolver.rs) and [thread state implementation](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/threads.rs). Migration itself starts no app-server or inference.

Acquire, cold resume, recovery and explicit non-retired maintenance use the migration mechanism. Offline CLI entry point: `uv run python translate.py source-sessions --project PROJECT --action migrate`. Read-only HTTP inventory does not migrate. Retired slots remain explicit maintenance evidence.

## Private state and frontend

Artifact enumeration prunes `codex-home` before descending, including archived artifacts, and avoids symlinks. P0 evidence readers reject private runtime paths. Static HTTP routes reject private runtime contents; API projections retain only reviewed public metadata. Existing repository ignore rules already cover `**/codex-home/`; runtime creation also installs a chapter-local `/codex-home/` ignore rule so projects inside other Git repositories are protected. Private homes/directories and auth files retain restrictive permissions. Configured auth is now linked directly to its explicitly authorized shared file, rather than copied into each native home. Atomic re-login updates become visible through the link, and native refresh writes reach the shared target. Under the slot lease and idle-owner proof, conversion preserves an older refreshed private copy in `home/.auth-before-shared-link.json`; the shared source is never overwritten by this conversion. Source-session app-servers explicitly use file credential storage. A partial link publication can resume without losing the backup; an interrupted copy may contain the exact authorized temporary auth link only inside its journal-owned staging directory. Migration copies the verified link itself, never its external target; cleanup/purge never follows it. Missing, public, linked, hard-linked or mismatched shared targets fail closed before inference.

The default P0 page now shows the same analysis units and order as P1, with IDs and word/UTF-8-byte counts. The workspace chapter column also counts the chapter's P1 preparation when analysis targets exist; future translation targets cannot turn an accepted analysis preload yellow. Translate-only sections retain their applicable P0 status. P0 completion uses semantic green rather than the configured model's palette color. Future translation fragments have a separate selectable list and progress denominator. Source scopes, prompts, model selection, global scheduling and whole-book P1/Review prerequisites are unchanged. Runtime stage evidence survives a long noisy activity tail, and session explanations are persistent selectable text. Direct navigation/reload of `/work/workspaces/<id>/preload` now serves the frontend.

A separate reported failure was reproduced with read-only diagnostics: required P1 terminology memory exceeded `memory_tokens`, before a model call. A fixed safe worker diagnostic now explains that failure instead of the generic fallback. In the Codex adapter the count is a conservative UTF-8-byte bound, not measured model tokens.

## Existing test runtime actually migrated

Exactly **one pre-existing native test runtime** was migrated using `migrate_project`, not recreated:

- Project: `/home/user/.local/state/intelitex-p0-p5-validation/p0-status-followup-20261002/tmp/native/test_installed_all_passes_one_0/project`.
- Old suffix: `private-native/3f18e6540d924ff49051880bdbb4e676/unit/beec115f8306f805fa599dd67882b78bd614d578b7e0a4afab53ba8d108758ca/bfdb6e46b5f5cc5c205afc8f720b712d0299fad436dbc4f5737b74c2d8e5278b/`.
- New project-relative runtime: `artifacts/unit/codex-home/beec115f8306/bfdb6e46b5f5/`.
- Thread and session ID before/after: `01a0fd8f-789b-7b72-98ca-a986759dffe5`.
- P0 turn before/after: `01a0fd8f-7905-7050-9f16-7c7359e6241a`.
- All **111 native entries** were preserved, with original index bytes retained in the private backup; **210 ordinary project files** were unchanged. The old runtime was removed after manifest commit. A second migration reported zero changes; a final idempotence check using the corrected module also reported zero, with the same original thread/session/P0 IDs. The direct CLI refused this minimal native fixture because it has no imported book; that attempt is not reported as a successful CLI migration. The fixture was migrated and checked through `migrate_project`.
- A fresh installed native app-server then successfully resumed that migrated thread and listed exactly its original P0 turn. No `turn/start` request was sent. The provider was confined to an unused loopback endpoint; cloud calls and model turns were zero.

The fixture had been created by the earlier token-free native lifecycle regression before the layout correction. It is a genuine native SQLite/rollout session, not a cloud-authenticated production session. Synthetic fixtures separately exercise refreshed dummy auth and WAL/SHM preservation. Proof is saved outside Git in `real-migration-proof.json` under the validation root above. No unrelated external runtime tree was scanned or deleted.

## Executed checks

All automated checks use fresh disposable test workspaces and private control homes, SQLite, XDG state and loopback ports. Resource scopes cap CPU at 50%, disable swap, and cap memory at 256 MiB for native checks, 512 MiB for Python and 768 MiB for browser checks. Two independently capped final suites can overlap; neither uses the live translation project. Browser-plugin tools were unavailable, so the installed Playwright/browser archive was used.

Final scratch data is under `/var/tmp/intelitex-p0-p5-final-3d88nmg1`, on a filesystem with available space. The frontend follow-up commands use isolated source snapshots there (shared dependencies, dependency synchronization disabled), so building its `web/dist` cannot replace the frontend being served from this feature worktree. Earlier frontend checks built the feature worktree's ignored `web/dist`; they were not server restarts. The results below are completed runs; earlier unsuccessful or deliberately interrupted runs are disclosed separately.

| Check | Result |
| --- | --- |
| `uv lock --check` | Passed; 35 packages resolved |
| `uv run python -m compileall -q bookpipe translate.py` | Passed again after the final auth crash-recovery change |
| `uv run --group dev python -m pytest -q` | 1926 passed, 0 failed/skipped, 3 warnings, 572.08 seconds; includes complete lifecycle and R1/R2 recovery |
| Focused migration/recovery/auth/security regression | 129 passed in 122.27 seconds; added copied-auth-link crash regression passed separately; all 44 runtime/migration cases and 86 source-session cases included in the final passing full suite |
| `node --test tests/*.cjs` | 31 passed, 0 failed/skipped |
| `npm --prefix web test` | Passed in isolated source snapshot; typecheck/build, 69 unit tests and 21 browser tests, 0 failed/skipped; final browser duration 249.07 seconds |
| Pre-existing native migration + cold history resume | Passed; 1 migrated, second migration 0, original thread/session/P0 retained, 0 model turns |

Final Python, compile, Node and frontend scopes recorded zero OOM events/kills. The final Python scope peaked at 512.0 MiB under its 512 MiB cap. Frontend memory reached its 768 MiB limit and completed successfully under reclamation; this is not reported as extra capacity. Logs, resource summaries, browser screenshots and migration/auth proofs are retained outside Git under `/home/user/.local/state/intelitex-p0-p5-validation/p0-status-followup-20261002`.

The focused migration tests cover new canonical paths, ignored legacy runtime roots, fresh-manager cold resume, old-layout migration/idempotence, five interruption boundaries followed by a fresh subprocess, actual cross-filesystem copying, active locks/processes, unverifiable owners, corrupt identity/path bindings, private symlink escapes, short-name collision refusal, old full-length local names and private HTTP/artifact exclusion. Native lifecycle/recovery tests include lost revert acknowledgement, interrupted/failed cleanup, selection rejection/restart and saved-candidate verification.

The first full Python attempt failed when the shared filesystem ran out of space; it is **not** counted as passed. Only completed disposable workspaces from this validation were removed to recover space; its log and the migration fixture were retained. The first frontend run passed 69 unit tests and failed three browser assertions that inspected the workspace shell before the asynchronous pipeline snapshot arrived. Those checks now wait for the expected snapshot before asserting; all assertions remain. A second frontend run passed 69 unit tests and 20/21 browser tests; its remaining Review test froze a pipeline snapshot before the review draft was available. The fixture now captures that snapshot after reading the draft. A later Python run returned 1906 passed / 3 failed: one new diagnostic fixture lacked its required workspace directory (fixed), and two native loopback tests timed out. A focused run returned 248 passed / 1 failed when local Codex schema generation exited unsuccessfully during the low-disk interval; the same recovery test subsequently passed using the new scratch filesystem. The focused migration/security run returned 39 passed / 2 failed due to a new test reading section DTOs from the wrong application boundary (fixed to use the actual web projection). The next full Python run returned 1912 passed / 1 failed: an existing supervisor test observed ownership and attempted a conflicting start in separate calls, allowing legitimate cleanup to release ownership between them. The observation and assertion now hold the existing supervisor lifecycle mutex; the production supervisor and all assertions are unchanged. These unsuccessful runs are retained in logs and are not reported as passing. A pre-auth full rerun then passed all 1913 tests. The first auth-focused run returned 127 passed / 1 failed because its new probe referenced the wrong client class name (fixed); the second passed 129 tests. A subsequent full run was deliberately interrupted only in its verified private test cgroup after an additional auth-link publication crash case was added; it is not counted as passed. That additional crash case passed separately, and the complete Python command is rerun after the final change, with final results above.

Unrun: paid/cloud inference, real cloud OAuth expiry/refresh, cloud cache-hit measurements, migration of authenticated production native sessions, power-loss/kernel filesystem fault injection, and native storage versions other than audited Codex 0.160.0. Simulated process-loss boundaries and actual filesystem copying are tested; they are not claimed to prove every hardware failure.

## Native auth-link verification before implementation

At the user's request, the link was first probed against installed `codex-cli 0.160.0` in a fresh private directory using synthetic API keys only. Native `login status` read credentials through the link; native `login --with-api-key` updated the shared target without replacing the link; a subsequent fresh process read the updated credentials. No user credentials, cloud/model endpoint or inference was used. The permanent regression additionally distinguishes credentials before/after atomic replacement by their synthetic suffix. Proof of the preliminary probe is in `auth-link-probe.json` under the validation root.

Public [credential storage documentation](https://learn.chatgpt.com/docs/auth#credential-storage) was consulted first through the documentation MCP. File behavior was then checked against matching [Codex 0.160.0 native auth storage](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/login/src/auth/storage.rs): its file backend reads the home auth path and writes through it. This local probe verifies file reads/writes, not a real cloud OAuth refresh.

Follow-up regressions cover shared-target replacement, native writes retaining the link, interrupted link installation, preserving old refreshed auth during migration, migration of a previously linked home, missing/untrusted targets, purge leaving the shared target intact, and fresh native cold resume retaining the same thread/session/P0 with zero new source transmission.

## User-requested read-only audit of ch0011

The user requested inspection of `artifacts/ch0011` in workspace `w-1b3404cbe23d433aa81b6e479d41d316`. No mutation, app-server connection, SQLite connection, inference or service action was performed for this audit. The saved manifest, attempt metadata/usage, verified post-P1 baseline and selected rollout header were read, and filesystem permissions were checked without printing auth contents or source text.

- Runtime: `artifacts/ch0011/codex-home/5f023190a31a/131163a941d3/`, layout 2 / short-12; private complete-identity binding matches the manifest.
- P0 and P1 thread/session: `01a0fdc1-dacf-7781-856c-88faeed48add`; original P0 turn: `01a0fdc1-db29-7303-b604-1c32429e4eea`.
- P0 reported 16546 input / 3328 cached-input tokens; P1 reported 23270 input / 16384 cached-input tokens (70.4%), 5217 output / 144 reasoning-output tokens. These are persisted measurements, not a new model test.
- P1 added zero source-message UTF-8 bytes, retaining the original 41739-byte source envelope. Both selection records verify `gpt-6-astra` / high effort. The saved post-P1 native baseline contains exactly the original completed P0 turn.
- Manifest state is `baseline_ready`, P0 is ready, cleanup is false and process owner is null. Home, SQLite and cwd are real 0700 directories; the observed private auth copy and owner lock were 0600. Native DB/WAL/SHM are present; their file modes are 0644 within the private 0700 directory. No permissions or active files were changed by the audit.

This audit occurred before the auth-link correction: that active home still held a regular private auth copy. It was not converted during read-only inspection. The new code converts configured auth at the next safe session acquisition or offline maintenance; the audit is not evidence that a running workspace was modified. These checks found no mismatch in the observed state. They do not constitute an active native/database integrity probe. A persistent thread alone does not guarantee a provider cache hit ([official prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)); this chapter's saved P1 measurement demonstrates an actual cache read.

## Explicit operational exceptions requested during this task

There was no merge or service deployment/restart by the agent, and the production `main` checkout was not modified. Local frontend builds and the final isolated build are disclosed above. The original blanket service/workspace prohibition was later overridden for these specific actions:

1. On the explicit request to stop the server, SIGTERM was sent to verified PID `2147742`, serving port `8780` from this feature worktree. Its normal shutdown path also interrupts active workers; it did **not** wait for P1 to finish. The process exited and the listener disappeared. The agent did not issue SIGKILL and did not restart the server.
2. After the user reported a new startup failure, read-only API/job/manifest/configuration diagnostics identified the P1 memory limit. The user then explicitly requested `500000`; under the project lock, only `memory_tokens` was changed from `12000` to `500000` in workspace `w-1b3404cbe23d433aa81b6e479d41d316`. All other settings were verified unchanged. No model run was started by the agent.

Consequently, this report does **not** claim that the running service and workspace were entirely untouched. The actions above are the bounded user-requested exceptions; no accepted translation results were edited or regenerated.

## Changed files

- Runtime placement/migration: `bookpipe/source_runtime.py`, `bookpipe/source_sessions.py`, `bookpipe/application/operations.py`, `bookpipe/cli.py`.
- Evidence/API protection: `bookpipe/artifacts.py`, `bookpipe/server/asgi.py`, and the confined P0 evidence reader.
- P0 projections/progress and safe failure reporting: `bookpipe/application/source_preload.py`, `bookpipe/application/workflow.py`, `bookpipe/runtime/{models,protocol,registry,worker}.py`, `bookpipe/server/{serialization,service}.py`.
- Frontend: `web/src/api/schema.ts`, `web/src/features/pipeline/{PreloadPage,Workspace}.tsx`, `web/src/styles/production.css`.
- Regressions: `tests/test_source_runtime.py`, `tests/test_source_sessions.py`, `tests/test_source_preload.py`, `tests/test_runtime.py`, `tests/test_web_production.py`, `web/tests/{preload,analysis-reset,recovery}.test.mjs`.
- Documentation: both P0 PRDs and this new validation report.

The user subsequently requested a normal commit and push on the feature branch, then explicitly required shared auth links and native link verification before implementing that correction. No merge into `main` is authorized.
