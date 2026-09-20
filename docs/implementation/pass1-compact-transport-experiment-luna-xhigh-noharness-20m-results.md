# Pass-1 compact transport experiment results — gpt-5.6-luna/xhigh cross-model variant

## Executive summary

The single `gpt-5.6-luna`/`xhigh` compact call completed in 521.347 seconds, well before its 1,200-second timeout. It passed the compact schema, evidence-index, canonical-expansion, and harness audits, but failed the unchanged production Pass-1 validator on the unattested normalized source form `Silfen communion`. A deterministic secondary assessment promoted its exact attested alias `communion`, after which canonical validation passed.

The provider reported 40,941 input tokens, 28,744 output tokens, 23,107 reasoning-output tokens, and 69,685 total tokens. Despite that large reasoning budget, manual review found serious entity-resolution regressions: the result merged Bradley Johansson with Clouddancer and merged `Firstlife` with `Firstlives`, even though the source and prompt require those referents to remain distinct. This was a one-call experimental benchmark, not a production change.

## Repository/test identity

- Intelitex commit: `03afaf9d5e8d95c9cdf2d34095186f9583f00913`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-luna` / `xhigh`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-luna` / `xhigh`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-luna-xhigh-noharness-20m-sCRH5A13`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-luna`/`xhigh` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Codex harness suppression

This run used a scratch-only model-catalog override for `gpt-5.6-luna`: `use_responses_lite` changed from `true` to `false`, `multi_agent_version` changed from `v1` to `null`, and app-server received `include_collaboration_mode_instructions=false` plus `include_environment_context=false`. The override and launcher remained below the private scratch directory; production transport configuration was not changed.

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
| Raw structured answer | 22,851 | 19,638 | -14.1% |
| Decoded compact canonical answer | n/a | 26,508 | n/a |

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
| output_tokens | 12,882 | 28,744 | +123.1% |
| reasoning_output_tokens | 7,548 | 23,107 | +206.1% |
| total_tokens | 70,216 | 69,685 | -0.8% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 521.347 | +81.9% |
| `turn/start` to first agent output | 1.672 | 1.869 | +11.8% |
| `turn/start` to terminal completion | 284.870 | 520.412 | +82.7% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested canonical source form: Silfen communion |
| Conservative repair, secondary only | passed (1 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 64 |
| Observations (baseline / compact) | 12 / 14 |
| Normalized shared source terms | 46 |
| Baseline-only / compact-only terms | 10 / 18 |
| Category agreement | 26 / 46 |
| Confidence agreement | 38 / 46 |
| Candidate-text overlap (intersection / union) | 51 / 84 (60.7%) |
| Alias overlap (intersection / union) | 7 / 12 (58.3%) |
| Evidence overlap (intersection / union) | 81 / 120 (67.5%) |
| Observation overlap by normalized `about + kind` | 1 / 25 (4.0%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Bodnant Park`, `Chobamba francs`, `Clouddancer`, `communion`, `Firstlives`, `Inner worlds`, `postphysical elevation`, `solidos`, `the executive`.
- Compact-only source forms: `astrogration`, `carbotanium`, `cybersphere`, `Darklake City`, `elevation`, `Howard Liang`, `hyperdrive`, `hyperluminal`, `Liatris McPeierl`, `Marius`, `memory kube`, `replicator technology`, `restricted intelligence`, `Second Chance`, `Silfen communion`, `Sol barrier`, `Tampico`, `Trisha`.
- The primary validator failure was mechanical: `Silfen communion` was not an exact source span, while `communion` was present as its alias. The secondary assessment changed only that source label and then passed canonical validation.
- The output made a critical unsupported merge by storing `Clouddancer` as an alias of `Bradley Johansson` and explicitly claiming they are the same entity. The cited dialogue instead introduces them separately: Bradley says, "I'm Bradley Johansson, and this is Clouddancer." This directly violates the prompt's entity-resolution rule.
- It also stored `Firstlives` as an alias of `Firstlife`, despite the text using `Firstlife` for a human status and `Firstlives` for the alleged creators of the Void. The prompt specifically requires lexical similarity without identity evidence to remain separate.
- `Garamond` and `Gralmond` were merged as source plus alias while the accompanying observation admits only a possible spelling inconsistency. That is less severe but still violates the rule not to merge uncertain identities.
- Useful additions include `carbotanium`, `memory kube`, `Sol barrier`, `restricted intelligence`, and several conservative proper-name entries. The result was moderately expansive at 64 terms and 14 observations, but those gains do not offset the entity errors.
- Overall manual assessment: **failed quality gate; xhigh spent substantially more reasoning and time than Luna/high while introducing explicit, translation-critical identity merges**.


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

`gpt-5.6-luna`/`xhigh` finished within the 20-minute limit, so the conditional 30-minute retry was not triggered. It failed primary canonical validation and, more importantly, failed the manual entity-resolution gate despite 23,107 reasoning-output tokens. This configuration is not a viable replacement for the accepted baseline from this sample. No production pipeline behavior was changed.

## Recommended next step

Proceed with the requested final benchmark using `gpt-6-astra`/`high`, then compare its strict and semantic quality against the completed harness-suppressed runs.
