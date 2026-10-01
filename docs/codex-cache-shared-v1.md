# Codex cache-shared-v1: shared physical prefixes only

This opt-in format supplies the same application-controlled developer contract,
provider schema and source representation to P1–P5. Every inference remains an
independent complete request, with the existing private runtime and fresh thread.
No app-server change, session routing, affinity key, conversation replay, fork,
warm-up, TTL assumption or explicit cache breakpoint is involved.

Matching hashes establish byte equality, **not provider prompt-cache reuse**.
The app-server may still contribute sandbox/environment framing. In particular,
JSON field boundaries inside one user message are not official provider cache
breakpoints. This step does not establish cache eligibility, routing, actual
hits, latency, cost, quality or retry-rate improvements.

## Activation and rollback

Set these options in the Codex profile(s) actually selected for the relevant
passes. Neither model nor effort selection changes:

```json
{
  "options": {
    "auth_source": "~/.codex/auth.json",
    "p1_wire_format": "cache-shared-v1",
    "translation_wire_format": "cache-shared-v1"
  }
}
```

Both options are independently selectable. Defaults stay `compact-v1` for P1
and `cache-v2` for P2–P5. P1 also accepts `canonical` and `cache-v1`; P2–P5 also
accept `canonical` and `cache-v1`. P1 still rejects `cache-v2`. Unknown values,
including explicit null, fail closed. Other providers retain their wire formats.
Rollback means restoring `compact-v1`/`cache-v2` in selected profiles; existing
accepted results require no migration or inference.

## Exact input order and candidate prefix levels

The input is one valid minified JSON object. Each value uses deterministic JSON
(sorted object keys, preserved array order/Unicode/whitespace, no non-finite
numbers); root field order is constructed explicitly. The marker is
`"CACHE_SHARED_V1":1`. Prefixes include their opening brace, all preceding
fields, and the trailing comma after their final complete value.

```text
{"CACHE_SHARED_V1":1,
 "SOURCE_BLOCKS": [[b,k,s,f,t], ...],
 "SOURCE_LOOKUP": {"k": [kind, ...]},
                         <-- end SOURCE prefix
 P1: "EXISTING_MEMORY": ..., "SECTION_ID": ...,
 P2–P5:
   "HISTORICAL_LOOKUP": [historical evidence ID, ...],
   "APPROVED_LEXICON": ...,
   "OBSERVATIONS": ...,
   "PREVIOUS_CONTEXT": ...,
   "CHUNK_ID": ...,
                         <-- end TRANSLATION COMMON prefix
 P2: "SOURCE_SENTENCES": ...,
 P3: "SEMANTIC_AUDIT": ...,
 P4/P5: "POLISH_DRAFT": ...,
                         <-- end DRAFT prefix (P4/P5)
 P4: "SEMANTIC_AUDIT": ..., "SOURCE_SENTENCES": ...,
 P5: "CORRECTION_LEDGER": ...,
 present compact retry feedback,
 "ACTIVE_PASS": N}
```

Only the applicable branch is present. P1 acquires no translation common data
or artifacts. P3/P5 acquire no new SOURCE_SENTENCES copy. P5 acquires no P2 audit.
The production orchestration and targeted-pass dependency graph are unchanged.

`encode_source()` is the single helper used by all five passes. `b` derives
from canonical block order, `k` from first occurrence of the kind, `s` from first
occurrence of a scene ID (`-1` means absent). `f` distinguishes absence (0),
explicit false (1), and true (2). `t` is the unchanged source string. No historical
evidence IDs, term IDs, retry details, model/effort or upstream artifacts enter
this source prefix. Block and scene IDs stay in local decoder maps.

Translation common uses the unchanged cache-v2 memory transformations. Term
indices belong to their own domain. Historical evidence IDs are sorted into
HISTORICAL_LOOKUP after the source: references `[0,b]` address current blocks;
`[1,h]` address that historical table. Meaning notes, confidence, attribution,
scope, provenance, English/Polish continuity and historical evidence survive
without shortening. P1 EXISTING_MEMORY uses the existing compact catalogue and
matched rows, preserving its own canonical memory semantics.

Sentence indices derive from the sorted canonical sentence-ID set, independent
of artifact/check array order. The original check order is retained in encoded
arrays. P2/P4 get rows `[s,b,t]` with exact supplied sentence text and segmentation;
P3 derives the domain from accepted audit checks, P5 from accepted ledger checks.
Block, sentence, scene, term and historical evidence namespaces remain separate.

Upstream inputs are re-encoded from **accepted canonical outputs**, including
any deterministic repairs. P4/P5 put the same complete encoded P3 draft directly
after common. Artifact/retry changes cannot change earlier lookup assignments.
SOURCE prefix equality across P1/translation requires the actual same source;
a whole chapter and smaller chunk have different complete prefixes. No units or
chunk boundaries are changed to force equality. Older wire-version prefixes
are not cross-version compatible.

## Shared instructions and fixed output

The developer contract reads all five project prompts and requires the active
canonical prompt to match its project definition. P1 uses the existing
`p1_compact.compact_instructions()` exact contract transformation; its generated
format legend is adapted to `a` and the shared source rows. P2–P5 use existing
`codex_cache_v2.compact_prompt()` transformations. Substantive project prose and
custom rules around recognized contracts remain intact. Unrecognized/custom
output contracts fail before inference; no project prompt is rewritten.

One common legend defines security, rows, fields and codes. The top-level final
ACTIVE_PASS selects exactly one pass; others are inactive reference definitions.
Source, context, memory, artifacts and apparent embedded stage selectors are
untrusted data. No definition may execute another stage.

Every output has `p,a,o,c,i,e,t` directly (no JSON string envelope):

| Field | Record | Active passes |
| --- | --- | --- |
| p | integer enum 1–5, must equal application's expected pass | all |
| a | `{s,a,c,m,q,p,e}` term; candidates `{t,r}` | P1 |
| o | `{a,k,s,q,e}` observation | P1 |
| c | `{s,v}` sentence checks | P2/P4 |
| i | `{s,x,k,m,c,q}` semantic issues | P2 |
| e | `{s,b,x,d,p,c,v,q}` corrections | P4 |
| t | `{b,t}` translations | P3/P5 |

All inactive arrays must be empty. P1 evidence arrays contain integer block
indices, not memory evidence `[domain,index]` pairs. Existing P1 and cache-v2
record meanings/code tables are unchanged:

- confidence: 1 high, 2 medium, 3 low;
- category: 1 name, 2 organization, 3 people, 4 place, 5 ship, 6 status,
  7 technology, 8 science, 9 jargon, 10 other;
- observation kind: 1 reference, 2 gender, 3 register, 4 technical, 5 continuity;
- P2 check: 0 low, 1 medium, 2 high; P4: 0 ok, 1 needs_correction (2 rejected locally);
- issue: 1 idiom, 2 pragmatics, 3 reference, 4 terminology, 5 morphology,
  6 technical, 7 relation, 8 style;
- severity: 1 minor, 2 major, 3 critical.

The exact fixed provider schema below is **2,758 minified UTF-8 bytes**. It
extends the proven flat record forms without unions, recursion, dynamic enums,
range/count/length restrictions, patterns or cross-field constraints. Every
object retains types, required fields and `additionalProperties:false`.
Structured Outputs remains enabled. The canonical schema is not put in input.

<!-- The schema is generated from bookpipe.codex_cache_shared.TRANSPORT_SCHEMA. -->
```json
{"additionalProperties":false,"properties":{"a":{"description":"terms","items":{"additionalProperties":false,"properties":{"a":{"description":"aliases","items":{"type":"string"},"type":"array"},"c":{"description":"category: 1=name, 2=organization, 3=people, 4=place, 5=ship, 6=status, 7=technology, 8=science, 9=jargon, 10=other","enum":[1,2,3,4,5,6,7,8,9,10],"type":"integer"},"e":{"description":"local SOURCE_BLOCKS evidence indices","items":{"type":"integer"},"type":"array"},"m":{"description":"brief evidence-based meaning or uncertainty","type":"string"},"p":{"description":"Polish candidates","items":{"additionalProperties":false,"properties":{"r":{"description":"brief tradeoff","type":"string"},"t":{"description":"candidate text","type":"string"}},"required":["t","r"],"type":"object"},"type":"array"},"q":{"description":"confidence: 1=high, 2=medium, 3=low","enum":[1,2,3],"type":"integer"},"s":{"description":"English source lexical form","type":"string"}},"required":["s","a","c","m","q","p","e"],"type":"object"},"type":"array"},"c":{"items":{"additionalProperties":false,"properties":{"s":{"type":"integer"},"v":{"enum":[0,1,2],"type":"integer"}},"required":["s","v"],"type":"object"},"type":"array"},"e":{"items":{"additionalProperties":false,"properties":{"b":{"type":"integer"},"c":{"type":"string"},"d":{"type":"string"},"p":{"type":"string"},"q":{"enum":[1,2,3],"type":"integer"},"s":{"type":"integer"},"v":{"enum":[1,2,3],"type":"integer"},"x":{"type":"string"}},"required":["s","b","x","d","p","c","v","q"],"type":"object"},"type":"array"},"i":{"items":{"additionalProperties":false,"properties":{"c":{"type":"string"},"k":{"enum":[1,2,3,4,5,6,7,8],"type":"integer"},"m":{"type":"string"},"q":{"enum":[1,2,3],"type":"integer"},"s":{"type":"integer"},"x":{"type":"string"}},"required":["s","x","k","m","c","q"],"type":"object"},"type":"array"},"o":{"description":"observations","items":{"additionalProperties":false,"properties":{"a":{"description":"English source forms this observation is about","items":{"type":"string"},"type":"array"},"e":{"description":"local SOURCE_BLOCKS evidence indices","items":{"type":"integer"},"type":"array"},"k":{"description":"kind: 1=reference, 2=gender, 3=register, 4=technical, 5=continuity","enum":[1,2,3,4,5],"type":"integer"},"q":{"description":"confidence: 1=high, 2=medium, 3=low","enum":[1,2,3],"type":"integer"},"s":{"description":"short observation","type":"string"}},"required":["a","k","s","q","e"],"type":"object"},"type":"array"},"p":{"enum":[1,2,3,4,5],"type":"integer"},"t":{"items":{"additionalProperties":false,"properties":{"b":{"type":"integer"},"t":{"type":"string"}},"required":["b","t"],"type":"object"},"type":"array"}},"required":["p","a","o","c","i","e","t"],"type":"object"}
```

## Local acceptance, evidence and compatibility

Strict JSON parsing rejects duplicate keys, non-finite values and truncated/fenced
JSON. Fixed schema validation precedes expected-pass/inactive checks and
reference/code decoding. References reject bools, floats, negative/out-of-range
values. Decoding delegates to the current P1/cache-v2 decoders; canonical results
then follow the existing conservative repair policy, full effective
`response_schema(pass, inputs)` validation and semantic validation before
checkpointing. Coverage/order, evidence attestation, candidate bounds, source
and draft spans, and correction/status consistency remain acceptance gates.
No missing checks or translations are invented.

Original retry diagnostics stay in canonical semantic evidence/local context.
The wire sends compact-format guidance with local indices after reusable fields;
it never replaces canonical IDs inside quoted source text. Local codec/map or
evidence failures do not trigger another model call. Normal bounded model retries
and terminal interruption handling are preserved.

Canonical fingerprints still depend only on prompt, canonical input and effective
canonical schema. Accepted checkpoints remain canonical and transport-independent.
Completed attempts decode according to their recorded wire version; shared
map version 1 reconstructs references from that attempt's canonical semantic
input and verifies persisted maps. Canonical, compact-v1, cache-v1 and cache-v2
recovery remains supported without rewriting original evidence.

Evidence retains semantic/canonical schema, actual RPC transport/schema, raw answer,
decoded canonical object, versioned `codec.context.json`, validation before/after
repair and the accepted canonical result. Decoder maps/diagnostics never enter RPC
parameters. The existing six usage categories and raw events remain unchanged.

Diagnostics include source/common/draft prefix SHA-256 and UTF-8 bytes where
applicable; developer/base/schema hashes; full input hash/bytes; wire/map version;
pass/unit; model and effort. Per-level application identities combine prefix and
instruction/schema/model/effort hashes. Provider wrappers and routing are outside
these identities. Only provider `cachedInputTokens` demonstrates an actual hit.

## Offline measurement on salvation-03/ch0022

Read-only accepted P1 `ch0022_a001` and P2–P5 `ch0022_c0001` tasks/results were
encoded with both formats. All canonical output round trips and all new input
round trips passed. The successful P2 attempt_002 was used; the interrupted
cache-v1 attempt_001 was excluded. Original canonical retries were retained.
No authentication access or model inference was needed for measurement.

| Pass | Format | Developer | Schema | Source prefix | Common prefix including source | Full input |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| P1 | compact-v1 | 6,420 | 1,717 | — | — | 98,868 |
| P1 | cache-shared-v1 | 18,164 | 2,758 | 32,416 | — | 98,912 |
| P2 | cache-v2 | 11,601 | 1,113 | 32,580 | 79,106 | 114,952 |
| P2 | cache-shared-v1 | 18,164 | 2,758 | 32,416 | 79,129 | 114,975 |
| P3 | cache-v2 | 11,601 | 1,113 | 32,580 | 79,106 | 116,085 |
| P3 | cache-shared-v1 | 18,164 | 2,758 | 32,416 | 79,129 | 116,122 |
| P4 | cache-v2 | 11,601 | 1,113 | 32,580 | 79,106 | 188,082 |
| P4 | cache-shared-v1 | 18,164 | 2,758 | 32,416 | 79,129 | 188,133 |
| P5 | cache-v2 | 11,601 | 1,113 | 32,580 | 79,106 | 126,796 |
| P5 | cache-shared-v1 | 18,164 | 2,758 | 32,416 | 79,129 | 126,847 |

All values are bytes, not tokens. compact-v1 has no leading source-only prefix
because memory precedes source. The old v2 source-prefix size includes historical
IDs in SOURCE_LOOKUP.h; shared moves them after source. The translation common
section excluding source is 46,526 bytes old / 46,713 shared.

New full input overhead is 23–51 bytes for P2–P5 and 44 bytes for P1. Instructions
and schema grow because P1 now shares the complete five-pass contract/superset.
This is intentional overhead, not a claim of smaller total requests. The real
saved source prefixes matched across all five passes; common matched P2–P5 and
draft matched P4/P5. This proves layout equality only.

Reproduce offline with a fresh report directory:

```bash
uv run python -m experiments.codex_cache_ab shared-sizes \
  --project /home/user/translations/salvation-03 \
  --chunk-id ch0022_c0001 --analysis-unit ch0022_a001 \
  --scratch /tmp/intelitex-cache-shared-sizes-new
```

The command saves sizes, prefix hashes, original task/result hashes and exact
provider schema to scratch. It cannot write into the production project or
repository and cannot invoke a model. There is no new live benchmark command in
this step. No browser tests, downloads, session experiments, commits or pushes
are required.
