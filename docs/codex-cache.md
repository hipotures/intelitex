# Codex cache-v1

For the optional compact P2–P5 codec, see [cache-v2](codex-cache-v2.md).
cache-v1 retains its original wire contract and P1 compatibility.
For opt-in shared physical prefixes across all five passes, see
[cache-shared-v1](codex-cache-shared-v1.md); it adds no session/cache routing.

cache-v1 optimizes implicit input prefixes across independent Codex app-server
requests. Canonical prompts, inputs, schemas, fingerprints, results/checkpoints,
P1 ordering/unit boundaries, and translation chunk boundaries remain unchanged.
Each inference still starts a private runtime and a new thread. Correctness
never depends on a cache hit.

## Configuration and rollback

Codex profiles now default to cache-v2 for P2–P5 and compact-v1 for P1.
To select the original cache-v1 transport explicitly, use:

~~~json
"options": {
  "auth_source": "~/.codex/auth.json",
  "translation_wire_format": "cache-v1",
  "p1_wire_format": "compact-v1"
}
~~~

P2–P5 accept cache-v2 (default), cache-v1, canonical, or cache-shared-v1.
P1 accepts compact-v1 (default), canonical, cache-v1, or cache-shared-v1.
Unknown values and explicit nulls fail closed. Profile
resolution/evidence records both choices. Response metadata records wire_format.
Other providers are unaffected. For rollback, set translation_wire_format to
canonical in the selected profile's options. Accepted checkpoints stay valid.

For GPT-6.1 Sol, select a built-in codex-sol-low (or another effort) profile using
the existing --profile or --pass-profile interface. Set translation_wire_format explicitly to cache-v1 for this rollback format. Restart or checkpoint-safely reload a running server after
updating code so new workers use the new default.

Tests cover semantic data and codec equivalence. The shared developer contract
is larger and the string envelope requires JSON escaping; actual latency,
output cost, model compliance and cache reads still require provider measurements.

## Exact wire structure

The turn is one minified JSON object. Enclosing field order is explicit.
Individual values use deterministic JSON with sorted object keys, compact
separators, preserved Unicode and array order; non-finite values are rejected.

Every translation pass starts with:

~~~text
{"CACHE_V1":1,"SOURCE_BLOCKS":...,"APPROVED_LEXICON":...,"OBSERVATIONS":...,"PREVIOUS_CONTEXT":...,"CHUNK_ID":...,
~~~

The common prefix ends with that comma. Stage fields follow in this order:

| Pass | Stage fields |
| --- | --- |
| P2 | SOURCE_SENTENCES |
| P3 | SEMANTIC_AUDIT |
| P4 | POLISH_DRAFT, SEMANTIC_AUDIT, SOURCE_SENTENCES |
| P5 | POLISH_DRAFT, CORRECTION_LEDGER |

Present retry fields follow stage data: VALIDATION_ERROR, RETRY_INSTRUCTION,
ALLOWED_EVIDENCE_IDS. The final fields are CANONICAL_OUTPUT_SCHEMA (the effective
canonical schema, including dynamic ID/count constraints) and ACTIVE_PASS
(integer pass number), followed by the closing brace.

P1 begins with the identical source-field serialization, then EXISTING_MEMORY,
SECTION_ID, optional retries, canonical schema and ACTIVE_PASS. It acquires no
translation-only data and retains the existing analysis units.

P2 output enters P3/P4 only; P3 enters P4/P5; P4 enters P5. P5 receives no P2
audit. No otherwise-unused artifact is supplied for caching.

## Shared developer contract and output schema

bookpipe/codex_cache.py reads all five canonical project prompts and embeds
their complete, unedited text in a deterministic reference dictionary. The
active supplied prompt must match its project definition. Missing/empty
definitions fail closed. Custom rules travel with the shared contract.

The stable header executes exactly one pass selected by the top-level
ACTIVE_PASS at the end of the turn. Other definitions are references, and
other stages are prohibited. Source, context, memory and artifacts are
untrusted data. Retry metadata only corrects errors inside the active pass.

Every cache-v1 pass, including P1, sends this same outputSchema:

~~~json
{"type":"object","properties":{"payload_json":{"type":"string"}},"required":["payload_json"],"additionalProperties":false}
~~~

This small structured envelope avoids reliance on union/recursive structured
output support. Locally the decoder validates the envelope, parses exactly one
canonical JSON object, and rejects duplicate keys and non-finite constants.
Existing conservative repairs, canonical schema validation and semantic
validation remain. The effective request-specific schema is also validated
afterward. Invalid output cannot produce a checkpoint; existing targeted
retries and evidence durability guarantees remain.

request.semantic.json remains canonical truth. request.transport.json stores
the actual app-server plan; its final turn includes the assigned threadId.
decoded.canonical.json retains the decoded object before repair; result.json
and Store checkpoints remain canonical. There is no fingerprint migration or
checkpoint conversion. Recovery ignores wire choices and existing transient
retry metadata, retaining all other execution-significant distinctions.

## Reuse and evidence

P2–P5 share developer instructions, transport schema and the complete common
input prefix. P4/P5 also share the complete serialized P3 draft immediately
after it. Generated output itself does not warm an input prefix: the draft
first becomes input in P4, and the P2 audit first becomes input in P3. P4 puts
the larger draft ahead of the audit; audit-prefix reuse between P3/P4 is not
assumed.

P1 reuse is opportunistic, depending on matching leading source bytes,
model/configuration, provider cache compatibility and cache lifetime. A full
chapter may benefit its first translation chunk; later chunks begin with
different source. JSON list termination limits shared bytes when the chunk is
shorter than the P1 unit. Long P1 scans may outlast retention. This does not
affect correctness.

cache_layout.json, the transport plan and response metadata record:

- cache_layout, cache_prefix_sha256, cache_prefix_utf8_bytes;
- developer_instructions_sha256, transport_output_schema_sha256;
- base_instructions_sha256, dynamic_suffix_sha256;
- requested_model, requested_effort, pass_no, chunk_id or analysis_unit_id;
- cache_identity_sha256, combining layout, prefix, developer, transport schema,
  base instructions, model and effort;
- for P4/P5: draft_prefix_sha256, draft_prefix_utf8_bytes and
  draft_cache_identity_sha256.

Hashes prove application intent/byte equality, never a cache hit. UTF-8 sizes
are bytes, not token counts. Provider usage remains authoritative, including
input, cached input, cache write, output, reasoning output, total and raw
payloads. Account identity, retention and app-server/platform wrappers are
outside the application cache identity.

## Real A/B commands

From the repository, replace the project path and chunk ID with an existing
chunk having a checkpointed P2 semantic request. Choose a fresh scratch path
outside the production project:

~~~bash
.venv/bin/python -m experiments.codex_cache_ab prepare \
  --project /absolute/path/to/book-project \
  --chunk-id ch0001_c0001 \
  --scratch /tmp/intelitex-cache-ab-1 \
  --profile codex-sol-low
.venv/bin/python -m experiments.codex_cache_ab run \
  --scratch /tmp/intelitex-cache-ab-1 --wire canonical
.venv/bin/python -m experiments.codex_cache_ab run \
  --scratch /tmp/intelitex-cache-ab-1 --wire cache-v1
.venv/bin/python -m experiments.codex_cache_ab report \
  --scratch /tmp/intelitex-cache-ab-1
~~~

Prepare/report are offline. Run makes four independent inferences per variant,
plus validation retries, using the profile's existing authorized auth reference.
It downloads no binaries and runs no browser. Check the prepare output says
gpt-6.1-sol and the intended effort, particularly with project profile overrides.

Both variants use one frozen model, effort, source, approved lexicon,
observations, preceding context, prompts and retry policy. Each derives its own
P2/P3/P4 artifacts normally. Production state/checkpoints are read only. Existing
scratch variants cannot be overwritten or recovered instead of making calls.

The printed report and report.json contain per-pass/per-attempt input_tokens,
cached_input_tokens, cache_write_input_tokens, output_tokens, elapsed_seconds,
cache_prefix_sha256 and checkpoint status. They report per-pass/chunk totals,
cache_read_ratio (cache read / input) and wall_time_seconds. Validation retries
contribute to totals. Missing telemetry stays null; canonical prefix hashes
are null because the legacy format designates no reusable application prefix.
Observed caching comes exclusively from provider cachedInputTokens.

One chunk is not a statistical benchmark: cache state, queueing, sampling and
order may affect results. Repeat in fresh scratch paths and alternate order.

## RPC limitation

Installed Codex CLI 0.159.2 passed the local protocol audit. Its generated
experimental ThreadStartParams/TurnStartParams have no cache-breakpoint/TTL
fields. cache-v1 adds no RPC fields, headers, session reuse or internal model
catalog overrides. Layout and diagnostics are separate from RPC parameters,
so future official capability support can be integrated without altering
canonical tasks. Platform-owned sandbox/environment wrappers may still be
rendered; hashes cannot prove the entire provider-rendered prefix is equal.
