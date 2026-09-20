# Pass-1 compact transport experiment results — gpt-5.6-terra/medium cross-model variant

## Executive summary

The Terra/medium compact call was much faster than the earlier Luna/high baseline (78.3 versus 286.6 seconds) and used 33.7% fewer total tokens, but it returned only 28 terms and 5 observations versus the baseline's 56 and 12. It also failed strict canonical validation because it normalized the typographic apostrophe in `Mellanie’s Redemption` and cited blocks containing only the original spelling. Existing conservative repair passed only by dropping that otherwise valid term. The compact request input was 23.8% smaller and its schema 88.8% smaller, but this is intentionally a cross-model/cross-effort comparison and cannot attribute the speed or quality differences to transport alone.

## Repository/test identity

- Intelitex commit: `f1b2f4ff59f07c506378e477793bc4a0f385f653`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-terra` / `medium`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-terra` / `medium`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-terra-medium-TICxPc0V`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

The copied rollout confirms the requested and effective model/effort as `gpt-5.6-terra`/`medium`, the custom 120-character base instruction with `custom` provenance, empty runtime workspace roots, the private scratch cwd, `read-only` sandbox, `never` approval, and no available tool calls. No rate-limit failure, provider retry, system error, or context compaction was recorded.

Unlike both Luna/high runs, this rollout contains two additional platform-owned developer items totaling 2,535 characters about collaboration and multi-agent behavior. They were not loaded from the repository, project `AGENTS.md`, user configuration, or the application prompt; one merely contains the literal text `AGENTS.md`. Their presence means the Terra turn did not receive exactly the same effective instruction stack as the Luna baseline despite identical Intelitex isolation controls. This is a material confounder and a residual app-server limitation for this comparison.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The selected memory contained exactly the specified `catalogue`, `matched`, and `catalogue_incomplete` fields, so no compact-memory extension was needed. Before the call, deterministic self-tests restored the exact canonical input, verified stable local block mapping and every code table, decoded the canonical result shape, and rejected negative and out-of-range evidence indices.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-terra`/`medium` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | 8,076 | -64.7% |
| Decoded compact canonical answer | n/a | 11,017 | n/a |

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

These deltas are observed cross-model deltas, not transport-only savings. Terra also received the two extra platform developer items described above.

| Field | Baseline | Compact | Delta |
| --- | ---: | ---: | ---: |
| input_tokens | 57,334 | 42,626 | -25.7% |
| cached_input_tokens | 0 | 0 | n/a |
| cache_write_input_tokens | 0 | 0 | n/a |
| output_tokens | 12,882 | 3,937 | -69.4% |
| reasoning_output_tokens | 7,548 | 1,537 | -79.6% |
| total_tokens | 70,216 | 46,563 | -33.7% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 78.340 | -72.7% |
| `turn/start` to first agent output | 1.672 | 1.833 | +9.6% |
| `turn/start` to terminal completion | 284.870 | 76.707 | -73.1% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

For context, the earlier compact Luna/high run used the identical compact payload and schema. The table below is auxiliary evidence, not a controlled model comparison because model, effort, stochastic sampling, provider conditions, and the effective platform instruction stack differ.

| Metric | Compact Luna/high | Compact Terra/medium | Terra delta |
| --- | ---: | ---: | ---: |
| Input tokens | 41,111 | 42,626 | +3.7% |
| Output tokens | 15,420 | 3,937 | -74.5% |
| Reasoning-output tokens | 10,769 | 1,537 | -85.7% |
| Total tokens | 56,531 | 46,563 | -17.6% |
| Client elapsed seconds | 283.765 | 78.340 | -72.4% |
| Terms | 55 | 28 | -49.1% |
| Observations | 10 | 5 | -50.0% |
| Primary strict validation | failed | failed | — |
| Secondary conservative validation | passed after 2 alias removals | passed after dropping 1 term | — |

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Term/alias is not attested in its cited source blocks: Mellanie's Redemption |
| Conservative repair, secondary only | passed (1 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 28 |
| Observations (baseline / compact) | 12 / 5 |
| Normalized shared source terms | 18 |
| Baseline-only / compact-only terms | 38 / 10 |
| Category agreement | 9 / 18 |
| Confidence agreement | 14 / 18 |
| Candidate-text overlap (intersection / union) | 19 / 29 (65.5%) |
| Alias overlap (intersection / union) | 1 / 3 (33.3%) |
| Evidence overlap (intersection / union) | 35 / 56 (62.5%) |
| Observation overlap by normalized `about + kind` | 1 / 16 (6.2%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Alcamo`, `Alpha Leonis`, `Augusta`, `Bodnant Park`, `Chatworth`, `Chobamba francs`, `Compression Space Transport`, `Double-O`, `Dyson Pair`, `energy and mass allocations`, `Firstlives`, `Fusion`, `Garamond`, `H-congruous`, `I-sentient`, `Inner worlds`, `inward migration`, `Juliaca`, `Kabul`, `Laril`, `Little Leo`, `Mark Vernon`, `Mellanie’s Redemption`, `Micro Leo`, `MorningLightMountain`, `Natural human`, `Nigel`, `postphysical elevation`, `Purlap`, `Ragnar`, `River-class`, `scruitineers`, `semisentient`, `Sentient Intelligence`, `shotgun message`, `solidos`, `the executive`.
- Compact-only source forms: `Admiral Juliaca`, `Darklake City`, `Digby`, `EMAs`, `Fusion with the Void`, `I-sentient personality`, `Marius`, `Mellanie's Redemption`, `President Alcamo`, `Sol barrier`.
- Six apparent compact-only items are alternate canonical forms of baseline concepts: `Admiral Juliaca` / `Juliaca`, `EMAs` / `energy and mass allocations`, `Fusion with the Void` / `Fusion`, `I-sentient personality` / `I-sentient`, `President Alcamo` / `Alcamo`, and the ASCII-apostrophe variant of `Mellanie’s Redemption`. The baseline generally better follows the smallest reusable-unit rule for the titled names.
- Terra added four clearly useful, directly attested items absent from the baseline: `Darklake City`, `Digby`, `Marius`, and `Sol barrier`. These are genuine improvements rather than schema artifacts.
- The coverage loss is nevertheless substantial. Even after accounting for alternate canonical forms, Terra omitted dozens of supported reusable items, including `Alpha Leonis`, `Bodnant Park`, `Chatworth`, `Compression Space Transport`, `Double-O`, `Dyson Pair`, `H-congruous`, `Inner worlds`, `Laril`, `MorningLightMountain`, `postphysical elevation`, `River-class`, `semisentient`, `Sentient Intelligence`, `shotgun message`, and `solidos`.
- The primary validation defect is a typography-normalization error: the source consistently uses the curly-apostrophe form `Mellanie’s Redemption`, while Terra returned ASCII `Mellanie's Redemption`. Both cited blocks contain the real ship name, but the unchanged validator correctly rejects the altered lexical form. Conservative repair drops the complete term rather than silently rewriting it, so its secondary pass does not recover the lost information.
- Shared-term category agreement was only 9/18. Most disagreements were systematic `people` versus `name` choices, while `communion` changed from `science` to `jargon`; confidence agreement was stronger at 14/18. Candidate overlap was 65.5%, showing reasonable translation-choice consistency where both calls selected the same term.
- Terra returned five concise observations. The reference observations were generally supported, and the `Clouddancer` register note was useful, but the set omitted most baseline technical/continuity cautions and was half the size of the Luna compact result. The sparse result, rather than obviously faster processing of equivalent analytical coverage, explains much of the output-token and latency reduction.

Manual assessment: Terra/medium was clearly faster and found several useful baseline omissions, but it was materially worse on Pass-1 coverage and still failed the primary correctness gate. The speed gain is not quality-neutral.


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. Terra completed 72.7% faster than the Luna/high verbose baseline and 72.4% faster than compact Luna/high. Its output and reasoning-output usage were also far lower, but it produced roughly half as many terms and observations as compact Luna. The result therefore supports a speed/coverage tradeoff, not a conclusion that Terra processed equivalent work more efficiently.

The Terra input-token count was 3.7% higher than compact Luna despite byte-identical application instructions, input payload, and schema. Model tokenization may differ, and the additional 2,535 characters of platform developer instructions are another likely contributor. The measured latency includes app-server startup, skill isolation, generation, late usage collection, graceful flush, and rollout copying. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- Terra received two additional platform-owned developer messages absent from the Luna runs, so the effective instruction context was not identical.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server is an agent runtime and may add its small platform-owned sandbox/environment wrapper even in the isolated thin configuration.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

Terra/medium is attractive on elapsed time and total token usage, but this result is not suitable as evidence of production parity. It returned about half the analytical coverage of compact Luna/high, failed the primary canonical validator, and ran with an unexpectedly different platform instruction stack. The faster completion largely coincided with much less output and reasoning work.

For Pass 1, the current evidence favors Luna/high when recall and detailed translation memory matter. Terra/medium may merit further testing as a deliberately lower-cost, lower-coverage mode, but it should not replace Luna/high under an equal-quality assumption. No production code or behavior was changed.

## Recommended next step

Before another model-quality comparison, investigate why Terra received the extra platform developer items and determine whether the installed app-server can produce the same effective thin context across models. Then repeat paired compact runs over several completed P1 units and score recall against reviewed terminology rather than raw baseline overlap alone. Keep strict pre-repair validity as the primary gate and repaired validity as secondary evidence. Do not infer a production migration from this single sample.
