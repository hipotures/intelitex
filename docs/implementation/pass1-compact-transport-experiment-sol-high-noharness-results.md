# Pass-1 compact transport experiment results — gpt-5.6-sol/high cross-model variant

## Executive summary

The compact request passed its transport schema, evidence-index checks, and canonical expansion, but failed the unchanged production Pass-1 validator on the unattested normalized form `Inner world`. A deterministic secondary assessment made four repairs and then passed validation. The request input changed from 192,150 to 146,364 minified UTF-8 bytes (-23.8%), but high effort expanded the answer to 75 terms and 28 observations. Relative to the accepted Luna/high baseline, provider-reported input usage fell 28.6% and total usage fell 17.5%, while output usage rose 31.9% and client elapsed time rose 8.1%.

Harness suppression passed exactly as in the Sol/medium run. The result nevertheless underperformed Sol/medium: it was 130.7% slower, used 20.3% more total tokens, and failed primary validation where medium passed.

## Repository/test identity

- Intelitex commit: `3a5c37c848a30dd0932cdcd46838163b71e90282`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-sol` / `high`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-sol` / `high`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-sol-high-noharness-je0p9LQv`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-sol`/`high` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Codex harness suppression

This run used a scratch-only model-catalog override for `gpt-5.6-sol`: `use_responses_lite` changed from `true` to `false`, `multi_agent_version` changed from `v2` to `null`, and app-server received `include_collaboration_mode_instructions=false` plus `include_environment_context=false`. The override and launcher remained below the private scratch directory; production transport configuration was not changed.

| Rollout check | Result |
| --- | --- |
| Automatic harness audit | passed |
| Exact P1 prompt segments | 1 (6,420 characters) |
| Platform permission segments | 1 (341 characters) |
| Unexpected developer segments | 0 |
| Forbidden Codex/collaboration/environment markers | none |

The inspected rollout retained the platform-owned read-only sandbox instruction. It contained no Codex coding-agent prompt, primary-agent collaboration block, multi-agent-mode block, environment-context block, skills/apps/plugins instruction block, or other unexpected developer message. This verifies suppression of the Codex collaboration harness for this run, but it is not a completely bare model request because the 341-character sandbox instruction remains.

The turn used a custom 120-character base instruction, one exact 6,420-character P1 developer prompt, and the compact JSON as the only user message. Skills were verified disabled, tools and capability roots were empty, the sandbox was read-only, and approval policy was `never`. No retry, rate-limit failure, or context compaction occurred.


## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | 27,018 | +18.2% |
| Decoded compact canonical answer | n/a | 35,649 | n/a |

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
| input_tokens | 57,334 | 40,941 | -28.6% |
| cached_input_tokens | 0 | 0 | n/a |
| cache_write_input_tokens | 0 | 0 | n/a |
| output_tokens | 12,882 | 16,993 | +31.9% |
| reasoning_output_tokens | 7,548 | 9,391 | +24.4% |
| total_tokens | 70,216 | 57,934 | -17.5% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 309.919 | +8.1% |
| `turn/start` to first agent output | 1.672 | 1.714 | +2.5% |
| `turn/start` to terminal completion | 284.870 | 308.975 | +8.5% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested canonical source form: Inner world |
| Conservative repair, secondary only | passed (4 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 75 |
| Observations (baseline / compact) | 12 / 28 |
| Normalized shared source terms | 38 |
| Baseline-only / compact-only terms | 18 / 37 |
| Category agreement | 21 / 38 |
| Confidence agreement | 31 / 38 |
| Candidate-text overlap (intersection / union) | 42 / 75 (56.0%) |
| Alias overlap (intersection / union) | 4 / 9 (44.4%) |
| Evidence overlap (intersection / union) | 59 / 91 (64.8%) |
| Observation overlap by normalized `about + kind` | 2 / 38 (5.3%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Alcamo`, `Bodnant Park`, `Chobamba francs`, `communion`, `Double-O`, `Fusion`, `Garamond`, `H-congruous`, `I-sentient`, `Inner worlds`, `inward migration`, `Juliaca`, `postphysical elevation`, `Purlap`, `semisentient`, `shotgun message`, `solidos`.
- Compact-only source forms: `Admiral Juliaca`, `astrogration`, `Bodant Park`, `carbotanium`, `Chobamba franc`, `condition-one alert`, `Darklake City`, `Digby`, `elevation mechanism`, `Friend’s daughter`, `Fusion with the Void`, `grade-one alert`, `Gralmond`, `H-congruous world`, `Howard Liang`, `I-sentient personality`, `Inner world`, `Liatris`, `Marius`, `memory kube`, `Motherholme communion`, `Mr. Drixel`, `postphysical ascension`, `President Alcamo`, `Protectorate`, `Purlap spaceport`, `Radical Highers`, `replicator technology`, `restricted intelligence`, `Second Chance`, `shotgun warning`, `Silfen Friend`, `Sol barrier`, `Tampico`, `toga-suit`, `Trisha`, `warrior Raiel`.
- The primary failure came from normalizing the attested plural `Inner worlds` to `Inner world`. The secondary assessment promoted the exact alias back to the source form, likewise promoted `Chobamba francs`, removed unattested alias `condition-one alert status`, and added the exact evidence block for `condition-one alert`. No meaning or candidate text was rewritten.
- After those four repairs, production canonical validation passed, but needing repairs remains a failed primary result.
- The output was much more exhaustive than the baseline: 75 versus 56 terms and 28 versus 12 observations. Some additions were useful, including `astrogration`, `carbotanium`, `Sol barrier`, `toga-suit`, `restricted intelligence`, `Radical Highers`, and explicit identity/continuity observations.
- High effort also over-extracted compositional or overly broad units. Examples include `warrior Raiel`, `replicator technology`, `Purlap spaceport`, `Admiral Juliaca`, `President Alcamo`, `Mr. Drixel`, and `Friend’s daughter`. Several conflict with the instruction to store the smallest reusable lexical unit and avoid ordinary title/name or name/common-noun combinations.
- The 28 observations include valuable identity, continuity, register, and technical constraints, but also many straightforward gender facts. This is substantially more memory material than the accepted baseline and would increase review and downstream-memory load.
- Candidate quality was mixed. `kostka pamięci` correctly leads the `memory kube` candidates, improving on Sol/medium, but alternatives such as `kub pamięci`, `egzekutywa` for a person designated `the executive`, and `Raielowie-wojownicy` remain questionable.
- Overall manual assessment: **high recall but over-extractive, slower, and less reliable at the strict boundary than Sol/medium; not the preferred configuration from these samples**.

## Comparison with harness-suppressed Sol/medium

Both runs used the same source, compact representation, output schema, model, non-Lite catalog override, and verified harness suppression. They remain independent stochastic samples, so the table is indicative rather than a controlled repeated evaluation.

| Metric | Sol/medium | Sol/high | Delta |
| --- | ---: | ---: | ---: |
| Input tokens | 40,941 | 40,941 | +0.0% |
| Output tokens | 7,215 | 16,993 | +135.5% |
| Reasoning output tokens | 2,446 | 9,391 | +283.9% |
| Total tokens | 48,156 | 57,934 | +20.3% |
| Client elapsed seconds | 134.311 | 309.919 | +130.7% |
| Terms | 53 | 75 | +41.5% |
| Observations | 12 | 28 | +133.3% |
| Primary validation | passed | failed | regressed |


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. The measured latency above includes app-server startup, skill isolation, generation, late usage collection, graceful flush, and rollout copying in both runs. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server retained a 341-character platform-owned sandbox instruction. The environment and collaboration wrappers were absent in the inspected rollout.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

Sol/high did not meet the primary-validation gate and offered a poor efficiency/quality tradeoff against harness-suppressed Sol/medium. Its greater reasoning budget produced substantially more terms and observations, but also more over-extraction, four deterministic repairs, 135.5% more output tokens, and 130.7% greater elapsed time.

Among the tested Sol configurations, medium remains the preferred candidate for repeated evaluation. No production code or behavior was changed by this experiment.

## Recommended next step

Prioritize repeated harness-suppressed Sol/medium runs across several completed P1 units. Keep primary validation without repair as the acceptance gate. There is no evidence from this sample that Sol/high merits further testing before medium's repeatability is established.
