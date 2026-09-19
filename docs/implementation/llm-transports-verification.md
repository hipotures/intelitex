# LLM transports implementation verification

Date: 2026-09-19 UTC

## Installed Codex protocol

The required no-model-turn audit was run from the installed `codex-app-server`
skill against `/home/user/.local/bin/codex`.

- CLI: `codex-cli 0.155.1`
- audit result: passed, no failures
- strict-config startup: passed
- initialization, skills list/disable/re-list, and no-turn thread start: passed
- disabled feature flags recognized by the installed binary: 24
- generated experimental schema directory used during development:
  `/tmp/intelitex-codex-schema.t2pRKq` (development-only, not a runtime dependency)

Relevant SHA-256 identities:

| Schema | SHA-256 |
| --- | --- |
| `v1/InitializeParams.json` | `6f0094be9a65242ec779a40794cbd4fdfa32fca1e45084a16adfb50501d33ea2` |
| `v2/ThreadStartParams.json` | `25f490368ec6df52a2a3b82a5469d2413307eb93439121b309f415b5648eee7a` |
| `v2/TurnStartParams.json` | `b36fb37326b1cf69f75c8b306f1f886d53a57c4b1b985e08e298e2407ea2ad02` |
| `v2/ThreadTokenUsageUpdatedNotification.json` | `aba4f6c7e4a19b2b842c08ee793b57000c07dafd57b922ad0d8e7c76609108c2` |
| `v2/TurnCompletedNotification.json` | `78af2a37391e8e669a4020cb58593e4d3e378756ced79d5fec72374fa69fb94b` |
| `ClientRequest.json` | `ef13a122e4fb0296b377d571fc26dc4bb7d85736364a65530a87d13fd84234a2` |
| `ServerNotification.json` | `49a787f49a8ba46034fbea8664f4cbfe10211abd57e34b8281430a7dc033b42b` |

Intelitex passes the audited argv directly, retains strict validation, sets a
custom non-empty base instruction, places the unchanged pass prompt in
`developerInstructions`, creates a new persisted thread for each attempt, and
places only the serialized source payload in the user turn. It does not use
`thread/resume` or invent an output-token parameter for app-server.

## Reference repository

The public reference was shallow-cloned over HTTPS to the unique temporary path
`/tmp/intelitex-any-item.V2QSA5` at commit
`3a47aecbfa1095164b881693cba62fb7e15216d6`.

The requested adapter, AI abstraction, model catalog loader/validator, and all
files in `src-tauri/resources/ai-model-families/` were inspected. A full content
and filename search covered the checkout, `scripts/`, and `.github/workflows/`.
That fetched commit contains versioned YAML catalogs and catalog validation, but
no model/pricing refresh script or workflow. Intelitex therefore implements an
explicit validated, staged-by-the-caller, atomic `catalog-import` operation. It
does not claim to reuse an updater absent from the inspected commit and has no
runtime dependency on the temporary clone.

## Deterministic verification

`uv run pytest -q` passes 75 tests. The suite includes the existing import,
review, P1, P2-P5, checkpoint and recovery tests plus native Responses SSE and
Codex JSON-RPC fixtures. The new fixtures cover exact pass-schema compilation,
token preflight, structured output, interleaved notifications and RPC responses,
unsupported server requests, multiple/late usage snapshots, commentary versus
final output, persisted rollout copying, malformed SSE, incomplete/refusal
terminal states, credential redaction, catalog rollback, and SQLite-backed
settings migration. It also fault-injects preflight/midstream evidence write
failure, Codex retry/system-error/compaction states, and stalled process-group
cleanup.

## Authorized live Codex checks

The user explicitly authorized reuse of the user's Codex authentication for
these checks. Only `auth.json` was copied into each unique private runtime; no
user config, skills, plugins, memories, rules or trust settings were copied. The
runtime copy was removed after process cleanup. No OpenAI API key or direct
OpenAI live call was used.

`model/list` returned `gpt-5.6-luna` as visible and reported support for `low`
and `medium`. Two independent persisted threads were then run:

| Check | Requested | Reported | Result |
| --- | --- | --- | --- |
| Minimal JSON smoke | `gpt-5.6-luna`, `low` | `gpt-5.6-luna`, `low` | passed |
| Real Pass-1 schema and validator | `gpt-5.6-luna`, `medium` | `gpt-5.6-luna`, `medium` | passed |

Local evidence is retained under ignored `.verification/codex/`. The final low
smoke evidence is `live-smoke-low-final/`; the representative structured test is
`live-p1-medium/`. Both contain RPC traces, identifiers, usage and copied
rollouts. The live rollout inspection found:

- the application custom base instruction with custom provenance;
- the exact application developer instruction and exact user payload;
- distinct thread/session/turn IDs and persisted rollout paths;
- no loaded `AGENTS.md`, skill descriptions, tool catalog, MCP resources, or
  plugin instructions;
- the expected platform-owned read-only permissions instruction and minimal
  date/timezone environment wrapper;
- model and effort `gpt-5.6-luna` / requested effort in turn context;
- final answer and token usage before graceful shutdown.

The app-server world-state format still contains generic skill/orchestrator
capability flags even after every discovered skill is disabled and the re-list is
clean; no skill content was injected into the inspected request. This is recorded
as a runtime-wrapper limitation rather than described as a bare request.

## Checks not run

- OpenAI live generation/token counting: not run because no API key was needed
  or authorized; deterministic HTTP/SSE fixtures passed.
- Live llama.cpp smoke: not run because no local server availability was
  provided; the existing real local transport test server and full pipeline
  suite passed.
- Full-book paid run: not run; only the authorized minimal and representative
  structured Codex checks were performed.
