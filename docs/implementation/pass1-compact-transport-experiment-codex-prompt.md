# Codex test prompt — compact P1 transport experiment

Work in the current Intelitex repository and execute this experiment. This is an **experimental transport-encoding benchmark**, not a production implementation.

The production Intelitex code must remain unchanged. You may add self-contained experimental code under `experiments/` and you must write the final benchmark report under `docs/implementation/`. Experimental code does not need to be removed after the test.

## Goal

Measure whether Pass 1 can use a much more compact JSON representation without losing correctness, while continuing to use Intelitex's **existing Codex transport implementation**.

Compare:

1. the existing completed Pass-1 result produced with the current canonical/verbose input representation and request-specific schema; and
2. one new Pass-1 request containing the same semantic information, sent through the same Intelitex `CodexAppServerClient`, but using the compact transport representation and compact output schema defined below.

Compare actual provider-reported token usage, request sizes, elapsed time, structured-output validity, Intelitex canonical validation, and semantic/result quality.

This test exists because large P1 requests currently spend a substantial amount of input on repeated JSON field names, technical IDs and a request-specific output schema. The owner has also observed empirically that recursive or structurally complicated Codex schemas can make server responses take tens of minutes, while simpler schemas can be several times faster. Therefore **schema simplicity is a first-class requirement**. Do not optimize schema byte size by introducing recursion or abstraction.

## Non-negotiable safety constraints

The active translation project is:

`/home/user/translations/evolutionary-void-v3`

It is currently in use by another Intelitex process.

Treat that entire project tree as **strictly read-only**:

- do not run `analyze`, `translate`, `approve`, `review`, import, migration, recovery, checkpoint, or any command that may write there;
- do not open its SQLite database read-write;
- do not acquire or manipulate its project lock;
- do not create test artifacts, provider-discovery files, temporary files, logs, backups, or Codex runtime directories under it;
- do not rename, touch, truncate, chmod, or delete anything in it;
- read only completed artifacts and configuration needed to reproduce the selected request;
- if a candidate artifact is still being written, skip it and use a completed stable artifact.

Before the model call, copy the exact selected baseline inputs/metadata needed for the test into a private scratch directory outside the project tree. Perform all subsequent test work from the scratch copy.

Do not place the book chapter, full model request, full model answer, or other copyrighted source text in this public repository. Raw benchmark artifacts must remain local outside the repository. The report in `docs/` must contain metrics, identifiers, aggregate comparisons, and short diagnostic examples only, never the full source or model output.

## Concurrency model

Use the existing Intelitex Codex transport. Do **not** write a new app-server client.

At the current repository version, `bookpipe.codex_transport.CodexAppServerClient` creates a new `codex app-server` process per generation and gives each runtime a unique private `CODEX_HOME`, `CODEX_SQLITE_HOME`, and empty work directory. This is the intended isolation for the test.

Parallel execution with the currently running analysis should therefore not share Codex runtime state. Still report any rate-limit, account-level concurrency, provider-side queueing, or obvious resource-contention symptoms if observed.

Do not call `discover()` with a `project_root` pointing at the live v3 project, because discovery itself creates evidence under the configured project root. Either avoid discovery when the exact existing profile is already verified by the selected baseline, or override all writable roots to scratch.

The configured authentication source may be read/copied through the normal Intelitex transport behavior. Do not modify it.

## Required preparation

Inspect the current checkout before writing test code. In particular read:

- `bookpipe/codex_transport.py`
- `bookpipe/contracts.py`
- `bookpipe/schemas.py`
- `bookpipe/engine.py`
- `bookpipe/evidence.py`
- the active Pass-1 prompt
- the selected baseline attempt's `request.semantic.json`, `request.transport.json`, `schema.canonical.json`, `schema.transport.json`, `usage.json`, `response_meta.json`, `attempt.json`, and accepted canonical result.

Reuse production helpers where they are read-only/pure and useful. Do not edit those modules for the experiment.

The current production behavior is important:

- `CodexAppServerClient.body(prompt, inputs, schema, pass_no)` accepts a caller-provided schema.
- `CodexAppServerClient.generate(...)` passes that schema to app-server as `turn.outputSchema`.
- Pass 1 currently expands each `evidence` item into a request-specific enum containing every canonical block ID in the current source unit.
- Production `validate_result(1, value, inputs)` performs the canonical local validation after generation.

The experiment should exploit those existing boundaries rather than bypass them.

## Select the baseline

Find the **largest completed, full Pass-1 analysis input** available in the v3 project at the time the benchmark begins.

Prefer the largest complete P1 unit by actual serialized semantic input size or source-text size, not an arbitrary filename. A candidate must have a completed stable attempt and an accepted canonical result. Do not select a partial, interrupted, failed, still-running, truncated, or silently altered request.

Record in the report:

- task key, e.g. `pass1/ch....`;
- attempt number;
- source block count;
- source text UTF-8 bytes;
- canonical compacted-input JSON UTF-8 bytes;
- baseline schema UTF-8 bytes;
- requested/reported model;
- requested/reported reasoning effort;
- baseline provider-reported usage;
- baseline `elapsed_seconds`.

If the currently largest unit is still running, select the largest already completed comparable unit instead. Do not wait for or interfere with the active process.

The compact test must use the exact same source blocks, existing-memory snapshot, semantic Pass-1 task, model, and reasoning effort as the chosen baseline.

Prefer the already recorded baseline for the classic side of the comparison. **Do not spend a second classic-schema model call merely for symmetry.** Only run a fresh classic control in scratch if no completed baseline has sufficiently complete timing/usage evidence for a meaningful comparison; if you do so, explain why.

## Experimental code location

Add a self-contained harness, preferably:

`experiments/pass1_compact_transport.py`

It may remain in the repository after the experiment.

Do not modify production code, production prompts, settings defaults, schemas, CLI behavior, tests, or the live project.

The harness should import and reuse `CodexAppServerClient`, `AttemptRecorder`, and existing pure validation/helpers where appropriate. All writable provider roots and attempt directories must point to a unique scratch tree such as:

`/tmp/intelitex-pass1-compact-transport-<pid-or-unique-id>/`

Keep raw source/request/answer evidence there, not under `docs/`.

## Compact input representation

Preserve all information required by the baseline Pass-1 call, but remove repeated transport verbosity.

Keep these top-level names for clarity because they occur only once:

- `SECTION_ID`
- `SOURCE_BLOCKS`
- `EXISTING_MEMORY`

### SOURCE_BLOCKS

Replace each verbose block object with a compact row:

`[i, k, s, f, t]`

where:

- `i` = zero-based local block/evidence index for this request;
- `k` = index into a one-time top-level `BLOCK_KINDS` array;
- `s` = zero-based local scene index assigned by first occurrence of canonical `scene_id`;
- `f` = `1` when `scene_start` is true, otherwise `0`;
- `t` = original block text, byte-for-byte/string-for-string unchanged.

Add one top-level array:

`"BLOCK_KINDS": ["heading", "paragraph", ...]`

containing the distinct canonical `kind` values used in this request in deterministic first-occurrence order.

The harness must retain a local map:

`local block index -> canonical B... block ID`

for decoding output evidence back to canonical IDs. This map is a local transport implementation detail and must not be written into the model output.

Do not alter, normalize, summarize, truncate or reorder source text.

### EXISTING_MEMORY

Use a compact but lossless transport form:

- `c` = catalogue rows
- `m` = matched rows
- `i` = `catalogue_incomplete` as `0` or `1`

Catalogue row:

`[id, source, aliases]`

Matched row:

`[id, source, aliases, candidates, chosen, meaning_notes]`

Preserve the values exactly, including nulls and list order. This is transport encoding only.

If the selected baseline contains additional memory fields not represented above, extend the compact representation minimally and document the extension. Do not silently drop semantic information.

## Compact developer instructions

Keep the semantic Pass-1 instructions equivalent to the baseline.

Do not redesign the task, loosen entity-resolution rules, change terminology policy, or change what constitutes a valid observation.

Make only the transport changes necessary for the compact representation:

1. explain the compact `SOURCE_BLOCKS` row layout and `BLOCK_KINDS`;
2. explain the compact `EXISTING_MEMORY` layout;
3. replace the verbose JSON output contract with the compact output contract below;
4. state that output evidence values are local integer block indices from the current `SOURCE_BLOCKS`, not canonical `B...` strings.

Keep this transport explanation concise. Measure its UTF-8 size separately so the report shows whether input savings are merely being moved into instructions.

## Compact output representation

Return:

```json
{
  "t": [
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
  ],
  "o": []
}
```

Field meanings:

### Root

- `t` = terms
- `o` = observations

### Term

- `s` = English source lexical form
- `a` = aliases
- `c` = category code
- `m` = meaning
- `q` = confidence code
- `p` = candidates
- `e` = evidence as local block indices

Category codes:

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

Confidence codes:

1. high
2. medium
3. low

### Candidate

- `t` = Polish candidate text
- `r` = reason/tradeoff

### Observation

- `a` = about
- `k` = observation kind code
- `s` = statement
- `q` = confidence code
- `e` = evidence as local block indices

Observation kind codes:

1. reference
2. gender
3. register
4. technical
5. continuity

## Compact output schema

Build the experimental schema directly in the harness. Do not modify `bookpipe/schemas.py`.

The schema must intentionally be **flat and structurally simple**:

- no `$ref`;
- no `$defs`;
- no recursion;
- no `oneOf`, `anyOf`, `allOf`, conditional schemas, dependent schemas, or schema indirection;
- no request-specific enum containing hundreds of evidence IDs;
- no regex/pattern for evidence IDs;
- only ordinary nested objects/arrays needed by the actual result shape;
- small integer enums are allowed;
- use short field names;
- use concise `description` strings on short fields so the schema is self-describing and no external output-field dictionary is needed;
- keep `additionalProperties: false` and required fields as required by the structured-output contract.

Evidence should simply be an array of integers. Do not put all valid local indices into an enum. Referential integrity is checked locally after generation.

A target shape is:

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
          "s": {"type": "string", "description": "English source lexical form"},
          "a": {"type": "array", "description": "aliases", "items": {"type": "string"}},
          "c": {"type": "integer", "description": "category: 1=name, 2=organization, 3=people, 4=place, 5=ship, 6=status, 7=technology, 8=science, 9=jargon, 10=other", "enum": [1,2,3,4,5,6,7,8,9,10]},
          "m": {"type": "string", "description": "brief evidence-based meaning or uncertainty"},
          "q": {"type": "integer", "description": "confidence: 1=high, 2=medium, 3=low", "enum": [1,2,3]},
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
          "e": {"type": "array", "description": "local SOURCE_BLOCKS evidence indices", "items": {"type": "integer"}}
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
          "a": {"type": "array", "description": "English source forms this observation is about", "items": {"type": "string"}},
          "k": {"type": "integer", "description": "kind: 1=reference, 2=gender, 3=register, 4=technical, 5=continuity", "enum": [1,2,3,4,5]},
          "s": {"type": "string", "description": "short observation"},
          "q": {"type": "integer", "description": "confidence: 1=high, 2=medium, 3=low", "enum": [1,2,3]},
          "e": {"type": "array", "description": "local SOURCE_BLOCKS evidence indices", "items": {"type": "integer"}}
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

You may make a smaller equivalent schema if the installed Codex protocol rejects a nonessential keyword, but do not make it more structurally complicated. Record any deviation in the report.

## Decode and canonical validation

After the compact response is returned:

1. parse JSON;
2. decode category/confidence/kind integer codes to the canonical strings;
3. convert every local integer evidence index back to its exact canonical `B...` block ID;
4. reject negative, out-of-range, non-integer, or otherwise invalid evidence indices;
5. expand short keys to the current canonical Pass-1 result shape;
6. save the decoded canonical result only in scratch;
7. validate the decoded result with the current production canonical validator against the original canonical baseline inputs.

Use the existing `validate_result(1, decoded, canonical_inputs)` if practical without side effects.

Do **not** silently repair the compact response before the primary correctness verdict.

If strict validation fails, report the failure. You may additionally run the current conservative repair logic on a copy to report whether production recovery would have made it acceptable, but that must be a separate secondary result.

## Measurements

For both baseline and compact run, report all available provider-reported fields from `usage.json`:

- input tokens;
- cached input tokens;
- cache-write input tokens;
- output tokens;
- reasoning output tokens;
- total tokens;
- model context window;
- usage scope/status.

Do not add overlapping token categories together. Use provider total as reported.

Report timing:

- total `elapsed_seconds`;
- if derivable from `transport.jsonl`, time from `turn/start` submission to first agent output/delta;
- time to terminal completion if derivable;
- if first-output timing is unavailable, say so rather than inventing it.

Report byte sizes using minified UTF-8 serialization:

- unchanged source text only;
- baseline canonical input payload;
- compact input payload;
- baseline developer instructions;
- compact developer instructions;
- baseline output schema;
- compact output schema;
- baseline raw answer;
- compact raw answer;
- decoded compact canonical answer.

Also report schema complexity counts:

- total schema UTF-8 bytes;
- property count;
- enum count and total enum values;
- `$ref` count;
- maximum observed nesting depth.

## Correctness comparison

The compact result is not successful merely because it parses.

Compare the decoded compact result with the accepted baseline canonical result.

### Mechanical checks

Report at least:

- strict compact schema parse/validation;
- local evidence-index validity;
- successful expansion to canonical form;
- successful current Intelitex Pass-1 canonical validation;
- term count;
- observation count;
- normalized source-term intersection;
- baseline-only source terms;
- compact-only source terms;
- category agreement for shared terms;
- confidence agreement for shared terms;
- candidate-text overlap for shared terms;
- alias overlap;
- evidence overlap after restoring canonical block IDs;
- observation overlap using a reasonable deterministic key such as normalized `about + kind`.

### Semantic review of differences

As the Codex implementation agent, inspect the differences against the selected source/evidence and determine whether compact encoding introduced meaningful quality loss.

Focus review on disagreements rather than re-summarizing the chapter.

Classify notable differences as appropriate, for example:

- baseline clearly better;
- compact clearly better;
- equivalent wording;
- stochastic but both supported;
- unsupported/hallucinated;
- missed reusable term;
- evidence/reference error;
- category/confidence-only difference.

Do not assume the old result is ground truth merely because it is the baseline. Both outputs can be wrong.

Do not quote substantial source prose in the report.

## Fairness requirements

The benchmark is intended to test transport representation, not a different task.

Keep constant:

- source text;
- existing-memory semantic content;
- Pass-1 semantic rules;
- requested model;
- reasoning effort;
- Codex transport implementation;
- app-server isolation behavior;
- tool/skill disabling;
- sandbox/approval policy;
- output task.

The intended changed variables are only:

- compact versus verbose input transport representation;
- compact versus verbose output field representation;
- compact simple output schema versus current request-specific verbose schema;
- the minimal transport-contract wording required to describe the compact representation.

Record any unavoidable additional difference.

## Result report

Write the final report to:

`docs/implementation/pass1-compact-transport-experiment-results.md`

The report must be self-contained and include:

1. **Executive summary**
2. **Repository/test identity** — Intelitex commit, Codex CLI version, selected baseline task/attempt, model and effort
3. **Safety statement** — confirm the v3 project was read-only and list where scratch artifacts were written
4. **Representations tested**
5. **Request/schema size table**
6. **Provider token-usage table**
7. **Timing table**
8. **Validation/correctness table**
9. **Semantic differences**
10. **Observed schema/latency behavior**
11. **Confounders and limitations**
12. **Conclusion** — whether compact transport is promising enough for a production design
13. **Recommended next step** — recommendation only; do not implement production changes in this task

Include percentage deltas for compact versus baseline where meaningful.

If the compact run fails, still write the report with the failure point, provider evidence and any partial measurements.

Do not commit raw book content or full generated answers.

## Tests for the experimental harness

Before spending a live model turn, add/run deterministic local tests or self-checks for at least:

- canonical input -> compact input -> semantic equivalence for the selected request;
- stable local block index mapping;
- compact output decoder;
- all category/confidence/kind code mappings;
- rejection of out-of-range evidence indices;
- decoded canonical result shape.

These can be assertions inside the experimental harness or separate experimental tests. Do not modify production tests just to accommodate the experiment.

Then run the single compact live request using the existing Intelitex Codex transport.

## Deliverable discipline

This task is complete only after the benchmark has actually been run (unless live Codex access itself fails), the comparison has been performed, and the results document has been written.

Do not stop after creating a plan or harness.

Do not modify production Intelitex code.

Keep experimental code and the result document as coherent repository changes. Finish with a local commit containing only the intended experiment harness/report changes unless repository instructions require otherwise. Do not push or open a pull request unless explicitly asked.
