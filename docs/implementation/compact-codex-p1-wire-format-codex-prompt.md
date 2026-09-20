# Codex implementation prompt — productionize compact Codex P1 wire format safely

Work in the current Intelitex repository.

Implement the PRD at:

`docs/prd/compact-codex-p1-wire-format.md`

Read the entire PRD before modifying code. Inspect the actual checkout and repository instructions. Do not assume the checkout matches a previous conversation or a remote branch.

This task is a production implementation, not another benchmark.

## Critical context

A real Intelitex translation may be running concurrently and may currently have only part of its translation units completed. The owner must be able to interrupt that old process, update to your implementation, and continue from the existing project/checkpoints without recomputing accepted work.

Therefore:

- preserve canonical task fingerprints;
- preserve old accepted checkpoints;
- preserve recovery from old completed semantic attempts;
- do not migrate canonical project data;
- do not change project prompt files;
- do not touch the live translation project during implementation or tests.

The live project path is:

`/home/user/translations/evolutionary-void-v3`

Treat it as completely off-limits for this implementation: no reads are required, and no writes, locks, migrations, CLI commands, or SQLite opens are permitted.

Use synthetic fixtures and temporary projects only.

## Implementation intent

Productionize the already-tested compact transport only for:

`provider=codex, pass=1`

The compact representation is **wire format only**.

Canonical Intelitex representations remain unchanged.

The desired boundary is:

```text
canonical prompt + canonical inputs + canonical schema
                    |
                    | semantic fingerprint / evidence
                    v
             compact-v1 encoder
                    |
                    v
       CodexAppServerClient / app-server
                    |
                    v
          raw compact JSON answer
                    |
                    v
             compact-v1 decoder
                    |
                    v
 canonical conservative_repair + validate_result
                    |
                    v
 canonical result / SQLite / book_memory / review
```

Do not compact P2-P5 in this task.

Do not compact llama.cpp or native OpenAI in this task.

## Existing code points to inspect first

Read at least:

- `bookpipe/engine.py`
- `bookpipe/codex_transport.py`
- `bookpipe/contracts.py`
- `bookpipe/schemas.py`
- `bookpipe/evidence.py`
- `bookpipe/store.py`
- profile/config normalization code
- relevant tests

Pay particular attention to:

- `Runner.run()`;
- `response_schema()`;
- `conservative_repair()`;
- `validate_result()`;
- `_semantic_execution_signature()`;
- `_compatible_completed_attempts()`;
- `CodexAppServerClient.body()`;
- `CodexAppServerClient.preflight()`;
- `CodexAppServerClient.generate()`;
- `AttemptRecorder.semantic()`;
- `AttemptRecorder.transport_request()`;
- `Store.job()` and `Store.save_job()`.

The current task fingerprint is based on canonical prompt+inputs+schema. Preserve that invariant.

## Experimental reference

If these files exist in the local checkout, inspect them:

- `experiments/pass1_compact_transport.py`
- `docs/implementation/pass1-compact-transport-experiment-results.md`
- the Sol/medium harnessless experiment report

They are empirical references only. The production contract in the PRD is authoritative.

Do not depend on the experiment harness at runtime.

## Required design constraints

### 1. No project prompt edits

Do not edit `prompts/pass1.txt` or any other project prompt as part of this feature.

The running old process reads prompt files from disk for each call. A repository pull during that process must not change its semantic prompt.

Create compact transport instructions in code at the wire boundary.

### 2. Canonical fingerprint must not change

Do not compute the fingerprint from compact payload/schema/instructions.

Keep fingerprinting canonical.

Add a regression fixture that fails if this changes.

### 3. Canonical response schema remains canonical

Do not replace `response_schema(1, inputs)` with the compact schema.

The canonical request-specific schema remains the semantic/local-validation contract and remains part of the existing fingerprint.

Generate a separate compact transport schema downstream.

### 4. Decode before existing repair/validation

The existing production sequence after model generation should remain semantically:

```text
decode transport -> canonical
conservative_repair(canonical)
validate_result(canonical)
checkpoint canonical
```

Do not weaken validation to make compact output pass.

### 5. Keep schema simple

Do not introduce:

- `$ref`
- `$defs`
- recursion
- `oneOf`
- `anyOf`
- `allOf`
- conditional/dependent schema machinery
- request-specific block-ID enums in the transport schema

The owner has directly observed severe Codex latency regressions with complicated structured-output schemas.

### 6. Transport-only rollback

Support `options.p1_wire_format` values:

- `compact-v1`
- `canonical`

Default Codex P1 to `compact-v1` if absent.

Do not rewrite existing project settings.

This option must not affect canonical fingerprint/recovery identity. Strip only this explicitly transport-only setting from semantic recovery comparison if necessary; do not broadly weaken recovery identity.

## Upgrade/resume behavior to prove

You must add deterministic tests proving the following.

### Existing completed chunks

Create a synthetic project state equivalent to:

`12 completed translation units / 34 total`.

After enabling the new code, continuation must retain those completed chunks and proceed only with unfinished work.

### Interrupted current chunk

Create a fixture where:

- P2 is accepted;
- P3 is accepted;
- P4 and P5 are unfinished.

Restart through the new version.

Assert P2/P3 are reused with zero new calls and execution resumes at P4.

### Existing P1 checkpoint

An accepted pre-change P1 checkpoint must be returned immediately using the unchanged canonical fingerprint, even though new physical P1 calls would use compact-v1.

### Completed but not checkpointed old Codex P1 attempt

Create an old-style verbose Codex P1 attempt with:

- completed answer;
- matching canonical `request.semantic.json`;
- no accepted job checkpoint.

After the upgrade, recovery must accept and checkpoint that old canonical answer without a new model call.

### Mixed project

A project containing both old verbose and new compact P1 attempts must work normally.

## Evidence requirements

Do not hide compact encoding.

A new compact attempt must make it obvious what happened:

- canonical `request.semantic.json`;
- canonical `schema.canonical.json`;
- actual compact `request.transport.json`;
- actual compact `schema.transport.json`;
- exact raw compact `answer.txt`;
- decoded canonical answer artifact;
- canonical validation repairs if any;
- canonical accepted `result.json`;
- response metadata with `wire_format: compact-v1`.

The semantic artifact must not lie by storing the compact schema as though it were the canonical contract.

## Implementation quality

Prefer a small pure codec module with deterministic encode/decode behavior over scattering mapping logic across `engine.py` and `codex_transport.py`.

A reasonable shape is a small transport-codec abstraction/value object that contains:

- wire developer instructions;
- wire input payload/text;
- wire output schema;
- codec/version identity;
- local block-ID mapping required for decode.

Do not over-engineer the abstraction. There is currently one production compact codec.

The encoder/decoder must have no network or project-state side effects.

## Exact compact-v1 contract

Implement the input, output field mappings, integer enum mappings, evidence-index behavior, and flat schema exactly as specified in the PRD.

In particular:

`SOURCE_BLOCKS` rows are:

`[i,k,s,f,t]`

and output evidence is a list of local integer `i` values, later restored to canonical block IDs.

Do not send the hundreds-entry canonical evidence enum in the Codex transport schema.

## Testing discipline

Run deterministic tests before any live model call.

Run the complete repository test suite.

A live Codex call is optional and must use scratch/synthetic data only.

Do not touch `/home/user/translations/evolutionary-void-v3`.

Do not modify v2/v3 project files to demonstrate compatibility; simulate compatibility in tests.

## Documentation

Update repository documentation narrowly to explain:

- compact-v1 is a Codex Pass-1 wire format;
- canonical project/storage formats are unchanged;
- old checkpoints are reusable;
- `p1_wire_format: canonical` is the rollback/debug option;
- P2-P5 and other providers are intentionally unchanged.

Do not replace the existing LLM transport architecture documentation.

## Completion criteria

Do not stop at a plan.

Implement the feature and all required tests.

Before finishing, verify:

1. full tests pass;
2. canonical P1 fingerprint fixture is unchanged;
3. P2-P5 fingerprints are unchanged;
4. 12/34-style resume test passes;
5. mid-unit P2/P3 resume test passes;
6. old verbose P1 recovery test passes;
7. compact-v1 encode/decode tests pass;
8. compact transport schema has no forbidden complexity;
9. no canonical storage format changed;
10. no project migration was added;
11. no project prompt file changed;
12. no files under the live v3 project were touched.

Finish with coherent local commits containing only intended repository changes, unless repository instructions say otherwise.

Do not push or open a pull request unless explicitly requested.

In your final report to the owner, write in Polish and include:

- files changed;
- codec boundary chosen;
- exact backward-compatibility strategy;
- proof that fingerprints/checkpoints remain reusable;
- tests and counts;
- whether any live model call was run;
- any limitations or follow-up work;
- final commit SHA(s).
