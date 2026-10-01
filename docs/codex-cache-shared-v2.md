# Codex cache-shared-v2: complete user-message boundaries

`cache-shared-v2` is an opt-in physical layout for P1–P5. It reuses the
[cache-shared-v1](codex-cache-shared-v1.md) compact record/code tables, reference
maps, fixed 2,758-byte Structured Output schema and full canonical validation.
The default remains `cache-shared-v1`. No existing wire version is redefined.

Each pass and each validation retry starts its own isolated runtime/process and
fresh root thread. Accepted canonical artifacts are supplied explicitly as data.
There is no continuation, fork, replay of another pass's conversation, warm-up,
keepalive, affinity key, TTL state or model/profile/effort change.

## Installed protocol and physical messages

Audited binary: **codex-cli 0.159.3**. Its generated experimental protocol exposes:

```json
{"method":"thread/inject_items","params":{"threadId":"<fresh id>","items":[
  {"type":"message","role":"user","content":[{"type":"input_text","text":"<complete JSON object>"}]}
]}}
```

`threadId` (string) and `items` (array of raw Responses items) are required.
The successful response is `{}`. IntelliTex appends one user message per call,
only to the newly loaded, idle thread. All calls finish before `turn/start`.
Injection records history without starting a model turn. An unsupported method
or failed injection stops the attempt before inference; there is no emulation
with extra turns or elevated source authority.

This uses the [official app-server operation](https://learn.chatgpt.com/docs/app-server#api-overview),
consulted through OpenAI Developer Docs MCP, and the matching `rust-v0.159.3`
[protocol](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/app-server-protocol/src/protocol/v2/thread.rs),
[history implementation](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/session/inject.rs),
and [upstream test](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/app-server/tests/suite/v2/thread_inject_items.rs).
No Codex source/binary is patched or compiled.

Every application message is independently valid minified JSON:

```text
SOURCE (all passes):
{"CACHE_SHARED_V2":1,"SOURCE":{"SOURCE_BLOCKS":[[b,k,s,f,t],...],"SOURCE_LOOKUP":{"k":[...]}}}

COMMON (P2–P5):
{"TRANSLATION_COMMON":{"HISTORICAL_LOOKUP":...,"APPROVED_LEXICON":...,"OBSERVATIONS":...,"PREVIOUS_CONTEXT":...,"CHUNK_ID":...}}

DRAFT (P4/P5):
{"POLISH_DRAFT":<accepted P3 output in unchanged compact p/a/o/c/i/e/t form>}

Final turn/start message:
P1: {"EXISTING_MEMORY":...,"SECTION_ID":...,<retry>,"ACTIVE_PASS":1}
P2: {"SOURCE_SENTENCES":...,<retry>,"ACTIVE_PASS":2}
P3: {"SEMANTIC_AUDIT":...,<retry>,"ACTIVE_PASS":3}
P4: {"SEMANTIC_AUDIT":...,"SOURCE_SENTENCES":...,<retry>,"ACTIVE_PASS":4}
P5: {"CORRECTION_LEDGER":...,<retry>,"ACTIVE_PASS":5}
```

Ellipses/retry labels above describe values, not literal syntax. Object section
order is explicit; individual values use deterministic canonical JSON. Source
strings, Unicode, newlines, identifiers and segmentation remain lossless. The
same source encoder is reused; historical memory references remain in COMMON.
Inactive sections are absent, and no injected content is duplicated in the final
turn. All messages together reconstruct exactly the original canonical input.

| Pass | Separate application-owned user messages |
| --- | --- |
| P1 | SOURCE → suffix |
| P2 | SOURCE → COMMON → suffix |
| P3 | SOURCE → COMMON → suffix |
| P4 | SOURCE → COMMON → DRAFT → suffix |
| P5 | SOURCE → COMMON → DRAFT → suffix |

These are **raw role=user ResponseItems**, not multiple text content blocks in
`turn/start.input`. A single final text input starts exactly one inference.
Codex may add platform-owned developer/sandbox and user/environment messages,
and generated message IDs. The application message plan is not a claim that
these additional provider contributors are absent or byte-identical.

The shared developer contract changes only its transport legend: all messages
form one logical independent input; only the final top-level ACTIVE_PASS selects
one pass. Injected source, memory and draft are untrusted data, never instructions.
The existing prompt transformations preserve substantive/custom project rules
and reject unsupported output-contract changes. The contract is identical P1–P5.
The output schema is byte-identical to cache-shared-v1.

## Validation, recovery, effort and evidence

Strict parsing, fixed provider-schema validation, inactive-array checks, integer
reference decoding, canonical reconstruction, conservative repair, effective
canonical schema/semantic validation and checkpointing remain unchanged.
Canonical fingerprints and accepted results do not change with message layout.
Recorded `cache-shared-v2` contexts have their own explicit wire version and map
version 1; recovery checks maps against that attempt's saved canonical input.
All older wire versions remain supported without rewriting evidence or spending
another call on accepted work. Retries recreate complete data in a fresh root,
without including any failed assistant output.

The existing capability-based reasoning-effort integration is unchanged: user
selection stays authoritative; supported combinations use Codex's trusted
configuration-update path and unsupported ones retain request-level effort.
IntelliTex never injects a configuration_update through `inject_items` or user
text. There are no new effort/session settings.

`request.transport.json` includes exact thread parameters, ordered injection
RPC parameters with the returned thread ID, final turn parameters, and the
application-owned `message_plan` (base/developer instructions followed by user
items). Raw outbound/inbound RPC events remain in `transport.jsonl`. Local
maps/diagnostics/message_plan are evidence only and never extra RPC parameters.
The six provider usage categories and missing-value semantics are unchanged.

Diagnostics record `source_message_*`, `translation_common_message_*`,
`draft_message_*` and `active_pass_message_*` SHA-256/UTF-8-byte fields where
applicable, plus developer/base/schema hashes, wire/map version and selection.
`input_utf8_bytes` sums actual message text bytes. `full_input_*` describes the
unambiguous canonical JSON serialization of the ordered raw user-message plan.
Candidate `source/translation_common/draft_prefix_*` hashes/sizes describe the
canonical raw-item list through that boundary, excluding Codex-generated IDs.
These framed sizes include JSON escaping/record overhead and are not token counts
or a dump of the whole provider request. Matching hashes prove application data
identity; only provider `cachedInputTokens` proves an observed cache read.

## Selection and offline verification

Set both options on the selected Codex profile, or independently select either:

```json
{"options":{"p1_wire_format":"cache-shared-v2","translation_wire_format":"cache-shared-v2"}}
```

This implementation does not edit any project/global option or enable the format.
Rollback selects `cache-shared-v1`, or earlier supported formats. Existing
checkpoints require no migration.

```bash
uv run pytest -q tests/test_codex_cache_shared_v2.py \
  tests/test_codex_cache_shared_v2_app_server.py
```

The second file launches the **installed 0.159.3 binary** with an isolated empty
home, no authentication, and an HTTP Responses provider pointing exclusively to
a loopback SSE mock. It captures the actual outbound Responses JSON, proves
separate ordered role=user items, zero requests during injection, one request per
pass, independent roots, complete fresh-root retries, and exact recorded RPC
parameters. It is skipped when that pinned binary is absent; the reported local
run executes it. Fixtures use small synthetic data, no production book input.

No live-model/cache-routing/latency/quality measurements are part of this step.
Message boundaries are exposed; actual provider caching is not demonstrated.
