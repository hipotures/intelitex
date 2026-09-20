# Pass-1 compact transport experiment results — gpt-6-astra/low cross-model variant

## Executive summary

The `gpt-6-astra`/`low` compact call completed normally and passed the transport schema, evidence-index, canonical-expansion, and harness-suppression checks. It did not pass the unchanged production Pass-1 validator: the model normalized three attested plurals to unattested singular source forms, beginning with `solido`. A deterministic secondary assessment promoted the exact attested aliases `solidos`, `Chobamba francs`, and `scruitineers`; that repaired result passed full canonical validation.

The provider reported 41,333 input tokens, 6,388 output tokens, only 18 reasoning-output tokens, 47,721 total tokens, and 197.533 seconds elapsed. Requested and reported identity both matched `gpt-6-astra`/`low`. Relative to the harness-suppressed Sol/medium sample, Astra used 0.9% fewer total tokens but took 47.1% longer, returned 35.8% more terms and 108.3% more observations, and regressed from primary validation pass to failure. This was one experimental call, not a production change.

## Repository/test identity

- Intelitex commit: `da3444432ca4ee6211f50a0f66dc7fa69706a7af`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-6-astra` / `low`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-6-astra` / `low`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-astra-low-noharness-UkoO2Dao`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-6-astra`/`low` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Codex harness suppression

This run used a scratch-only model-catalog override for `gpt-6-astra`: `use_responses_lite` changed from `true` to `false`, `multi_agent_version` changed from `v2` to `null`, and app-server received `include_collaboration_mode_instructions=false` plus `include_environment_context=false`. The override and launcher remained below the private scratch directory; production transport configuration was not changed.

| Rollout check | Result |
| --- | --- |
| Automatic harness audit | passed |
| Exact P1 prompt segments | 1 (6,420 characters) |
| Platform permission segments | 1 (341 characters) |
| Unexpected developer segments | 0 |
| Forbidden Codex/collaboration/environment markers | none |

The inspected rollout retained the platform-owned read-only sandbox instruction. It contained no Codex coding-agent prompt, primary-agent collaboration block, multi-agent-mode block, environment-context block, skills/apps/plugins instruction block, or other unexpected developer message. This verifies suppression of the Codex collaboration harness for this run, but it is not a completely bare model request because the 341-character sandbox instruction remains.


## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | 22,088 | -3.3% |
| Decoded compact canonical answer | n/a | 30,256 | n/a |

| Schema metric | Baseline | Compact |
| --- | ---: | ---: |
| UTF-8 bytes | 15,399 | 1,717 |
| Property count | 16 | 16 |
| Enum count | 6 | 4 |
| Total enum values | 1,295 | 21 |
| `$ref` count | 0 | 0 |
| Maximum structural traversal depth | 10 | 10 |

## Provider token usage

Provider `total_tokens` is used as reported; overlapping cached/reasoning categories are not added.

| Field | Baseline | Compact | Delta |
| --- | ---: | ---: | ---: |
| input_tokens | 57,334 | 41,333 | -27.9% |
| cached_input_tokens | 0 | 0 | n/a |
| cache_write_input_tokens | 0 | 0 | n/a |
| output_tokens | 12,882 | 6,388 | -50.4% |
| reasoning_output_tokens | 7,548 | 18 | -99.8% |
| total_tokens | 70,216 | 47,721 | -32.0% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 197.533 | -31.1% |
| `turn/start` to first agent output | 1.672 | 2.063 | +23.4% |
| `turn/start` to terminal completion | 284.870 | 196.590 | -31.0% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested canonical source form: solido |
| Conservative repair, secondary only | passed (3 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 72 |
| Observations (baseline / compact) | 12 / 25 |
| Normalized shared source terms | 45 |
| Baseline-only / compact-only terms | 11 / 27 |
| Category agreement | 22 / 45 |
| Confidence agreement | 40 / 45 |
| Candidate-text overlap (intersection / union) | 39 / 82 (47.6%) |
| Alias overlap (intersection / union) | 4 / 11 (36.4%) |
| Evidence overlap (intersection / union) | 77 / 131 (58.8%) |
| Observation overlap by normalized `about + kind` | 1 / 36 (2.8%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Bodnant Park`, `Chobamba francs`, `Double-O`, `Natural human`, `postphysical elevation`, `scruitineers`, `semisentient`, `shotgun message`, `solidos`, `the executive`.
- Compact-only source forms: `Bodant Park`, `carbotanium`, `Chobamba franc`, `Darklake City`, `Digby`, `Dreamer`, `elevation mechanism`, `Gore`, `Howard Liang`, `human friend`, `Liatris`, `Likan`, `Marius`, `memory kube`, `Mr. Drixel`, `Natural`, `Protectorate`, `Radical Highers`, `restricted intelligence`, `scruitineer`, `Second Chance`, `Silfen Friend`, `Silfen Motherholme`, `solido`, `Tampico`, `trike pod`, `Trisha`.
- The primary failure was mechanical rather than a fabricated citation: exact plural forms were present in the cited source blocks. The secondary assessment changed only the three source labels; it did not rewrite meanings, candidates, or evidence.
- Recall was high. The 72 terms include useful material omitted by the baseline, such as `carbotanium`, `memory kube`, `restricted intelligence`, `Radical Highers`, and several named people and places. The observations also caught useful translation constraints: the `Garamond`/`Gralmond` discrepancy, the `Advancers`/`Accelerators` distinction, uncertain gender for Juliaca, and distinct simulated entities around Troblum.
- The output was substantially more expansive than both the baseline and Sol/medium. Some items are marginal for reusable memory, including the one-off title/name combination `Mr. Drixel`; several observations combine many entities or restate fairly direct facts. At 25 observations, the result would impose materially more human review and downstream-memory load.
- Candidate quality was uneven. Most proper names were conservatively retained, but `human friend` was proposed as `przyjaciel ludzi`. In context, a Silfen says that Clouddancer was named "a human friend", meaning a human granted a friend designation, not a friend of humankind; the proposed Polish candidate reverses that relation. `Natural` -> `Naturalny` is also grammatically brittle without a noun and is weaker than the baseline's fuller `Natural human` treatment.
- Overall manual assessment: **high recall and several strong continuity observations, but over-extractive and not reliable enough at the strict or candidate-quality boundary to prefer over Sol/medium from these samples**.

## Comparison with harness-suppressed Sol/medium

Both calls used the same source unit, compact representation, output schema, non-Lite catalog override, and verified harness suppression. They used different models and are independent stochastic samples, so this is indicative rather than a controlled repeated evaluation.

| Metric | Sol/medium | Astra/low | Delta |
| --- | ---: | ---: | ---: |
| Input tokens | 40,941 | 41,333 | +1.0% |
| Output tokens | 7,215 | 6,388 | -11.5% |
| Reasoning output tokens | 2,446 | 18 | -99.3% |
| Total tokens | 48,156 | 47,721 | -0.9% |
| Client elapsed seconds | 134.311 | 197.533 | +47.1% |
| Terms | 53 | 72 | +35.8% |
| Observations | 12 | 25 | +108.3% |
| Primary validation | passed | failed | regressed |


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. The measured latency above includes app-server startup, skill isolation, generation, late usage collection, graceful flush, and rollout copying in both runs. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server is an agent runtime and may add its small platform-owned sandbox/environment wrapper even in the isolated thin configuration.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

`gpt-6-astra`/`low` did not meet the primary-validation gate. Its very small reported reasoning usage still produced high recall and useful continuity analysis, but it was slower than Sol/medium, over-extracted substantially more review material, and included a clear translation-relation error in `human friend`.

Among the fully harness-suppressed samples, Sol/medium remains the stronger candidate for repeated evaluation. No production code or behavior was changed by this experiment.

## Recommended next step

After the manual difference review, decide whether to run a small repeated benchmark across several completed P1 units before drafting any production design. Do not infer a production migration from this single sample alone.
