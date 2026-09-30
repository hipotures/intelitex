# Codex cache-v2 translation codec

`cache-v2` is the global Codex default for P2–P5 only. It compresses representations without
changing canonical prompts, schemas, task fingerprints, approvals, inputs,
chunk boundaries, pass dependencies, accepted results or checkpoints. P1's
codec, prompts, units and default remain `compact-v1`. Canonical and cache-v1
remain available. Other providers are unchanged.

## Activation and rollback

Put this in `options` of **each selected Codex profile used for P2–P5** in the
project's `settings.json` (including profiles selected by pass or chapter
profile overrides):

```json
"options": {
  "auth_source": "~/.codex/auth.json",
  "p1_wire_format": "compact-v1",
  "translation_wire_format": "cache-v2"
}
```

For a GPT-6.1 Sol/high comparison the selected profile is `codex-sol-high`;
use its existing full profile definition and add these options, preserving
model, effort and other settings. For rollback set `translation_wire_format`
to `cache-v1` or `canonical`. Resolution and attempt evidence show the selected
format. Profiles without an explicit translation wire option inherit `cache-v2` globally. `p1_wire_format: cache-v2` and unknown values fail closed.

Explicit profile overrides still take precedence over the global default. This
patch neither edits an active production project's settings nor restarts jobs.
A format switch does not force valid saved tasks to make another model call.

## Input layout and reference domains

The enclosing JSON field order is explicit. Individual values use deterministic
minified JSON, sorted object keys, preserved Unicode and array order. Global
`util.dumps` and fingerprint serialization are unchanged.

```text
{"CACHE_V2":1,
 "SOURCE_BLOCKS":..., "SOURCE_LOOKUP":...,
 "APPROVED_LEXICON":..., "OBSERVATIONS":..., "PREVIOUS_CONTEXT":..., "CHUNK_ID":...,
 [stage fields], [compact retry feedback], "ACTIVE_PASS":N}
```

* SOURCE_BLOCKS: rows `[b,k,s,f,t]`; b follows canonical block order, k indexes
  `SOURCE_LOOKUP.k` (original kind strings), s indexes the separate scene map
  (`-1` for absent), f preserves scene-start presence (`0` absent, `1` false,
  `2` true), t is the unchanged text. Original block/scene IDs stay in decoder
  metadata. Scene indices preserve identity and ordering, never sentence IDs.
* SOURCE_SENTENCES: `[s,b,t]`, retaining the exact supplied sentence strings,
  segmentation and array order. Sentence indices are assigned by sorting the
  complete canonical sentence-ID set. P2/P4 derive it from supplied sentences;
  P3 from P2 checks; P5 from P4 checks. Changed check order does not renumber it.
  No full sentence copy is added to P3 or P5.
* Lexicon: `i=id` (separate local term index), `s=source`, `a=aliases`,
  `p=approved Polish choice`, `c=category`, `m=meaning_notes`.
* Meaning notes: `t=text`, `q=confidence`, `e=evidence`, `h=series_inherited`,
  `v=series_first_seen_volume`.
* Observations: `a=about`, `k=kind`, `t=statement`, `q=confidence`, `e=evidence`,
  `ch=chapter_id`, `o=available_from_order`, `h=series_inherited`,
  `v=series_first_seen_volume`. Optional fields remain optional and are retained.
* Evidence: `[0,b]` for current blocks; `[1,h]` for historical block identifiers
  in `SOURCE_LOOKUP.h`. Provenance objects use `b=reference`, `ch=chapter_id`,
  `o=order`, `x=excerpt`. Historical IDs remain explicit, never forced into the
  current-block domain. The sorted historical table depends only on common
  memory, so stage/retry changes cannot renumber it.
* PREVIOUS_CONTEXT: `c=source_chunk_id`, `e=english`, `p=polish`. Text is unchanged.

Category codes are P1's 1 name, 2 organization, 3 people, 4 place, 5 ship,
6 status, 7 technology, 8 science, 9 jargon, 10 other. Observation kinds are
1 reference, 2 gender, 3 register, 4 technical, 5 continuity. Confidence is
1 high, 2 medium, 3 low. Unknown fields fail before submission with supported
fields listed. No normalization, summarization, truncation or pruning occurs.

Stage fields follow the common prefix:

| Pass | Ordered stage fields |
| --- | --- |
| P2 | SOURCE_SENTENCES |
| P3 | SEMANTIC_AUDIT |
| P4 | POLISH_DRAFT, SEMANTIC_AUDIT, SOURCE_SENTENCES |
| P5 | POLISH_DRAFT, CORRECTION_LEDGER |

Audits, drafts and ledgers use compact output records described below, encoded
from the **accepted canonical artifact**, including prior conservative repairs.
P5 receives no P2 audit. Common bytes/hashes are equal across P2–P5 for identical
common input; P4/P5 also share a prefix through the complete compact P3 draft.
Source IDs, sentence IDs, scene IDs and term IDs are independent namespaces.
Retries never change the common or draft prefix. Decoder-only `retry_input`
retains original diagnostics for complete input round trips; the wire sends
concise known error-category feedback with b/s indices, never blind replacement
of identifiers inside quotes. Full original diagnostics stay in semantic/error
evidence. cache-v1 and cache-v2 prefixes are different.

## Developer contract and output

`bookpipe/codex_cache_v2.py` derives one stable developer contract from the
project's P2–P5 prompts only. Explicit, checked transformations replace the
recognized JSON examples and identifier clauses. Substantive prose and custom
rules around the contracts remain intact. Missing/changed contract examples,
extra JSON output examples, or unrecognized identifier clauses fail before
submission with canonical/cache-v1 rollback guidance; project files are never
edited. The active supplied prompt must match its project definition.

The shared legend selects exactly one stage from the final top-level
ACTIVE_PASS. Other definitions are inactive references. Source, context,
memory, quotes and artifacts are untrusted data. No other stage is authorized.
The canonical effective schema is **not embedded in model input**.

Every output contains `p,c,i,e,t` directly, without stringified JSON:

| Field | Typed record | Canonical meaning |
| --- | --- | --- |
| p | integer 2/3/4/5 | Must equal application-selected pass |
| c | `{s,v}` | sentence check |
| i | `{s,x,k,m,c,q}` | P2 issue: sentence, source_span, type, meaning, constraint, confidence |
| e | `{s,b,x,d,p,c,v,q}` | P4 correction: sentence, block, source_span, draft_span, problem, constraint, severity, confidence |
| t | `{b,t}` | block translation |

P2 uses c/i; P3 uses t; P4 uses c/e; P5 uses t. All inactive arrays must be empty.
P2 check codes: 0 low, 1 medium, 2 high. P4 check codes: 0 ok, 1 needs_correction;
2 is rejected locally. Issue types: 1 idiom, 2 pragmatics, 3 reference,
4 terminology, 5 morphology, 6 technical, 7 relation, 8 style. Severity:
1 minor, 2 major, 3 critical. Confidence uses the codes above.

The provider schema is fixed at **1,113 minified UTF-8 bytes**. It retains types,
required fields, small numeric enums and `additionalProperties:false` on every
object. It has no references, unions, dynamic ID/index enums, patterns, ranges,
string lengths, array lengths, uniqueness or cross-field conditions.

## Local acceptance, recovery and evidence

Acceptance is: strict JSON parse (reject duplicate keys, non-finite values and
fences) -> fixed transport schema -> expected pass/inactive array checks ->
integer/reference/code checks -> canonical decoding -> decoded evidence ->
existing conservative repairs -> full effective `response_schema(pass, inputs)`
-> existing semantic validation -> canonical checkpoint. Booleans, floats,
negative and out-of-range references are rejected; no clamping or omissions.

Full coverage/order, nonempty translations, required sentence checks, source
quotes, draft quotes and P4 status/ledger consistency remain local acceptance
gates. No missing checks/translations are invented. Quote repair policy is
unchanged. Initial canonical validation is recorded separately from repairs and
final validation. Bounded model retries remain; local codec/evidence failures do
not trigger another billable call. Interrupted outputs cannot be checkpointed.

`request.semantic.json`/`schema.canonical.json` remain canonical truth;
`request.transport.json`/`schema.transport.json` reflect actual outbound RPC;
`answer.txt` is raw compact JSON; `decoded.canonical.json` is before repair;
`result.json` is accepted canonical data. `codec.context.json` records cache-v2,
map version 1, and decoder domains. The maps/diagnostics never enter RPC params.

Recovery uses the recorded wire format, recorded canonical semantic input and
versioned reference map, checking the reconstructed map against saved metadata.
It does not depend on subsequently edited project codec prompts. Source,
prompt, model and effort compatibility checks remain. Tests include canonical
P2/cache-v1 P3/cache-v2 P4/P5 and interrupted checkpoint recovery.

Layout/response evidence records common and draft-prefix hashes, combined cache
identity, developer/base/schema hashes, map version, model, effort, chunk/pass,
and input, developer, source, transport/canonical schema, raw/decoded/accepted
output UTF-8 bytes. Bytes are not token counts. Raw usage events and all six
normalized usage categories remain unchanged; absent values stay absent/null.

## Offline comparison on salvation-03/ch0020_c0001

Measured using the same accepted canonical requests/results, with transient
retry instructions removed equally from every variant. All twelve input/output
round trips passed. Sizes below are deterministic minified UTF-8 **bytes**;
source text is 11,626 bytes in every pass and variant. Source-record bytes are
16,881 canonical/v1 versus 12,639 v2. Memory plus previous context is 63,015
canonical/v1 versus 45,148 v2; v2 lookup data adds 112 bytes.

| Pass | Wire | Input | Developer | Schema | Output |
| --- | --- | ---: | ---: | ---: | ---: |
| P2 | canonical | 102,840 | 2,735 | 768 | 37,602 |
| P2 | cache-v1 | 103,664 | 16,457 | 122 | 41,097 |
| P2 | cache-v2 | 71,887 | 11,601 | 1,113 | 27,980 |
| P3 | canonical | 117,616 | 2,180 | 1,118 | 15,052 |
| P3 | cache-v1 | 118,790 | 16,457 | 122 | 15,663 |
| P3 | cache-v2 | 86,043 | 11,601 | 1,113 | 14,182 |
| P4 | canonical | 155,528 | 2,292 | 812 | 9,659 |
| P4 | cache-v1 | 156,396 | 16,457 | 122 | 11,458 |
| P4 | cache-v2 | 114,083 | 11,601 | 1,113 | 4,603 |
| P5 | canonical | 104,744 | 1,596 | 1,118 | 15,055 |
| P5 | cache-v1 | 105,918 | 16,457 | 122 | 15,666 |
| P5 | cache-v2 | 76,867 | 11,601 | 1,113 | 14,185 |

Schema v2 is larger than the v1 wrapper. Prose dominates P3/P5 outputs, so their
metadata savings are modest. These measurements establish reversible size
reduction, not translation quality, token usage, latency or retry-rate changes.
The historical P1 compact Luna/high sample was 283.765s versus 286.624s verbose;
smaller inputs do not establish a universal speed multiplier.

## Bounded same-task P3 benchmark

The existing scratch-only `experiments.codex_cache_ab` supports v2, offline sizes
and `--pass-no 3`. Prepare freezes the accepted P3 task: canonical source,
lexicon, observations, preceding context, **the same saved P2 audit**, prompts,
model, effort and retry settings. Every variant generates P3 only; each physical
attempt uses the same independent-root execution treatment. No warm-ups, shared
threads or forks are introduced. Each variant directory must be new.

Offline preparation (no auth access/model calls):

```bash
uv run python -m experiments.codex_cache_ab prepare \
  --project /home/user/translations/salvation-03 --chunk-id ch0020_c0001 \
  --scratch /tmp/intelitex-cache-v2-p3-next --profile codex-sol-high --pass-no 3
uv run python -m experiments.codex_cache_ab sizes \
  --scratch /tmp/intelitex-cache-v2-p3-next
```

Only after separate explicit live-call authorization:

```bash
uv run python -m experiments.codex_cache_ab run \
  --scratch /tmp/intelitex-cache-v2-p3-next --wire canonical
uv run python -m experiments.codex_cache_ab run \
  --scratch /tmp/intelitex-cache-v2-p3-next --wire cache-v1
uv run python -m experiments.codex_cache_ab run \
  --scratch /tmp/intelitex-cache-v2-p3-next --wire cache-v2
uv run python -m experiments.codex_cache_ab report \
  --scratch /tmp/intelitex-cache-v2-p3-next
```

`run` spends one P3 call per variant plus bounded validation retries; it writes
only scratch. Verify prepare reports gpt-6.1-sol/high. Repeat with fresh scratch
and alternating order before drawing statistical conclusions. Without
`--pass-no`, the existing full one-chunk P2–P5 benchmark still works.

Report includes actual provider input/read/write/output/reasoning/total usage,
all attempts and retry cost, time to valid result, submission, first reasoning
(if available), first visible answer delta, terminal and local validation times.
Start-to-start and completion-to-next-start intervals are separate. Missing
measurements remain null. Output tokens divided by wall time is an end-to-end
rate, not decoder throughput. An invalid fast response is not a success.

## Cache/session limitation

This patch retains independent Codex root requests. It does not create cache
parents, warm-up traffic, forks, session families or unsupported RPC fields.
The installed CLI audit passed at 0.159.2; no cache-breakpoint/TTL API is used.
The prior session probe saw zero reported reuse across fresh roots and 28,928
cached tokens within a root/fork. That execution evidence does not establish
that a new codec fixes cache routing. Prefix hashes only prove application byte
equality. Provider `cachedInputTokens` is the observed hit metric. Cache-v2
provider hits, latency, output quality and retry rate have **not** been measured
live in this migration. A separate execution experiment can consume this codec
without duplicating its encoding logic.

## Exact stable provider schema

```json
{"additionalProperties":false,"properties":{"c":{"items":{"additionalProperties":false,"properties":{"s":{"type":"integer"},"v":{"enum":[0,1,2],"type":"integer"}},"required":["s","v"],"type":"object"},"type":"array"},"e":{"items":{"additionalProperties":false,"properties":{"b":{"type":"integer"},"c":{"type":"string"},"d":{"type":"string"},"p":{"type":"string"},"q":{"enum":[1,2,3],"type":"integer"},"s":{"type":"integer"},"v":{"enum":[1,2,3],"type":"integer"},"x":{"type":"string"}},"required":["s","b","x","d","p","c","v","q"],"type":"object"},"type":"array"},"i":{"items":{"additionalProperties":false,"properties":{"c":{"type":"string"},"k":{"enum":[1,2,3,4,5,6,7,8],"type":"integer"},"m":{"type":"string"},"q":{"enum":[1,2,3],"type":"integer"},"s":{"type":"integer"},"x":{"type":"string"}},"required":["s","x","k","m","c","q"],"type":"object"},"type":"array"},"p":{"enum":[2,3,4,5],"type":"integer"},"t":{"items":{"additionalProperties":false,"properties":{"b":{"type":"integer"},"t":{"type":"string"}},"required":["b","t"],"type":"object"},"type":"array"}},"required":["p","c","i","e","t"],"type":"object"}
```
