# Intelitex — LLM Transports and Communication Evidence

**Document type:** Product Requirements Document (PRD)  
**Version:** 1.0  
**Date:** 2026-09-20  
**Status:** Ready for implementation by Codex  
**Target repository:** `https://github.com/hipotures/intelitex`  
**Implementation prompt:** `CODEX_IMPLEMENTATION_PROMPT.md`

## 1. Objective

Enable Intelitex to execute its existing five-pass translation pipeline through three independently implemented LLM transports:

| Provider ID | Transport | Purpose |
|---|---|---|
| `llamacpp` | HTTP, llama.cpp Chat Completions and native auxiliary endpoints | Preserve the existing local-model workflow. |
| `codex` | A locally installed `codex app-server`, JSON-RPC over stdio | Use models available through the user's explicitly configured Codex authentication. |
| `openai` | Native OpenAI Responses API over HTTP with SSE streaming | Use models through separately configured OpenAI API credentials. |

**The primary product requirement is durable, inspectable communication evidence, not merely interchangeable text generation.** For every attempt, the user must be able to determine what was requested, which model and settings were requested and reported, what was sent and received, how the attempt ended, and what token usage and cost information is actually available.

Preserve the existing import → P1 → human terminology review → approval → P2/P3/P4/P5 workflow, validated checkpoints, terminology decisions, frozen translation units, and recovery behavior. This is an inference-layer change, not a replacement of Intelitex with a coding agent.

## 2. Basis, assumptions, and source hierarchy

### 2.1 Source-derived baseline

The inspected `bookpipe/client.py` combines llama.cpp discovery, tokenizer calls, request construction, context preflight, and HTTP streaming. Its body contains llama.cpp-specific options, including template/thinking controls. It is not a generic OpenAI transport. [R1]

The inspected `bookpipe/engine.py` builds checkpoint identity from prompt, inputs, and schema. Recovery separately inspects Chat Completions request bodies and completed attempt artifacts. These are different mechanisms and must remain different. [R2]

The reference `any-item` implementation contains a Codex app-server adapter, authentication isolation, structured-output handling, and model-family configuration. It is an implementation reference, not a runtime dependency or an authoritative protocol specification. [R3]

The supplied Codex App Server skill explicitly separates persistence, model context, authentication, and capabilities. Its reference recipe was validated against `codex-cli 0.145.0`; the installed executable and generated schemas must take precedence over that historical snapshot. [R4]

The owner states that `any-item` is public and includes a pricing-refresh script. **An unrefreshed catalog is not evidence that the refresh mechanism is missing or that the integration is broken.** Locate and inspect that mechanism rather than inventing a script path or replacing it with manually hardcoded prices. Its precise path has not been established by this PRD.

The Intelitex `main` ref observed while preparing this document was `ffd3fa437e6013769dd1dd3f8dabe97a7efa0821`. The implementation must inspect its actual checkout, including uncommitted changes; it must not reset the repository to this reference. [R9]

### 2.2 Implementation-time source hierarchy

For Codex protocol fields and behavior, use the installed binary, its generated experimental schemas, the installed `$codex-app-server` skill and audit, and inspected live rollouts. Use current official documentation for OpenAI and llama.cpp. Use `any-item` to understand reusable patterns. Do not treat model names, context capacities, effort enums, feature flags, or prices from earlier conversation messages as verified constants.

The attached `openai.yaml` describes the skill's interface. It is **not** an OpenAI model/pricing catalog. [R4]

Sections below are product requirements and proposed design decisions, not claims that every backend already implements every capability.

## 3. Product decisions fixed for this implementation

| Decision | Requirement |
|---|---|
| Evidence retention | Full local communication evidence is enabled in ordinary operation, including when debug is off. |
| Sensitive content | Retaining prompts, source text supplied to the model, schemas, responses, and usage is expressly requested. Authentication secrets are excluded. |
| Codex persistence | Use persisted threads: `ephemeral: false`, subject to the installed protocol. Retain the returned rollout and its identifiers. |
| Conversation history | Each inference attempt is independent. Do not resume an earlier thread or attach an earlier response as conversational history. |
| Runtime | Intelitex owns orchestration; Codex is thin inference, not an autonomous coding/research agent. |
| OpenAI remote storage | Request `store: false`; local evidence remains fully retained. Remote retention policies are separate. |
| Selection | Named profiles, one default profile, and optional per-pass overrides. No automatic routing. |
| Configuration | Models, endpoints, effort choices, capacities, prices, and user-selectable defaults are data, not model-name conditionals in executable code. |
| Existing results | Preserve accepted results and human choices. Changing a profile affects new work, not automatic regeneration. |
| Failure behavior | No silent truncation, semantic downgrade, provider fallback, missing-data fabrication, or automatic duplicate generation. |
| Implementation scope | Native Python integration in Intelitex; no Rust sidecar, HTTP proxy, external agent framework, or new service required. |

**Persisted does not mean stateful inference.** A new persisted Codex thread per attempt provides an inspectable rollout without introducing previous passes into model context. The former suggestion to use ephemeral threads for this application is superseded by the owner's evidence-retention requirement.

Retention permission does not authorize copying arbitrary authentication files or spending API credit in unattended development tests. Credential reuse and live model tests require separately explicit configuration/authorization.

## 4. Scope and non-goals

### Required scope

Implement all three providers, a transport-neutral request/result boundary, durable attempt recording, accurate usage normalization, refreshable model/pricing data, capability-aware settings, context budgeting, per-pass profile selection, and offline inspection/reporting. Preserve old projects through a narrow, tested migration/read path.

The first release must support the actual text-plus-JSON-schema requests used by all five Intelitex passes. It is not sufficient to demonstrate a generic one-line prompt while real pipeline schemas fail.

### Non-goals

Do not redesign translation prompts, terminology review, import structure, stale-result rules, or EPUB handling. Do not add tools, MCP, web research, shell access, multi-agent inference, image generation, automatic model choice, cross-provider fallback, distributed scheduling, or a generic plugin platform. Do not add vision merely because `any-item` uses it. Do not promise access to hidden chain-of-thought or unseen provider-internal requests.

## 5. Architecture and contracts

### 5.1 Boundary

The engine operates on a semantic request. A provider compiles it into its native protocol, executes it, and reports normalized events and results. A shared evidence recorder handles persistence. Keep these responsibilities separable without introducing a large framework.

The engine must not inspect `messages`, `response_format`, SSE event payloads, Codex RPC fields, or OpenAI response items to make business decisions. A small provider registry is acceptable; provider-specific branches throughout P1–P5 are not.

Keep the existing Python/HTTP stack where practical. An official SDK is acceptable only if its raw events, transport metadata, cancellation, and retry behavior remain observable. Do not introduce LiteLLM or LangChain just to support these three transports.

### 5.2 Semantic request

The request must identify the task/pass/unit and attempt, exact trusted instructions, source input data, canonical output schema and version, selected profile/provider/model, reasoning intent, supported generation controls, planning output reserve, optional enforced output cap, timeout policy, and explicitly namespaced provider options.

Separate:

- requested settings from settings actually sent and settings reported effective;
- a planning reserve from a provider-enforced generation limit;
- logical request identity from physical request/thread/response identifiers;
- trusted instructions from book content and prior-stage outputs used as data.

Placeholders in example profiles are not available models. Model IDs are opaque strings; do not parse arbitrary `:` or `/` characters as effort/provider separators. Store provider, model, and effort separately. Any legacy combined identifier is handled only by a scoped migration.

### 5.3 Result and event contract

Expose final answer text, terminal outcome, refusal/incomplete/error details, requested and reported model identity, normalized and raw usage, identifiers, timing, evidence location, and evidence completeness. Stream visible output separately from provider-exposed reasoning text or reasoning summaries.

Use distinct statuses for generation, validation, evidence finalization, and pipeline acceptance. A response can be provider-completed but fail JSON validation; it must still retain its usage and raw output. A valid response can have missing usage; it must not be represented as zero-token generation.

Recommended terminal outcomes include completed, incomplete/length-limited, refused, failed, timed out, cancelled, protocol error, and unknown remote outcome. An HTTP disconnect after submission does not prove that remote generation stopped.

The accepted task checkpoint remains separate from the attempt record. Native provider statuses must be retained, not disguised as a synthetic Chat Completions `finish_reason: stop`.

## 6. Communication evidence: mandatory product behavior

### 6.1 Evidence envelope

Create an attempt identity and writable evidence location **before** discovery/preflight or generation activity associated with that attempt. Record a request before sending it. Append incoming messages as they are received. Finalize metadata on every ordinary exit path, including exceptions and Ctrl+C. A hard crash may leave a partial last record; recovery must detect and report it.

An attempt must retain:

| Category | Required content |
|---|---|
| Identity | Project, command run, pass, section/unit, task fingerprint, attempt ID/number, provider, profile, requested model, reported model, and provenance of each identity. |
| Instructions and data | Exact prompt file content, exact submitted source/input payload, retry additions, canonical schema, and provider-specific schema actually sent. |
| Native communication | Outgoing request bodies/RPC messages, incoming responses/notifications/server requests, native streaming events, and explicit unsupported-request replies. |
| Effective configuration | Resolved non-secret profile, sent parameters, supported/omitted settings with reasons, protocol/catalog versions, safe endpoint or executable identity. |
| Lifecycle | UTC timestamps, local ordering/monotonic timing, request start, first event, first visible text when available, terminal event, cleanup, and elapsed time. |
| Output | Partial visible text, canonical final text, exposed reasoning/summary with its actual type, native terminal response, validation result, and any conservative repair as a separate artifact. |
| Usage | All original usage payloads/events, normalized fields, accounting scope, completeness, and source. |
| Diagnostics | Structured errors, safe HTTP status/headers, response IDs, retry information, bounded redacted stderr, and truncation indicators. |
| Codex-specific | CLI version/schema identity, thread/session/turn/item IDs where exposed, isolation configuration, opaque returned rollout path, and retained rollout evidence. |
| Pricing | Catalog version/source/time, applicable rate snapshot, estimate type, priced coverage, and unknown components. |

“Native communication” means the application-visible HTTP/SSE or JSON-RPC exchange after mandatory secret redaction. It does not mean a TLS packet capture, hidden server instructions, or guaranteed visibility into Codex's internal backend traffic. Codex rollouts add runtime evidence beyond the client RPC trace, but must not be advertised as a complete raw model-network capture.

### 6.2 Storage and portability

Extend the current attempt/artifact organization rather than replacing project storage wholesale. Existing `answer.txt`, partial files, result artifacts, and checkpoints must remain usable. Introduce an explicit artifact format version for new records.

An attempt must provide discoverable equivalents of:

| Artifact | Purpose |
|---|---|
| `attempt.json` | Identity, lifecycle states, evidence manifest, and completeness. |
| `request.semantic.json` | Exact semantic request plus immutable resolved configuration. |
| `request.transport.json` | Compiled generation payload or Codex thread/turn request parameters. |
| `transport.jsonl` | Ordered bidirectional native messages/events with timestamps and correlation IDs. |
| `schema.canonical.json`, `schema.transport.json` | Original contract and documented backend adaptation. |
| `context.json` | Count method, capacity source, reserves, margins, and effective limit support. |
| `answer.partial.txt`, `answer.txt` | Received visible output and canonical completed output. |
| `reasoning.partial.txt`, `reasoning.txt` | Only reasoning content/summary actually exposed, with type metadata. |
| `response_meta.json` | Native outcome, identities, timings, validation and execution metadata. |
| `usage.raw.json`, `usage.json` | Native usage snapshots and normalized selected accounting. |
| `pricing.json` | Immutable rate snapshot and clearly labeled estimates. |
| `error.json`, `stderr.txt` | Safe diagnostics when applicable. |
| `codex/` | Persisted rollout copy and isolation/protocol evidence for Codex attempts. |

These names describe the required content, not a demand to duplicate an existing equivalent file. A content-addressed shared session trace is acceptable if the attempt manifest identifies its messages and the evidence remains present in a normal project backup. An absolute path into `/tmp` or an external Codex home alone is not sufficient.

Use atomic final-file replacement, explicit flushes, and existing project locking. Persist the finalized evidence manifest before accepting the task checkpoint. Do not buffer an entire long stream solely in memory. Ordinary operation must not delete completed or failed attempt traces. Retention cleanup is explicit and outside this release's required interface.

### 6.3 Failure and observability completeness

If evidence cannot be created before sending, fail before generation. If writing fails during generation, attempt cancellation/cleanup and stop the pipeline; never claim a fully recorded success. Preserve recoverable received data and distinguish write failure from model failure.

A completed answer with unavailable usage remains recoverable; record `usage_status: unavailable` and a reason. Do not regenerate a valid answer just to recover accounting. For a supported backend expected to expose usage, incomplete telemetry must produce a visible warning and stop launching subsequent paid work by default until an explicit incomplete-observability continuation is chosen. This operational hold must not invalidate or discard an otherwise accepted answer.

If a required Codex rollout cannot be copied after a completed turn, retain the answer and terminal evidence, mark evidence finalization incomplete, and repair the local evidence before any decision to regenerate. An older binary without the required persistence feature is unsupported for this product's full-evidence Codex mode.

### 6.4 Privacy boundaries

Use private directories/files (0700/0600 on Unix where appropriate). Never store API keys, bearer/access/refresh tokens, cookies, `auth.json` contents, credential-bearing URLs, or arbitrary environment dumps in artifacts, logs, exceptions, tests, Git, or exports. Redact before persistence, including malformed frames and stderr; annotate redaction without removing ordinary source text.

Use credential references in configuration. Do not snapshot an entire Codex home or SQLite directory to collect a rollout. Exclude operational evidence, source books, credentials, and runtime state from Git. Do not send a diagnostic bundle to an external service. Keep API and Codex authentication isolated from each other and from local-server authentication.

Full local retention is authorized for this workflow. That is not a zero-retention claim about cloud providers. `store: false` and local artifact retention are independent decisions. [R8]

## 7. Token accounting, cost, and catalog refresh

### 7.1 Usage model

Normalize input, cached input, cache-write input, output, reasoning output, and total token counts when their meanings can be established. Preserve additional provider categories without requiring a schema change to retain raw evidence. Each normalized value must retain its provenance and scope.

Rules:

1. Distinguish missing, not supported, partial, estimated, protocol-defaulted, and provider-reported values. Missing is not zero. A documented protocol default may be applied only with that provenance recorded.
2. Use the provider's reported total as canonical when available. Cache and reasoning categories may overlap parent categories; never add all displayed columns together.
3. Keep preflight counts, generated-output character counts, and billed/reported usage separate. Do not present streamed characters or event counts as exact tokens.
4. Record every usage update, but do not sum cumulative snapshots. Select or derive final accounting according to the verified provider semantics.
5. For Codex, preserve the entire `tokenUsage` event, including `last`, `total`, and any context-capacity information actually exposed. Map fields such as `inputTokens`, `cachedInputTokens`, `cacheWriteInputTokens`, `outputTokens`, `reasoningOutputTokens`, and `totalTokens` according to the installed schema. `last` and thread totals must not be counted twice. [R4]
6. If an agent runtime performs multiple internal model operations/retries, distinguish last-operation usage from turn/attempt usage. Record exposed totals and scope; do not claim a complete per-attempt bill from only the last internal operation.
7. Include unsuccessful, rejected-JSON, cancelled, and incomplete attempts in accounting when usage is known. Usage missing after disconnection must be explicitly counted as an unaccounted attempt.
8. Reusing an accepted checkpoint or locally recovering a completed response consumes no new model tokens. Reference its original attempt rather than duplicating its original usage in command totals.

Expose per-attempt usage and aggregates by project, pass, provider, and model. Reports must distinguish new usage in the current command from historical usage and accepted-result usage. Mixed-model token totals may be displayed as accounting totals, not as equivalent amounts of text or comparable tokenizer units.

### 7.2 Cost semantics

Separate provider-reported monetary charges, OpenAI API cost estimates, and optional Codex API-equivalent estimates. Subscription-backed Codex tokens are not automatically an OpenAI API bill. Discover/report the configured authentication/billing mode without exposing its secrets; do not assume all app-server use has identical billing.

For an explicitly self-hosted local endpoint, a zero provider fee may be recorded as a configuration fact; hardware/electricity cost is unknown unless separately modeled. An arbitrary HTTP endpoint is not proof of zero cost.

Estimates must retain currency, rate units, model mapping, tier/service assumptions, calculation version, applicable input/cache/output/context rules, source, retrieval time, and an immutable catalog snapshot/hash. Unknown pricing produces an unknown estimate, not a fabricated zero. Do not assume every model has the same pricing categories.

A price refresh must not retroactively rewrite recorded estimates. Revaluation, if later supported, is a separate report with its own catalog version. Inference and usage capture must work when pricing is unknown or refresh is offline.

### 7.3 Refreshable data

Models, efforts, capacities, prices, and user-selectable defaults live in versioned data/configuration. Adding a model supported by an existing transport or updating its rate must not require editing Python or rebuilding Intelitex. New protocol mechanics may require adapter code; this is not a requirement for arbitrary transport plugins.

Inspect the pricing-refresh mechanism the owner identifies in the public `any-item` repository. Search by behavior/content as well as filename. If reusable, adapt the necessary mechanism with attribution where applicable, not the whole application. If it is absent from the fetched branch, state that precisely and provide an explicit validated catalog import/update path; do not claim to have found or reused an unobserved script. Lack of this reference must not block the three transports.

Provide an explicit refresh/import operation, separate from generation. A configured updater must stage its output; Intelitex validates it before atomic activation. Retain the previous valid catalog on failure. Preserve custom profiles and user overrides. Record the updater/source and refreshed time, and show changes. Do not execute a remote script or mutable pricing fetch implicitly at application import or before every request.

Distinguish configured entries from live availability. Use provider discovery where supported; do not assume every advertised field is present, and do not fabricate capacities from a model name. Model discovery and catalog refresh must not silently change the selected profile or regenerate completed work.

## 8. Provider requirements

### 8.1 `llamacpp`

Extract the current client into the provider boundary with behavior-preserving tests first. Keep existing discovery, native tokenizer access, request-level preflight and template/tokenizer fallbacks, SSE streaming, and supported thinking controls. Keep native parameters inside this adapter. Existing local projects must not suddenly require OpenAI credentials or a Codex executable. [R1]

Preserve the no-silent-truncation invariant. Report per-request capacity and its source, not merely a model's advertised maximum. Keep endpoint construction correct for explicit ports, path prefixes, `/v1`, IPv6, and default HTTP/HTTPS ports. Never apply the legacy local port 8080 to a normal OpenAI HTTPS URL.

Accept only the provider's verified complete-generation condition; retain partial outputs and native finish reasons on all other outcomes. Record usage-only final chunks and provider timings. Stream/event parsing must not confuse reasoning with the final JSON answer.

### 8.2 `openai`

Implement native Responses API, not a llama.cpp request with a different base URL. Keep OpenAI credentials separate. Use the existing HTTP stack unless an official SDK materially simplifies the implementation without hiding events or retries.

Map trusted pass instructions and user data to distinct supported instruction/input fields. Compile the output schema to the supported strict structured-output form. Keep the canonical schema and local semantic validators authoritative; record all schema adaptations. Verify model-specific parameter support rather than forwarding llama.cpp temperature/seed/thinking extensions. [R5]

Use foreground streaming and independent requests, without `previous_response_id` or automatic conversation state. Request `store: false` and disabled truncation using the current supported API. Do not configure background mode, tools, or automatic compaction. Local recording remains enabled regardless of remote storage settings. [R8]

Record native SSE events, response ID, reported model, usage, safe request/rate-limit headers, refusals, incomplete details, and the terminal response. A text delta or completed output item alone is not overall completion. Handle lifecycle errors and cancellation explicitly. Disable hidden SDK retries or expose every physical attempt. [R6]

**Preflight correction:** OpenAI documents `POST /v1/responses/input_tokens`. Use it where supported for the actual request's input; do not implement this adapter on the earlier incorrect assumption that no Responses token-count endpoint exists. Keep request-schema support checks and a clearly labeled fallback policy separate. [R7]

### 8.3 `codex`

#### Skill and protocol validation

Use the installed **Codex App Server** skill (`$codex-app-server`, “Build isolated Codex app-server integrations”). Read its full instructions and thin-inference reference. Run its no-model-turn audit against the actual executable, inspect generated experimental schemas, and record the version, relevant schema hashes, features, and audit results. Do not globally edit the user's skill or downgrade validation merely to make startup pass. [R4]

Audit the exact argv/config used by Intelitex, not only a neighboring sample. Separate “this historical recipe no longer matches” from “the current binary cannot provide required isolation.” Unsupported optional fields may be omitted with an explicit capability record; missing required isolation/persistence controls fail before generation. Do not preserve obsolete flags as silent compatibility guesses.

#### Isolation and authentication

Use private application-owned `CODEX_HOME`, `CODEX_SQLITE_HOME`, and an empty working directory outside the source repository. The working directory must not be the Intelitex checkout, a book folder, or the temporary `any-item` clone. Do not inherit arbitrary agent configuration, skills, plugins, memories, rules, environment roots, MCP servers, or unrelated credential environment variables.

Provide explicit authentication setup/import using a selected source or an isolated supported login flow. Reuse/copy only authentication expressly authorized for Intelitex; never copy the user's complete Codex configuration. Use atomic private-file writes and protect against concurrent refresh/import operations. Do not automatically overwrite a refreshed isolated credential with an older source file on every request. Never log authentication payloads.

Use a short, nonempty application-owned `baseInstructions` to replace the coding-agent base prompt. Put the exact `passN.txt` instructions in the supported trusted developer-instruction field and only the source/input payload in the user turn. Keep the short runtime base instruction in an editable resource, not model-specific Python. Record both instruction layers. A minimal base plus explicit pass developer instructions is intentional; the book payload must never be promoted into either trusted layer.

Use read-only sandboxing and no approvals for inference, together with actual tool/capability disablement. Sandbox/approval flags or `personality: none` alone are not isolation. Disable loaded skills through the installed protocol, verify each write, re-list, and fail on skill-list errors or remaining enabled skills. Clear supported dynamic tools, environments, selected capability roots, and runtime workspace roots; repeat required turn-level overrides. Verify from persisted evidence that no project/user instructions or tools leaked. [R4]

The implementation agent uses the skill while developing Intelitex. The runtime inference child does **not** load that skill or any other coding skill.

#### Persistence and lifecycle

Create a new **persisted** thread for every attempt, including validation retries. Do not use `thread/resume` to continue translation history. Reusing a subprocess does not authorize reusing conversation state. A one-shot subprocess per attempt is acceptable for the initial implementation if clean shutdown and recording are correct; no process pool is required.

Initialize, acknowledge initialization, validate capabilities, start the thread, start the turn, and consume the entire interleaved stream through a verified terminal outcome. Use a dispatcher/buffer that never discards notifications while waiting for an RPC response. Correlate RPC, thread, turn, and item identifiers. Store unknown notifications before deciding whether they affect correctness. Explicitly respond to unsupported server requests instead of leaving them pending.

Prefer a completed final-answer item as canonical text; keep other message phases separate. Support a missing-phase variant only when verified for the installed protocol, without concatenating commentary into output. Require explicit successful turn completion. Thread idle state, valid-looking JSON, stream EOF, or partial text are not success. Preserve retrying errors, terminal failures, and system-error notifications.

Wait a bounded interval for late usage after completion. Finish immediately when all required terminal evidence is available; do not impose a fixed extra delay on every successful request. Then flush gracefully and stop the child. On cancellation/timeouts, interrupt using the supported protocol when possible, then terminate/kill the owned process group with bounded waits. Drain stdout/stderr concurrently and reap processes.

Retain thread/session/turn IDs and the **returned** rollout path; do not derive Codex's directory layout. Copy the flushed rollout to durable attempt evidence or another project-owned location before removing a temporary runtime. Inspect and record runtime-added instruction/environment layers. A small platform-owned wrapper may remain; do not claim a bare-model request. [R4]

For this translation product, prevent automatic context compaction/truncation using verified controls where available. Detect exposed compaction/context-reduction events and reject the affected attempt as context-altered. If the installed runtime cannot establish the required no-silent-context-change behavior, expose that limitation and fail preflight rather than claiming the invariant is enforced.

### 8.4 Structured-output compatibility

All providers must execute the real P1–P5 schema/validation contract. Keep provider schema compilation separate from the canonical schema. Preserve required IDs, complete block/sentence coverage, source order, and evidence grounding in local validation.

A backend may implement a narrower JSON Schema dialect. Any permitted reduction in generation-time constraints must be explicit in the adaptation record and still enforced locally. Never remove a semantic check from Intelitex to accommodate a provider. If faithful validated execution is not possible, reject the configuration before generation. No silent fallback to unconstrained text or a different model.

## 9. Context planning and generation limits

Separate text tokenization for planning from complete-request token counting and observed usage. Represent count quality as provider-exact, verified-tokenizer with estimated wrapper, conservative estimate, or unavailable. Record tokenizer identity/version, method, input hash, and scope.

For local llama.cpp, preserve native tokenization. For OpenAI, prefer the documented input-token endpoint for complete requests. For Codex, do not assume a public preflight-count method or repackage Codex authentication as direct API access. Use verified installed capabilities, a verified tokenizer when available, or an explicit conservative estimate policy. Never silently substitute a different model's tokenizer or an unexplained characters-divided-by-four heuristic.

When an exact count is unavailable, use a declared margin and a verified/configured capacity, report the uncertainty, and fail near the limit or when the capacity is unknown. Capability metadata must say what is known, not pretend that an estimate proves exact fit. Planning does not require thousands of paid/network count calls for individual fragments: use cached verified tokenization where possible and full-request preflight before generation.

**Output reserve and output cap are different.** Keep a planning reserve for every profile. Enforce a generation cap only where the actual backend supports it. An explicit requested hard cap that cannot be enforced is a configuration error; an unset hard cap may be used with a planning reserve and timeout, clearly reported as such. Do not invent an app-server `max_tokens` field or pretend that reasoning effort enforces a token budget.

Reasoning availability/effort is model-specific. `thinking: off` must not be translated into an unsupported `effort: none`; preserve local behavior and require an explicit supported setting for new profiles. Unsupported explicit temperature/seed controls fail validation. Legacy local defaults must not silently acquire different meaning on Codex/OpenAI.

Namespace token caches by tokenizer/model identity and relevant configuration. Imported `source_tokens` without matching tokenizer provenance must be recalculated before being used for a changed profile. Never rewrite frozen source blocks, IDs, natural scene boundaries, human choices, or completed translation units to make a smaller model fit. Preserve existing P1 subdivision rules; changes to a saved P1 plan require explicit versioning and cannot discard accepted analysis.

## 10. Profiles and configuration

Provide named, independently configurable profiles. A profile defines a provider, model, endpoint/executable settings, credential reference, reasoning/sampling controls, planning reserve, optional enforced output cap, timeout, context policy, and relevant catalog references. Do not conflate a profile with a provider: several profiles may use different models/endpoints with the same provider.

Resolve selection in this order: explicit per-pass command override, explicit command-wide profile override, project per-pass profile, project default profile. Record the fully resolved selection for every new attempt. Existing accepted checkpoints keep their original provenance. Never mutate project settings merely because a one-command override was supplied; saving a new default is an explicit action.

Keep parameter precedence documented and deterministic. Resolve project/profile/pass values before validation, expose where each effective value came from, and reject unknown misspelled keys. Provider-specific extensions are namespaced and cannot override instructions, source payloads, schema, routing, isolation, credential policy, or evidence capture.

Credentials are references to an explicitly selected environment variable or supported private credential store, not literal values in a project/catalog. Local, OpenAI, and Codex credentials must never be routed to one another. Validate URL handling and redirects so authorization cannot be forwarded to an unintended origin.

Support at least: one profile for all passes; different configured profiles for P1–P5; changing a future-work profile while preserving existing results; and inspecting the resolved settings without generating text. Ship usable local defaults and documented cloud-profile templates, not fabricated available model IDs or unverified effort/capacity values.

## 11. Checkpoints, recovery, and existing projects

### 11.1 Separate task acceptance from generation identity

Preserve the existing policy that accepted results survive a later model/settings change. A task identity reflects the source/input/instruction/schema work and existing dependency rules. It is not an automatic “rerun on model change” key.

Every actual generation attempt separately receives a semantic execution signature. It includes provider, routing/endpoint identity, requested model and verified resolved model metadata where available, all generation-affecting settings, trusted instruction layers, input data, schema, and adapter/compilation semantics needed to distinguish materially different requests. Exclude secrets and volatile request/response/thread IDs. Profile aliases alone must not cause duplicate work when effective configuration is identical.

Record runtime and catalog versions as provenance. A price-only catalog update is not a generation change. Cosmetic profile edits, stream presentation, debug settings, and credential-token rotation are not reasons to regenerate. Unknown model metadata must not be fabricated to force an apparent signature match.

### 11.2 Recovery behavior

Accepted checkpoint reuse and recovery of an unaccepted completed attempt are distinct operations. The latter must verify compatible generation identity, terminal success, artifact integrity, and the current canonical validators. It must not treat a response from a different provider/model as interchangeable just because the prompt matches.

Keep the present intentional handling of validation-retry helper fields and validation-only schema changes through a narrowly specified recovery comparison plus revalidation. Preserve the exact original request and every repair. Removing a retry helper for matching must never remove it from the evidence record. Do not replace recovery with strict byte-for-byte native request equality; transport envelopes and new RPC IDs are not semantic differences.

On restart after a crash, inspect durable terminal events, final-answer artifacts, and Codex rollouts before resubmitting. A completed generation that lacked only local evidence finalization or a checkpoint should be recovered without a model call. In-flight or unknown remote outcomes remain explicitly uncertain; resume does not guarantee exactly-once billing. Require deliberate retry for an ambiguous submitted request rather than silently duplicating it.

### 11.3 Narrow migration

Existing local settings without provider/profile metadata migrate explicitly to a `llamacpp` profile while preserving their effective values. Preserve old artifacts and keep a narrowly scoped reader for their actual known format. Do not create a broad compatibility layer for obsolete Codex protocol versions.

Use the existing safe SQLite backup mechanism before any on-disk migration. Keep source manifests, review choices, approvals, checkpoints, and evidence intact. Legacy missing usage/model provenance stays marked legacy/unknown; do not backfill guesses or fake communication transcripts.

A project must not require re-import, rerunning P1, or re-reviewing terminology solely to add transports. Existing model-change safeguards must become profile-aware rather than being removed. Warn that continuing with a changed profile produces mixed provenance. Preserve prompt files unchanged unless a separately documented compatibility issue makes a specific change unavoidable.

## 12. Operational interface

Extend the existing CLI with the following capabilities; choose exact names to fit its current style and document them:

| Operation | Required behavior |
|---|---|
| Profile listing/show | Display configured profiles, selected/default/pass assignments, resolved settings, and secret references without values. |
| Provider/model discovery | Fetch supported live metadata explicitly; report unknown capabilities honestly. |
| Doctor | Validate configuration, recording paths, credentials presence without disclosure, schema support, and installed Codex protocol/isolation; no model turn by default. |
| Catalog refresh/import | Validate and atomically activate data; retain old catalog on failure; no generation. |
| Attempt inspection | Offline access to prompt, source inputs, native requests/events, outcome, model, usage, and rollout locations. |
| Usage report | Offline per-project/pass/provider/model accounting, known subtotals, missing coverage, and separate estimate types. |
| Live smoke test | Explicitly opted-in small request against a chosen profile; records the same evidence as production. |

Operational commands that only inspect local evidence, status, or export must work without model servers, credentials, or internet. Live discovery and token counting contact the selected provider but must not start a generation turn. Doctor must clearly distinguish local validation, network metadata checks, token-count checks, and opted-in generation.

Normal progress should identify pass/unit, profile/provider/model, receiving/finalizing status, and available token metrics. Do not expose full book prompts by default in the terminal; retain them in private artifacts. Quiet/debug modes change presentation, not retention. Do not add an unrelated dashboard or replace the review UI.

## 13. Acceptance criteria and test plan

All criteria are release requirements unless marked environment-dependent. Use deterministic fixtures and fake transports/processes. Real services are an additional verification layer, not a substitute for fault-injection tests.

| ID | Scenario | Acceptance evidence |
|---|---|---|
| AC-01 | Existing local project | Existing pipeline tests pass; project resumes without re-import, re-review, lost terms, or duplicate completed calls. |
| AC-02 | Three provider contracts | Text/structured request, terminal result, failure, and full recording contracts pass for each adapter. |
| AC-03 | Actual pass schemas | Real P1–P5 requests compile and validate through each provider, including dynamic evidence IDs and block/sentence coverage. |
| AC-04 | Mixed profiles | P1–P5 can select independently configured profiles; each artifact reports its own actual provenance. |
| AC-05 | Debug disabled | Exact prompts/inputs, native messages, model metadata, terminal result, and available usage still persist. |
| AC-06 | Failed attempt | Timeout, refusal, 4xx/5xx, malformed stream, connection loss, cancellation, invalid JSON, and output limit leave inspectable attempts, not successful checkpoints. |
| AC-07 | Secret redaction | Injected secrets in headers, exceptions, stderr, auth replies, and malformed events do not appear in any retained artifact, log, or test snapshot. Ordinary source content is preserved. |
| AC-08 | Interleaved Codex RPC | Notifications before RPC responses, repeated usage updates, multiple message phases/items, and unsupported server requests are retained and handled without deadlock or loss. |
| AC-09 | Terminal correctness | Idle, EOF, a plausible JSON fragment, or output-item completion alone never marks a Codex/OpenAI request successful. |
| AC-10 | Late/missing usage | Late final usage is captured; missing usage remains unknown, raises the operational warning/hold, and never triggers regeneration of a valid answer by itself. |
| AC-11 | Token arithmetic | Cache/reasoning overlap and cumulative snapshots are not double-counted; raw unknown categories remain available; every normalized total has a source/scope. |
| AC-12 | Recovery accounting | Checkpoint reuse and local terminal-result recovery issue no model calls and do not duplicate historical usage. |
| AC-13 | Output reserve versus cap | Unsupported explicit hard caps fail; a planning-only reserve is labeled honestly. Local enforced limits remain enforced. |
| AC-14 | Context safety | Verified counting or declared conservative preflight works; changed tokenizer invalidates counts; overflow/context alteration cannot silently drop source text. |
| AC-15 | OpenAI token counting | Supported input-token requests are recorded and used; unsupported endpoint/schema behavior is diagnosed rather than mistaken for zero tokens. |
| AC-16 | Codex persisted evidence | With debug off, two attempts create different persisted threads; returned rollout locations/IDs are captured and durable copies remain after cleanup. |
| AC-17 | Codex isolation | Fake and authorized live evidence show custom base/pass instructions and no user/project AGENTS, enabled skills, tools, MCP, plugins, or inherited conversation context. |
| AC-18 | Process cleanup | Cancel/timeout/startup failure releases pipes and reaps the child/process group; successful shutdown allows final evidence flush. |
| AC-19 | Storage failure | Disk-full/permission errors prevent unrecorded generation where detected early; midstream failure is visible and cannot become an unqualified success. |
| AC-20 | Catalog data | Adding a supported model/profile or editing prices needs only data changes; refresh failure preserves the prior catalog and user selections. |
| AC-21 | Pricing history | Old estimates keep their original rate snapshot; Codex equivalent estimates are not labeled invoices; unknown price/usage is not zero. |
| AC-22 | Independent credentials | OpenAI keys never reach local endpoints or Codex; Codex auth is never used as an API key; unsafe redirects/URLs are rejected. |
| AC-23 | Offline inspection | Attempt and usage reports work without network, Codex, or credentials; legacy missing fields are displayed as unknown. |
| AC-24 | Interrupted finalization | A persisted terminal result can finish local recording/checkpointing after restart without regenerating; unknown remote outcomes are not silently replayed. |
| AC-25 | Protocol drift | A changed installed binary is re-audited; required-field or isolation mismatch fails clearly without deleting strict validation. |

Build replay fixtures for native SSE and JSON-RPC. Include unknown noncritical event fields, nullable phases, usage-only final chunks, malformed final frames, API incomplete/refusal events, Codex `willRetry`, thread system errors, startup errors, and stalled stderr/stdout. Synthetic fixtures must be labeled synthetic; do not present them as captured real traffic.

Environment-dependent release evidence consists of a small explicitly authorized smoke test for each available profile and inspection of an actual Codex rollout. Run a minimal request and a representative real pipeline structured request, not a full book. Report each check as passed, failed, or not run, with its reason. Do not silently install/upgrade Codex, search unrelated credential files, import authentication, or run paid tests merely to make a report green.

## 14. Implementation sequence and deliverables

### Sequence

First inspect the actual checkout, skill, reference repository, and current tests. Record baseline behavior and known failures. Next define the semantic boundary and evidence recorder, then extract llama.cpp without behavioral regression. Add profiles/capability validation and the narrow project migration early enough that both new adapters use them. Implement OpenAI Responses and Codex thin inference, then finish usage/catalog/inspection operations and fault-injection coverage.

Codex may adjust code organization and ordering when the checkout demands it, but must not defer full evidence recording to a later optional phase. Observability is part of every provider's definition of done.

### Required deliverables

Deliver working native adapters, shared recording and normalized contracts, config/profile/catalog data, explicit refresh/import and inspection operations, migration tests, provider contract/fault tests, and user documentation. Keep dependencies minimal and updated through `uv`.

Record implementation decisions and verification results, including installed Codex version/schema identity, located reference/updater paths, deviations from this PRD, exact test commands/results, and checks not run. Keep secrets and real book content out of the repository. Finish with coherent local commits containing only intended changes unless instructed otherwise; do not push, open a pull request, alter repository visibility, or modify `any-item` without a separate request.

The definition of done is not “all three providers returned a string.” It is: **all three run the existing validated pipeline while leaving a durable, truthful, inspectable record of each attempt, including failures, with old projects and human decisions preserved.**

## 15. Reference-repository access for Codex

The Intelitex workspace does not contain `any-item`. Inspect it by creating a unique temporary directory under `/tmp` and making a shallow HTTPS clone of `https://github.com/hipotures/any-item.git`. Record the fetched commit. Do not require a GitHub token for this public reference, reuse an arbitrary existing checkout, overwrite unrelated temporary files, or add it as an Intelitex submodule/dependency.

Start with:

- `src-tauri/crates/adapters/src/codex.rs`
- `src-tauri/crates/adapters/src/ai.rs`
- `src-tauri/src/model_catalog.rs`
- `src-tauri/resources/ai-model-families/`
- the pricing/catalog refresh mechanism identified by content search.

Treat reference files as design evidence. Inspect their relevant tests and caveats. Do not blindly copy model IDs, prices, flags, immediate process-kill behavior, RPC loops that drop early notifications, thread-idle-as-success shortcuts, or unrelated domain features. If the clone fails, document the access failure and continue from Intelitex, the installed skill, and official protocol sources; do not change repository access settings.

## 16. References and verification boundaries

[R1] Intelitex `bookpipe/client.py`, inspected from the connected GitHub repository. Observed blob: `d3ae5fbdb670a71910ca7bb90fc698b2b2cbce2b`. https://github.com/hipotures/intelitex/blob/main/bookpipe/client.py

[R2] Intelitex `bookpipe/engine.py`, inspected from the connected repository. Observed blob: `d2f494f3ab6b6966377aa62d496ce97d9470b855`. https://github.com/hipotures/intelitex/blob/main/bookpipe/engine.py

[R3] `any-item` Codex adapter, observed blob `befd8e0961a3d27bdf6f9955530e55cf2727574f`, and model-family/catalog files supplied during analysis. https://github.com/hipotures/any-item/blob/main/src-tauri/crates/adapters/src/codex.rs

[R4] User-supplied `SKILL.md`, `thin-inference.md`, `audit_installed_protocol.py`, and `openai.yaml`. The implementation agent has the dedicated installed Codex App Server skill. Public supporting reference: https://developers.openai.com/codex/app-server . The locally installed schema/audit remains authoritative.

[R5] Official OpenAI structured-output documentation, checked 2026-09-20. https://developers.openai.com/api/docs/guides/structured-outputs

[R6] Official OpenAI streaming documentation, checked 2026-09-20. https://developers.openai.com/api/docs/guides/streaming-responses

[R7] Official OpenAI token-counting guide and input-token reference, checked 2026-09-20. https://developers.openai.com/api/docs/guides/token-counting and https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens

[R8] Official OpenAI data-control documentation, checked 2026-09-20. https://developers.openai.com/api/docs/guides/your-data

[R9] Intelitex `refs/heads/main`, observed via the GitHub connector while preparing this PRD. https://github.com/hipotures/intelitex/commit/ffd3fa437e6013769dd1dd3f8dabe97a7efa0821

Implementation-time llama.cpp reference: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md . Recheck against the user's actual server version; this document does not certify that server's current feature set.

No live model turn, installed-CLI audit, credential import, complete repository test suite, or pricing-refresh execution was performed while writing this PRD. No current rate table or universal context-window size is certified here. Those measurements/checks belong to the implementation and verification work above.
