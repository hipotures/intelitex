# Pass-1 compact transport experiment results — gpt-6-astra/high cross-model variant

## Executive summary

The single `gpt-6-astra`/`high` compact call completed in 333.574 seconds and passed the compact schema, evidence-index, canonical-expansion, and harness audits. It failed the unchanged production Pass-1 validator after normalizing three exact plural source forms to unattested singulars: `solido`, `Inner world`, and `scruitineer`. A deterministic secondary assessment restored `solidos`, `Inner worlds`, and `scruitineers`; the repaired result passed full canonical validation.

The provider reported 41,333 input tokens, 10,962 output tokens, 2,070 reasoning-output tokens, and 52,295 total tokens. Manual review found substantially better entity discipline than Astra/low or Luna/xhigh, including the correct distinction between Bradley Johansson and Clouddancer, `Firstlife` and `Firstlives`, and the reversed meanings of `human friend` and `Silfen Friend`. The tradeoff was severe over-extraction: 78 terms and 40 observations. This was a one-call experimental benchmark, not a production change.

## Repository/test identity

- Intelitex commit: `ade83eaaed5026b9de23865f1b2969c3ddd961ab`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-6-astra` / `high`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-6-astra` / `high`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-astra-high-noharness-EBgIk4PM`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-6-astra`/`high` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

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
| Raw structured answer | 22,851 | 32,373 | +41.7% |
| Decoded compact canonical answer | n/a | 42,474 | n/a |

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
| output_tokens | 12,882 | 10,962 | -14.9% |
| reasoning_output_tokens | 7,548 | 2,070 | -72.6% |
| total_tokens | 70,216 | 52,295 | -25.5% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 333.574 | +16.4% |
| `turn/start` to first agent output | 1.672 | 1.651 | -1.3% |
| `turn/start` to terminal completion | 284.870 | 332.634 | +16.8% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested canonical source form: solido |
| Conservative repair, secondary only | passed (3 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 78 |
| Observations (baseline / compact) | 12 / 40 |
| Normalized shared source terms | 45 |
| Baseline-only / compact-only terms | 11 / 33 |
| Category agreement | 22 / 45 |
| Confidence agreement | 38 / 45 |
| Candidate-text overlap (intersection / union) | 51 / 85 (60.0%) |
| Alias overlap (intersection / union) | 5 / 13 (38.5%) |
| Evidence overlap (intersection / union) | 78 / 132 (59.1%) |
| Observation overlap by normalized `about + kind` | 2 / 50 (4.0%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Bodnant Park`, `Chobamba francs`, `Double-O`, `Inner worlds`, `Natural human`, `postphysical elevation`, `scruitineers`, `shotgun message`, `solidos`, `the executive`.
- Compact-only source forms: `Bodant Park`, `carbotanium`, `cybersphere`, `Darklake City`, `Digby`, `Dreamer`, `enrichment`, `Gore`, `Howard Liang`, `Inner world`, `Liatris`, `Likan`, `macrocellular clusters`, `Marius`, `memory kube`, `Mr. Drixel`, `Natural`, `nucleus`, `postphysical ascension`, `postphysicals`, `Protectorate`, `Radical Highers`, `restricted intelligence`, `scruitineer`, `Second Chance`, `shotgun warning`, `Silfen Friend`, `Silfen Motherholme`, `solido`, `storage lacuna`, `Tampico`, `trike pod`, `Trisha`.
- All three primary-validation failures were deterministic source-form normalization errors. The secondary assessment changed only the source labels to exact attested plurals; meanings, candidates, aliases, and evidence were left intact.
- Entity handling was a clear strength. Bradley Johansson and Clouddancer remained separate speakers; `Firstlife` and `Firstlives` remained distinct; Chatworth was not merged with Donald Chatfield; Catriona, Trisha, and the Sentient Intelligence remained separate; and uncertain identification of the Silfen-described machine with the inversion core was explicitly qualified.
- The model correctly explained the opposite relations encoded by `human friend` and `Silfen Friend`, avoiding Astra/low's mistranslation of the former as `przyjaciel ludzi`. It also preserved attribution and uncertainty in the `Fusion`/inversion-core dispute and in the `Advancers`/`Accelerators` source inconsistency.
- Useful terminology additions include `carbotanium`, `memory kube`, `restricted intelligence`, `Radical Highers`, `postphysicals`, and the more complete aliases for `storage lacuna` and `shotgun warning`.
- Recall was excessive. Forty observations are more than three times the baseline count, and several aggregate routine gender facts or restate local source details unlikely to justify durable translation memory. Marginal term entries include the generic `nucleus`, one-off `Mr. Drixel`, and potentially redundant re-emissions of established memory items. This materially increases review and downstream-memory load.
- Candidate quality was generally stronger than Astra/low, but not uniformly settled: `Naturalny` is grammatically brittle as a standalone category, and candidates such as `kierownictwo` for Gore as an individual or `zasobnik pamięci` for `storage lacuna` need human rejection or review.
- Overall manual assessment: **the strongest entity-resolution output among the new Luna/Astra variants, but too expansive and still mechanically noncompliant for direct acceptance**.

## Comparison with harness-suppressed Astra/low

Both Astra calls used the same source, compact representation, schema, non-Lite catalog override, and verified harness suppression. They are independent stochastic samples, so the comparison is indicative rather than a controlled repeated evaluation.

| Metric | Astra/low | Astra/high | Delta |
| --- | ---: | ---: | ---: |
| Input tokens | 41,333 | 41,333 | +0.0% |
| Output tokens | 6,388 | 10,962 | +71.6% |
| Reasoning output tokens | 18 | 2,070 | +11,400.0% |
| Total tokens | 47,721 | 52,295 | +9.6% |
| Client elapsed seconds | 197.533 | 333.574 | +68.9% |
| Terms | 72 | 78 | +8.3% |
| Observations | 25 | 40 | +60.0% |
| Primary validation | failed | failed | unchanged |


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. Timing, when available, begins before app-server startup and skill isolation; a failed or timed-out call can end before late usage collection, graceful flush, or rollout copying. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server is an agent runtime and may add its small platform-owned sandbox/environment wrapper even in the isolated thin configuration.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

`gpt-6-astra`/`high` delivered the best qualitative entity and continuity analysis among the newly requested Luna/Astra variants, without the severe identity errors seen in Luna/xhigh. It nevertheless failed the primary validator, required three deterministic repairs, and produced far more review material than the baseline or Sol/medium.

For a production-oriented repeated evaluation, harness-suppressed Sol/medium remains the best efficiency/acceptance starting point because it passed primary validation with 53 terms and 12 observations. Astra/high is the more promising candidate only when higher semantic recall is worth substantially more review. No production pipeline behavior was changed.

## Recommended next step

If benchmarking continues later, repeat Sol/medium and Astra/high across several completed P1 units and score entity-resolution errors separately from mechanical source-form repairs. Do not infer a production migration from this single sample alone.
