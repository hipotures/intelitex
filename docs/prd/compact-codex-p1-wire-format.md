# PRD — Compact Codex Pass-1 wire format with checkpoint-safe upgrade

## Status

Productionization specification based on the completed Pass-1 compact-transport experiments.

This change is deliberately narrow. It must reduce Codex Pass-1 wire/token overhead without changing Intelitex's canonical project formats, semantic task identity, review data, SQLite state, existing checkpoints, or the P2-P5 translation pipeline.

## Problem

Large Pass-1 Codex requests currently send canonical verbose JSON and a request-specific verbose output schema. In the measured large `ch0005_a001` case, the compact experimental transport reduced provider-reported input tokens from 57,334 to about 41k (~28%) and reduced the transport schema from 15,399 bytes to 1,717 bytes.

The compact experiment also demonstrated that a simple flat structured-output schema is important. Recursive or heavily abstracted schemas are explicitly undesirable: prior empirical testing showed that structurally complicated Codex schemas can increase server latency by several times or even to tens of minutes.

The production implementation must preserve the existing canonical data model. Compact JSON is a wire format only.

## User-critical upgrade constraint

A real translation is currently in progress and may be partially completed when this code is pulled. A code upgrade must not force accepted work to be recomputed.

The current pipeline computes task fingerprints from canonical:

- project prompt;
- canonical inputs;
- canonical response schema.

That semantic identity must remain stable.

A translation interrupted before the upgrade must resume after the upgrade from its existing accepted checkpoints. If a unit has completed P2 and P3 but not P4/P5, the new version must reuse the completed P2/P3 jobs and continue from the first unfinished pass. If 12 of 34 units are complete, the new version must continue with the remaining units rather than restart completed work.

No migration of canonical project content is required or desired.

## Goals

1. Productionize the empirically tested compact wire representation for **Codex Pass 1**.
2. Keep all canonical Intelitex data and persistent files unchanged in shape.
3. Keep the existing task fingerprint exactly based on the canonical prompt, canonical inputs, and canonical schema.
4. Preserve checkpoint/recovery compatibility across the upgrade.
5. Keep P2-P5 behavior unchanged.
6. Keep llama.cpp and native OpenAI behavior unchanged.
7. Preserve complete communication evidence: canonical semantic request plus actual compact transport request/schema/answer.
8. Provide a rollback switch to use the canonical Codex P1 wire representation without changing semantic task identity.
9. Keep the compact schema flat and simple; do not introduce recursive/abstract schema machinery.

## Non-goals

This change does **not**:

- compact `book_memory.json`;
- compact `terms.review.json`;
- compact approved lexicon files;
- change SQLite term/fact/chunk/job formats;
- migrate existing projects;
- change frozen book/source IDs;
- change human review behavior;
- change P1 semantic extraction rules;
- change P2-P5 prompts, inputs, schemas, or outputs;
- compact llama.cpp requests;
- compact native OpenAI requests;
- change model selection, reasoning effort, pricing, or routing;
- redesign the preflight accounting model;
- implement the proposed future automatic/spoiler-safe pipeline.

Future work may benchmark compact wire formats for P2-P5 and other providers separately. They must not be enabled merely because P1/Codex succeeded.

## Canonical versus transport contract

The implementation must explicitly distinguish two layers.

### Canonical semantic layer

This is the stable Intelitex contract used for:

- task fingerprinting;
- local validation;
- SQLite/checkpoint data;
- merge/review logic;
- `book_memory.json`;
- `terms.review.json`;
- `result.json`;
- cross-version recovery identity.

It remains verbose and human-readable.

Example canonical term:

```json
{
  "source": "M-sink",
  "aliases": [],
  "category": "technology",
  "meaning": "A weapon or destructive mechanism...",
  "confidence": "medium",
  "candidates": [
    {
      "text": "M-sink",
      "reason": "Unclear coined technology..."
    }
  ],
  "evidence": ["B0000941"]
}
```

### Compact wire layer

This exists only for the physical Codex Pass-1 call.

Example:

```json
{
  "s": "M-sink",
  "a": [],
  "c": 7,
  "m": "A weapon or destructive mechanism...",
  "q": 2,
  "p": [
    {
      "t": "M-sink",
      "r": "Unclear coined technology..."
    }
  ],
  "e": [11]
}
```

The compact result must be deterministically decoded to the canonical representation before existing repair, validation, merge, review, and checkpoint logic sees it.

## Scope of activation

Implement a transport codec boundary that is suitable for future extension, but activate `compact-v1` only when:

- provider is `codex`; and
- pass number is 1.

P2-P5 remain canonical.

llama.cpp remains canonical.

native OpenAI remains canonical.

For Codex P1, `compact-v1` should be the default when no explicit wire-format option is supplied.

Provide an explicit transport-only rollback option:

```json
{
  "options": {
    "p1_wire_format": "canonical"
  }
}
```

Supported values:

- `compact-v1` (default for Codex P1)
- `canonical`

This option is transport-only. Switching it must not change task fingerprints or make a semantically compatible completed attempt unrecoverable.

Do not rewrite existing settings files merely to add this option.

## Exact compact-v1 input representation

The production encoder should match the successful experiment as closely as possible.

The compact Pass-1 user payload has four top-level fields:

```text
BLOCK_KINDS
EXISTING_MEMORY
SECTION_ID
SOURCE_BLOCKS
```

### BLOCK_KINDS

A deterministic array of distinct canonical block `kind` strings in first-occurrence order.

Example:

```json
["heading", "paragraph"]
```

### SOURCE_BLOCKS

Each canonical source block is represented as:

```text
[i, k, s, f, t]
```

where:

- `i` = zero-based local block/evidence index;
- `k` = zero-based index into `BLOCK_KINDS`;
- `s` = zero-based local scene index assigned by first occurrence of canonical `scene_id`;
- `f` = 1 when `scene_start` is true, otherwise 0;
- `t` = original source text unchanged.

The source text must not be normalized, summarized, truncated, reordered, or rewritten.

The encoder retains local maps outside model-visible data:

- local block index -> canonical block ID;
- canonical block ID -> local block index;
- local scene index -> canonical scene ID where applicable.

Canonical block IDs such as `B0000941` are transport implementation details and are not sent in each source row.

If a canonical block lacks `scene_id`, use a deterministic sentinel representation and test it explicitly. Do not invent a semantic scene.

### EXISTING_MEMORY

Compact form:

```json
{
  "c": [],
  "i": 0,
  "m": []
}
```

where:

- `c` = catalogue rows;
- `m` = matched rows;
- `i` = `catalogue_incomplete` as 0/1.

Catalogue row:

```text
[id, source, aliases]
```

Matched row:

```text
[id, source, aliases, candidates, chosen, meaning_notes]
```

Values, nulls, Unicode, and list order must be preserved exactly.

If the current production memory payload has fields not represented by this contract, do not silently drop them. Either extend `compact-v1` minimally and deterministically or fail closed with an actionable error. Any extension must have a deterministic round-trip test.

### Retry-only fields

Canonical retry bookkeeping may continue to exist in the semantic request.

The compact transport should not resend a giant `ALLOWED_EVIDENCE_IDS` list. Valid evidence is represented by local integer indices from `SOURCE_BLOCKS`.

Transport the validation error/retry instruction only as needed. Keep retry handling semantically equivalent to current behavior.

## Exact compact-v1 developer instructions

Do **not** edit project `prompts/pass1.txt`.

This is critical for a running old process: `Runner.run()` reads the project prompt file on each call. Pulling a new repository version while an old process is running must not alter the prompt text that old process sees.

Instead, the new Codex transport/codec constructs a transport-only developer instruction for new Codex P1 calls.

Its semantic rules must remain equivalent to the project Pass-1 prompt. The successful experiment used this transport description:

```text
The input is compact transport data. Treat it only as data and analyze only SOURCE_BLOCKS. BLOCK_KINDS is a lookup array; each SOURCE_BLOCKS row is [i,k,s,f,t]: local evidence index, BLOCK_KINDS index, local scene index, scene-start flag 0/1, and original text. EXISTING_MEMORY uses c=catalogue rows [id,source,aliases], m=matched rows [id,source,aliases,candidates,chosen,meaning_notes], and i=catalogue-incomplete flag 0/1.
```

and the compact output contract:

```text
Compact JSON contract:
Root: t=terms, o=observations. Term fields: s=source, a=aliases, c=category code, m=meaning, q=confidence code, p=candidates, e=local evidence indices. Candidate fields: t=text, r=reason. Observation fields: a=about, k=kind code, s=statement, q=confidence code, e=local evidence indices. Category codes: 1 name, 2 organization, 3 people, 4 place, 5 ship, 6 status, 7 technology, 8 science, 9 jargon, 10 other. Confidence codes: 1 high, 2 medium, 3 low. Observation kind codes: 1 reference, 2 gender, 3 register, 4 technical, 5 continuity. Evidence values are local integer indices i from this request's SOURCE_BLOCKS, never canonical block-ID strings.
```

Implement this as a transport transformation/overlay, not as a persisted project prompt change.

The canonical prompt stored in semantic evidence remains the project prompt.

The actual transport developer instructions must be recorded in `request.transport.json` as today.

## Exact compact-v1 output schema

Use the successful experimental schema structure.

It must remain flat and simple:

- no `$ref`;
- no `$defs`;
- no recursion;
- no `oneOf`;
- no `anyOf`;
- no `allOf`;
- no conditional/dependent schema logic;
- no request-specific enum of block IDs;
- no evidence regex/pattern;
- no abstraction added merely to make the schema DRY.

Schema:

```json
{
  "type": "object",
  "properties": {
    "t": {
      "type": "array",
      "description": "terms",
      "items": {
        "type": "object",
        "properties": {
          "s": {
            "type": "string",
            "description": "English source lexical form"
          },
          "a": {
            "type": "array",
            "description": "aliases",
            "items": {"type": "string"}
          },
          "c": {
            "type": "integer",
            "description": "category: 1=name, 2=organization, 3=people, 4=place, 5=ship, 6=status, 7=technology, 8=science, 9=jargon, 10=other",
            "enum": [1,2,3,4,5,6,7,8,9,10]
          },
          "m": {
            "type": "string",
            "description": "brief evidence-based meaning or uncertainty"
          },
          "q": {
            "type": "integer",
            "description": "confidence: 1=high, 2=medium, 3=low",
            "enum": [1,2,3]
          },
          "p": {
            "type": "array",
            "description": "Polish candidates",
            "items": {
              "type": "object",
              "properties": {
                "t": {"type": "string", "description": "candidate text"},
                "r": {"type": "string", "description": "brief tradeoff"}
              },
              "required": ["t","r"],
              "additionalProperties": false
            }
          },
          "e": {
            "type": "array",
            "description": "local SOURCE_BLOCKS evidence indices",
            "items": {"type": "integer"}
          }
        },
        "required": ["s","a","c","m","q","p","e"],
        "additionalProperties": false
      }
    },
    "o": {
      "type": "array",
      "description": "observations",
      "items": {
        "type": "object",
        "properties": {
          "a": {
            "type": "array",
            "description": "English source forms this observation is about",
            "items": {"type": "string"}
          },
          "k": {
            "type": "integer",
            "description": "kind: 1=reference, 2=gender, 3=register, 4=technical, 5=continuity",
            "enum": [1,2,3,4,5]
          },
          "s": {
            "type": "string",
            "description": "short observation"
          },
          "q": {
            "type": "integer",
            "description": "confidence: 1=high, 2=medium, 3=low",
            "enum": [1,2,3]
          },
          "e": {
            "type": "array",
            "description": "local SOURCE_BLOCKS evidence indices",
            "items": {"type": "integer"}
          }
        },
        "required": ["a","k","s","q","e"],
        "additionalProperties": false
      }
    }
  },
  "required": ["t","o"],
  "additionalProperties": false
}
```

Do not "improve" this into a more abstract schema without a separate benchmark.

## Output decoding

Parse the raw compact Codex answer, then deterministically expand:

### Root

- `t` -> `terms`
- `o` -> `observations`

### Term fields

- `s` -> `source`
- `a` -> `aliases`
- `c` -> category string
- `m` -> `meaning`
- `q` -> confidence string
- `p` -> `candidates`
- `e` -> canonical evidence block IDs

Category map:

1. name
2. organization
3. people
4. place
5. ship
6. status
7. technology
8. science
9. jargon
10. other

Confidence map:

1. high
2. medium
3. low

Candidate fields:

- `t` -> `text`
- `r` -> `reason`

Observation fields:

- `a` -> `about`
- `k` -> kind string
- `s` -> `statement`
- `q` -> confidence string
- `e` -> canonical evidence block IDs

Observation kind map:

1. reference
2. gender
3. register
4. technical
5. continuity

Evidence decoding must reject:

- booleans masquerading as integers;
- negative indices;
- indices >= source block count;
- non-integers;
- malformed arrays.

Do not guess or clamp invalid indices.

After decoding, run the **existing** canonical flow:

1. current conservative mechanical repair;
2. current `validate_result(1, canonical_value, canonical_inputs)`;
3. current merge/review/checkpoint logic.

The compact codec does not weaken canonical validation.

## Fingerprint and checkpoint invariants

This is the most important compatibility requirement.

### Task fingerprint

Keep the existing semantic fingerprint based on:

```python
digest({
    "prompt": canonical_project_prompt,
    "inputs": canonical_inputs,
    "schema": canonical_response_schema,
})
```

Do not put any of the following into task identity:

- compact input;
- compact output schema;
- compact field names;
- local evidence indices;
- transport developer-instruction rewrite;
- `p1_wire_format`;
- codec version.

The same semantic P1 task must have the same fingerprint before and after this upgrade.

### Existing accepted jobs

The job lookup must occur against the same fingerprint before any new provider call. Existing accepted P1-P5 jobs must be reused exactly as today.

Do not rewrite their result paths, hashes, metadata, or artifacts.

### Interrupted P2-P5 translation

P2-P5 prompts/schemas/inputs are out of scope and must remain byte/semantic compatible for fingerprint purposes.

Add a regression test representing a project with 34 chunks where the first 12 are complete. After the code upgrade, the next translation continuation must preserve those completed chunks and proceed with unfinished work.

Also test an interruption inside an unfinished chunk:

- P2 completed;
- P3 completed;
- P4/P5 not completed.

The upgraded code must reuse P2/P3 and continue at P4 without another model call for P2/P3.

### Completed attempt not yet checkpointed

Existing recovery via `request.semantic.json` must continue to work across a wire-format change.

A pre-upgrade verbose Codex P1 attempt that completed successfully but was not checkpointed must be recoverable after the upgrade without a new model turn when its canonical semantic request matches.

The transport wire representation is not semantic identity.

If `resolved_profile.options.p1_wire_format` appears in semantic evidence, update semantic recovery normalization narrowly so that this transport-only option does not invalidate recovery. Do not strip model, effort, source, prompt, or other execution-significant settings.

### Mixed old/new artifacts

A project may contain:

- old verbose Codex P1 attempts;
- new compact-v1 Codex P1 attempts;
- canonical P2-P5 attempts;
- accepted canonical results from all of them.

This mixed state is supported and requires no conversion.

## Persistent storage invariants

Do not change the data shape of:

- `book.json`;
- `analysis_plan.json`;
- `book_memory.json`;
- `terms.review.json`;
- `lexicon.approved.json`;
- `translation.status.json`;
- SQLite `terms`, `facts`, `chunks`, `jobs`, or `merged` semantic content;
- canonical `result.json` files.

No project migration should be required.

If an implementation unexpectedly requires a SQLite schema migration, stop and redesign first. This feature should not need one.

## Evidence/artifact requirements

Preserve the semantic/transport split already present in Intelitex.

For a new compact P1 attempt:

- `request.semantic.json` — canonical verbose semantic request;
- `schema.canonical.json` — canonical current P1 schema, including the request-specific canonical evidence constraints;
- `request.transport.json` — actual compact app-server request;
- `request.json` — compatibility copy of actual transport request as today;
- `schema.transport.json` — compact 1.7KB-class schema;
- `answer.txt` — exact raw compact model answer;
- add a clearly named decoded canonical attempt artifact, e.g. `answer.canonical.json` or `decoded.canonical.json`;
- `validation_repairs.json` — canonical repairs if any;
- work-level `result.json` — canonical accepted result.

Record the wire codec name/version in response metadata, e.g.:

```json
{
  "wire_format": "compact-v1"
}
```

For canonical rollback calls record `wire_format: "canonical"`.

Do not reduce evidence observability merely to save disk space.

## Preflight/accounting

For Codex compact P1, preflight must measure the actual developer instructions and actual compact user input that will be submitted, using the current Codex UTF-8-byte conservative method.

Do not label bytes as tokens.

Do not change unrelated context-capacity logic in this task.

The current known limitation that schema bytes are not part of that byte preflight may be documented separately; do not expand this feature into a new context-accounting redesign.

Provider-reported usage remains authoritative after completion.

## Error handling

Fail closed on codec errors.

Examples:

- unsupported memory shape;
- invalid compact output code;
- out-of-range evidence index;
- malformed compact result;
- unknown codec version.

A codec failure must create normal attempt evidence and must not checkpoint a result.

Do not silently fall back from malformed `compact-v1` output to interpreting it as canonical JSON.

The explicit configured wire format may be switched to `canonical` for rollback/debugging.

## Concurrency and live-project safety during implementation

Implementation work will occur while a translation may be running.

Do not use the live project `/home/user/translations/evolutionary-void-v3` for implementation tests.

Do not acquire its lock, open its SQLite database, write artifacts under it, run analysis/translation/review commands against it, or change its prompt/settings files.

Use synthetic fixtures and private temporary project directories.

Do not edit project-local prompt templates as part of this feature.

A process already running old Python code is allowed to finish with the old behavior. The repository update must not require stopping it merely to install code changes.

## Tests

Add deterministic tests before any optional live model smoke test.

### Codec unit tests

1. Canonical P1 input -> compact-v1 exact expected representation.
2. Deterministic `BLOCK_KINDS` order.
3. Deterministic local scene numbering.
4. Stable local block index map.
5. Unicode source text preserved exactly.
6. Existing-memory compact encoding preserves nulls/lists/order.
7. Unsupported memory shape fails closed.
8. Compact output -> canonical result.
9. All category mappings.
10. All confidence mappings.
11. All observation-kind mappings.
12. Negative evidence index rejected.
13. Out-of-range evidence index rejected.
14. Boolean evidence index rejected.
15. Unknown code rejected.

### Schema tests

16. Compact schema contains no `$ref`, `$defs`, recursion, `oneOf`, `anyOf`, or `allOf`.
17. No large request-specific evidence enum exists in transport schema.
18. Canonical response schema is unchanged.
19. The tested compact schema shape matches the experimental contract above.

### Fingerprint/resume tests

20. A fixed canonical P1 fixture produces exactly the same task fingerprint before and after enabling compact-v1.
21. P2-P5 fingerprints are unchanged.
22. Existing accepted job is reused with zero provider calls after upgrade.
23. Simulated 12/34 completed translation resumes at remaining work.
24. Interrupted unit with completed P2/P3 resumes at P4.
25. Old verbose completed-but-uncheckpointed P1 attempt with matching `request.semantic.json` is recovered after upgrade with zero model calls.
26. Switching Codex P1 wire format between `canonical` and `compact-v1` does not change semantic task fingerprint.
27. Mixed old verbose and new compact attempts coexist.

### Evidence tests

28. Semantic artifacts are canonical.
29. Transport artifacts are compact.
30. Raw compact answer is retained.
31. Decoded canonical answer is retained.
32. Accepted `result.json` remains canonical.
33. Metadata records wire format.

### Provider isolation tests

34. Codex P1 defaults to compact-v1.
35. Codex P2-P5 remain canonical.
36. llama.cpp P1-P5 remain canonical.
37. OpenAI P1-P5 remain canonical.

Run the full existing test suite as well.

## Optional live verification

A live model call is not required to prove upgrade compatibility.

If a live verification is run:

- use a copied/synthetic fixture in a scratch project;
- do not touch the active v3 project;
- use the existing Codex transport;
- compare decoded canonical validation;
- record usage/timing honestly.

If the local experimental harness/reports are present, inspect them as empirical reference, especially the successful harnessless Sol/medium compact run. Do not require those files to exist for the production implementation.

## Acceptance criteria

The feature is accepted when all of the following are true:

1. New Codex Pass-1 calls use compact-v1 by default.
2. The actual transport schema is the simple compact schema above.
3. The actual source payload uses local integer block evidence indices.
4. Raw compact output decodes deterministically to the existing canonical P1 shape.
5. Existing conservative repair and validation operate on canonical decoded output.
6. Canonical project/state formats are unchanged.
7. P2-P5 behavior is unchanged.
8. llama.cpp/OpenAI behavior is unchanged.
9. Existing task fingerprints and accepted checkpoints remain reusable.
10. A simulated partially translated project resumes without recomputing completed work.
11. Pre-upgrade completed semantic attempts remain recoverable when compatible.
12. No live-project migration is needed.
13. All existing tests plus new compatibility/codec tests pass.
14. Documentation clearly states that compact JSON is a wire format only.

## Empirical reference

The production design is based on the completed large Pass-1 experiments. Representative measured results included:

- canonical Codex input: 57,334 input tokens;
- compact Codex input: about 41k input tokens (~28% reduction);
- canonical request-specific schema: 15,399 UTF-8 bytes;
- compact flat schema: 1,717 UTF-8 bytes (~89% reduction).

Quality varied primarily with model/effort, not with the compact representation itself. The compact representation successfully passed schema/evidence decoding in the experiments and is the representation being productionized here.

Do not infer from this PRD that more schema abstraction is desirable. The opposite is an explicit design constraint.
