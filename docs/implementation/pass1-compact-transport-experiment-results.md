# Pass-1 compact transport experiment results

## Executive summary

The compact experiment reduced provider-reported input tokens by 28.3% and total tokens by 19.5%, but it did not reduce elapsed time materially and did not produce a strictly valid canonical result. The primary failure was an unattested `Oscean` alias for `Ocisen`; a secondary application of the existing conservative repair found one additional unattested alias, removed both, and then passed validation. The request input changed from 192,150 to 146,364 minified UTF-8 bytes (-23.8%), while the output schema changed from 15,399 to 1,717 bytes (-88.8%). This was a one-call experimental benchmark, not a production change.

## Repository/test identity

- Intelitex commit: `1a8da949ee9b36a2db56cf40f5d0d026d5c067ef`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Requested model/effort: `gpt-5.6-luna` / `high`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-luna` / `high`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-transport-vK6WufCz`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

The copied rollout confirms that the custom 120-character base instruction had `custom` provenance, the turn used the private scratch work directory with `read-only` sandbox and `never` approval, model effort was `high`, runtime workspace roots were empty, and no tools or skills were exposed in the turn context. No project `AGENTS.md` marker was present in the initial rollout context. This confirms the existing Intelitex thin-client isolation behavior for the experiment; it does not remove the documented possibility of a small platform-owned sandbox/environment wrapper.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The selected memory contained exactly the specified `catalogue`, `matched`, and `catalogue_incomplete` fields, so the compact `c`, `m`, and `i` representation required no extension. A deterministic round trip restored the exact canonical input, including source order, source strings, scene boundaries, nulls, and list order, before the model call.

The semantic Pass-1 rules, exact source strings and order, memory values and order, model, effort, production Codex client, isolation, sandbox, approval policy, and output task were held constant. The changed variables were the input/output transport representation, schema, and the minimum contract wording needed to describe them.

## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | 16,393 | -28.3% |
| Decoded compact canonical answer | n/a | 22,061 | n/a |

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
| input_tokens | 57,334 | 41,111 | -28.3% |
| cached_input_tokens | 0 | 0 | n/a |
| cache_write_input_tokens | 0 | 0 | n/a |
| output_tokens | 12,882 | 15,420 | +19.7% |
| reasoning_output_tokens | 7,548 | 10,769 | +42.7% |
| total_tokens | 70,216 | 56,531 | -19.5% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 283.765 | -1.0% |
| `turn/start` to first agent output | 1.672 | 2.481 | +48.4% |
| `turn/start` to terminal completion | 284.870 | 282.150 | -1.0% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested alias(es) for Ocisen: ['Oscean'] |
| Conservative repair, secondary only | passed (2 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 55 |
| Observations (baseline / compact) | 12 / 10 |
| Normalized shared source terms | 40 |
| Baseline-only / compact-only terms | 16 / 15 |
| Category agreement | 34 / 40 |
| Confidence agreement | 27 / 40 |
| Candidate-text overlap (intersection / union) | 49 / 68 (72.1%) |
| Alias overlap (intersection / union) | 5 / 12 (41.7%) |
| Evidence overlap (intersection / union) | 68 / 99 (68.7%) |
| Observation overlap by normalized `about + kind` | 0 / 22 (0.0%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Anomine`, `Bodnant Park`, `Chatworth`, `Chobamba francs`, `communion`, `energy and mass allocations`, `Firstlives`, `Inner worlds`, `inward migration`, `Juliaca`, `Nigel`, `postphysical elevation`, `semisentient`, `solidos`, `the executive`.
- Compact-only source forms: `Admiral Juliaca`, `carbotanium`, `Darklake City`, `Digby`, `EMAs`, `executive`, `hyperdrive`, `Marius`, `memory kube`, `Motherholme`, `Ocisen`, `restricted intelligence`, `Second Chance`, `Sol barrier`, `Tampico`.
- Equivalent lexical choices account for three apparent differences: `Juliaca` / `Admiral Juliaca`, `energy and mass allocations` / `EMAs`, and `the executive` / `executive`. The baseline better follows the smallest-unit rule for `Juliaca`; the other two pairs are largely canonical-form choices rather than lost concepts.
- The compact result was clearly better on several baseline omissions. `Marius`, `carbotanium`, `hyperdrive`, `memory kube`, `restricted intelligence`, `Darklake City`, `Digby`, and `Second Chance` are directly attested, reusable items with relevant evidence. It also folded `Firstlives` into an alias of `Firstlife`, avoiding the baseline's separate entries for singular and plural forms.
- The baseline was clearly better on other omissions. The compact result missed directly supported reusable items including `Anomine`, `communion`, `postphysical elevation`, `semisentient`, `Inner worlds`, `Bodnant Park` plus its spelling variant, and the `Garamond` / `Gralmond` and `Bodnant` / `Bodant` continuity issues.
- The primary correctness defect is real rather than an encoding or index-decoding error. `Ocisen` occurs in the selected source, but `Oscean` does not; the model appears to have generalized it from the existing-memory entry `Oscean Empire`. `Silfen Motherholme` is likewise absent as an exact source form in this unit. The production conservative repair removed these two aliases and made the otherwise unchanged compact result valid, but that is only a secondary recovery result.
- Category disagreements for shared terms were mostly defensible taxonomy choices (`people` versus `name`, `technology` versus `science` or `jargon`). Confidence differed more often: 13 of 40 shared terms. Candidate overlap remained reasonably high at 72.1%, while the low alias overlap reflects both different extraction choices and the two invalid aliases.
- The observation sets emphasized different facts. The compact result added useful observations about distinct identities, gender, `Last Throw`, and Dark Fortress technology, but grouped several unrelated people into shared gender observations and omitted baseline technical cautions for `Fusion`, `H-congruous`, `M-sink`, `communion`, and `Firstlife`. The 0% exact `about + kind` overlap is harsher than a semantic reading: several observations cover the same relationship under a different kind or with a slightly different `about` set.

Overall, neither result dominates semantically. The compact call shows substantial stochastic churn and two strict-validation defects, so this run does not establish correctness parity even though most differences are supported and some compact-only discoveries are improvements.


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. This cut schema bytes by 88.8% and provider input tokens by 28.3%. Output tokens rose 19.7% and reasoning-output tokens rose 42.7%, offsetting some of the input saving; total tokens still fell 19.5%.

Despite the much smaller request and schema, total elapsed time was effectively unchanged (-1.0%), as was turn-to-terminal time (-1.0%). Therefore this sample does not support the hypothesis that the flatter schema alone materially improves latency for this workload. No rate-limit failure, provider retry, context compaction, or obvious account-level queueing symptom was recorded. The measured latency includes app-server startup, skill isolation, generation, late usage collection, graceful flush, and rollout copying in both runs. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server is an agent runtime and may add its small platform-owned sandbox/environment wrapper even in the isolated thin configuration.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

Compact transport is promising for request and token efficiency, but this single run is not sufficient for a production design. It achieved large input/schema reductions and preserved mechanically correct evidence decoding, yet showed no latency improvement and failed strict canonical validation on two unattested aliases before conservative repair. The semantic comparison also showed substantial two-way extraction churn rather than clear parity.

The appropriate verdict is **continue experimenting, do not migrate production yet**. No production code or behavior was changed by this experiment.

## Recommended next step

Run a small repeated benchmark across several completed P1 units, with at least two samples per representation or paired repeats if cost permits. Separate the input-row encoding and simplified output schema into distinct variants so their token, latency, and correctness effects can be attributed. Track strict pre-repair validity as the primary quality measure and repaired validity only as secondary evidence. If parity holds across units, draft a production design that retains local index validation and the current canonical validator. Do not infer a production migration from this single sample alone.
