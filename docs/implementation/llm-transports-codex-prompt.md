# Codex implementation prompt — Intelitex LLM transports

Work in the current Intelitex repository. Implement the attached PRD, saved in the repository as `docs/prd/llm-transports.md`.

Read the entire PRD before modifying code. If it is attached as `PRD.md` rather than already installed at that path, read the attachment and save it at the repository path first. Inspect the actual checkout and applicable repository instructions; do not assume the branch matches an earlier conversation. Preserve unrelated local changes. Implement and test the feature; do not stop after producing a plan or another architecture proposal.

## Product intent

Add three native inference providers behind a transport-neutral boundary:

- `llamacpp`: preserve the existing local llama.cpp HTTP/SSE behavior and native tokenization/preflight.
- `openai`: use native OpenAI Responses API, not a renamed llama.cpp client or a Chat Completions compatibility proxy.
- `codex`: use the locally installed Codex app-server over stdio as isolated thin inference, not as a coding agent.

The most important requirement is complete, durable application-visible communication evidence. For every attempt, including failures, retain the exact prompts and source inputs, semantic and native requests, schemas, stream/RPC events, requested and reported model identities, effective parameters, available usage, timestamps, terminal state, and safe diagnostics. Store this during normal operation even with debug disabled. Do not reduce evidence to character counts, hashes, or final text. Never record authentication secrets.

## Required preparation

Use the installed **Codex App Server** skill: `$codex-app-server` — “Build isolated Codex app-server integrations.” Read its full instructions and thin-inference reference. Run the supplied no-model-turn protocol audit against the actual executable and inspect the generated experimental schemas. Validate the exact argv/config Intelitex will use. Record the CLI/schema identity and audit findings. Do not copy a historical flag list blindly, disable strict validation to hide errors, globally modify the skill, or assume version 0.145.0 is installed.

The Intelitex checkout does not include `any-item`. Create a unique temporary directory under `/tmp` and shallow-clone this public reference over HTTPS:

`https://github.com/hipotures/any-item.git`

Record the fetched commit. Inspect `src-tauri/crates/adapters/src/codex.rs`, `src-tauri/crates/adapters/src/ai.rs`, `src-tauri/src/model_catalog.rs`, and `src-tauri/resources/ai-model-families/`. Search by content for the existing model/pricing refresh mechanism identified by the owner. Its location is not prescribed here. A stale generated catalog does not mean that refresh is missing.

Use the clone only for inspection. Do not modify it, add it as a dependency/submodule, require a GitHub credential for the public clone, or make Intelitex depend on a `/tmp` path. If the clone or updater cannot be found, report exactly what was checked and continue from the PRD, skill, and official documentation. Provide validated catalog import/update rather than inventing an existing script or hardcoding prices.

## Non-negotiable implementation rules

### Preserve Intelitex

Keep import, frozen source/scene/unit IDs, P1, terminology review/approval, P2–P5, local validation, SQLite checkpoints, recovery, and stale-result behavior. Do not redesign prompts or silently rerun accepted work. Migrate existing local configuration narrowly to a llama.cpp profile; preserve old artifacts and human choices. Use a safe SQLite backup before an on-disk migration.

Keep task acceptance identity separate from per-attempt execution identity. A changed profile affects future calls, not automatic replacement of accepted results. Recover a completed compatible attempt without generation when only local finalization/checkpointing was interrupted. Do not reuse a different provider/model's unaccepted result as though it were the same request. Never double-count usage when reusing checkpoints.

### Evidence and accounting

Create the attempt record before preflight/submission, record outgoing requests before sending, append native inbound/outbound messages incrementally, and finalize metadata on errors and cancellation as well as success. Preserve partial answers. Keep transport completion, output validation, evidence completeness, and accepted checkpoints distinct. If recording fails, do not silently continue unrecorded work.

Record all usage fields exposed by the actual backend, including cached input, cache-write input, reasoning output, and native totals where present. Preserve the whole raw payload and all usage events. Missing is not zero; estimates are not measurements; cumulative updates are not additive. Report accounting scope and coverage. Do not regenerate a valid answer merely to obtain missing usage.

Separate OpenAI API cost estimates from any Codex API-equivalent estimate and from actual provider-reported charges. Keep rate snapshots immutable per attempt. Prices, models, context capacities, effort choices, endpoints, and profile defaults belong in data/configuration. Adding a model supported by an existing transport or refreshing a rate must not require Python changes. Unknown prices must not block inference.

### Codex runtime

Use a new persisted thread for every attempt: `ephemeral: false`. Retain thread/session/turn IDs, the opaque returned path, and a durable flushed rollout copy. Do not use `thread/resume` or carry history across passes/retries. Persistence is evidence, not conversation context, and it is not controlled by debug mode.

Use private application-owned Codex homes and an empty work directory. Reuse/copy only explicitly authorized authentication. Do not copy user configuration, skills, rules, memories, trust settings, plugins, or arbitrary credential environment variables. Keep the implementation agent's skill separate from the inference runtime, which loads no coding skills.

Replace the default coding-agent base instructions with a short nonempty application-owned base instruction. Put the unchanged pass instructions in the supported trusted developer-instruction field and the source payload in the user turn. Record both trusted layers. Apply verified tool/capability isolation, read-only sandboxing, no approvals, and strict configuration. Disable skills and re-list to verify the result. Check persisted evidence for leaked context and runtime-added wrappers; do not claim a completely bare model request.

Do not discard notifications while awaiting an RPC response. Correlate request/thread/turn/item IDs; explicitly reply to unsupported server requests; distinguish commentary from final output; require a successful terminal turn. Idle, EOF, and plausible JSON are not completion. Capture late usage, flush gracefully, and reap the owned process group after bounded interrupt/termination on cancellation or timeout.

### OpenAI and context

Use independent foreground Responses requests, native structured output, `store: false`, no conversation chaining/tools, and disabled truncation using currently supported API fields. Local logging stays fully enabled. Capture terminal responses, refusals, incomplete states, native usage, and safe request identifiers. Do not hide retries inside an SDK.

OpenAI documents `POST /v1/responses/input_tokens`; verify and use it where supported. Do not inherit the earlier incorrect assumption that the endpoint is absent. Check current official docs and actual capabilities rather than relying on previous conversation claims about model names, prices, or context sizes.

Separate a planning output reserve from an enforceable generation cap. Never invent a Codex max-output parameter or claim reasoning effort is a hard token limit. Validate explicit unsupported settings. Token caches must include tokenizer identity; do not reuse old imported token counts across different tokenizers. Never silently truncate source text, change frozen boundaries, compact context, or switch providers to force a request through.

## Execution and verification

Build the shared semantic/evidence boundary and extract llama.cpp without regression first. Add named/default/per-pass profiles and capability validation, then both new providers. Complete the explicit catalog refresh/import path, offline attempt/usage inspection, narrow migration, and the PRD's acceptance tests.

Keep the implementation small and native to the existing Python codebase. Use `uv` for dependency/test workflows. Do not introduce a Rust sidecar, LiteLLM/LangChain stack, proxy service, automatic routing, multi-agent inference, or unrelated UI redesign.

Test all actual P1–P5 schemas and the failure paths in AC-01 through AC-25. Include fake HTTP/SSE and fake app-server transcripts, interleaved RPC notifications, late/missing usage, unknown events, schema failures, stream interruption, redaction, disk/write failure, process cleanup, and recovery without duplicate calls.

Run no-model-turn auditing and deterministic tests first. Credential import/reuse and live model spending require explicit authorization/configuration; missing live access must not block implementation and mocked verification. Do not upgrade Codex or search unrelated credential locations to make tests pass. Mark every live check accurately as passed, failed, or not run.

All newly authored code, comments, and documentation must be in English; preserve Intelitex's existing translation-language behavior and original source content. Report completion to the user in Polish, covering changes, test commands/results, installed Codex protocol findings, reference/updater findings, migrations, evidence locations, limitations, and checks not run.

Finish with coherent local commits containing only intended changes unless instructed otherwise. Do not push, open a pull request, alter repository visibility, or modify `any-item` without an additional request.
