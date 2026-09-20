# Pass-1 compact transport experiment results — gpt-5.6-sol/medium cross-model variant

## Executive summary

The compact request completed and passed its transport schema, evidence-index checks, canonical expansion, and the unchanged production Pass-1 validator without repair. The request input changed from 192,150 to 146,364 minified UTF-8 bytes (-23.8%); the output schema changed from 15,399 to 1,717 bytes (-88.8%). Provider-reported input usage fell 28.6%, total usage fell 31.4%, and client elapsed time fell 53.1% relative to the accepted Luna/high baseline. This was the first compact run in this experiment series to pass primary validation.

The scratch-only Codex harness suppression also passed rollout inspection. Responses Lite and multi-agent metadata were disabled for Sol, and no coding-agent, collaboration, multi-agent, environment, skills, apps, or plugins instruction leaked into the model context. The unavoidable 341-character read-only sandbox instruction remained, so this was isolated thin inference rather than a literally bare request.

## Repository/test identity

- Intelitex commit: `60eddc1527d2967c87c2e0f342a1b8a88f2de6a6`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-sol` / `medium`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-sol` / `medium`
- Experiment status: `completed`; strict validation: `passed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-sol-medium-noharness-MTDnlz5a`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-sol`/`medium` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

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
| Raw structured answer | 22,851 | 16,768 | -26.6% |
| Decoded compact canonical answer | n/a | 22,443 | n/a |

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
| output_tokens | 12,882 | 7,215 | -44.0% |
| reasoning_output_tokens | 7,548 | 2,446 | -67.6% |
| total_tokens | 70,216 | 48,156 | -31.4% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 134.311 | -53.1% |
| `turn/start` to first agent output | 1.672 | 1.692 | +1.2% |
| `turn/start` to terminal completion | 284.870 | 133.370 | -53.2% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | passed |
| Conservative repair, secondary only | not_run |
| Terms (baseline / compact) | 56 / 53 |
| Observations (baseline / compact) | 12 / 12 |
| Normalized shared source terms | 31 |
| Baseline-only / compact-only terms | 25 / 22 |
| Category agreement | 19 / 31 |
| Confidence agreement | 29 / 31 |
| Candidate-text overlap (intersection / union) | 33 / 56 (58.9%) |
| Alias overlap (intersection / union) | 4 / 6 (66.7%) |
| Evidence overlap (intersection / union) | 52 / 85 (61.2%) |
| Observation overlap by normalized `about + kind` | 3 / 21 (14.3%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Alcamo`, `Bodnant Park`, `Chatworth`, `Chobamba francs`, `communion`, `Double-O`, `Dyson Pair`, `energy and mass allocations`, `Fusion`, `Garamond`, `H-congruous`, `Inner worlds`, `inward migration`, `Juliaca`, `Leo Twins`, `MorningLightMountain`, `Natural human`, `postphysical elevation`, `Purlap`, `Ragnar`, `scruitineers`, `semisentient`, `solidos`, `the executive`.
- Compact-only source forms: `Admiral Juliaca`, `Anomine homeworld`, `Darklake City`, `daughter of our friend`, `Digby`, `elevation mechanism`, `Fusion with the Void`, `Gore`, `Howard Liang`, `Marius`, `memory kube`, `postphysical ascension`, `President Alcamo`, `Protectorate`, `Purlap spaceport`, `Radical Highers`, `restricted intelligence`, `Second Chance`, `Sol barrier`, `Tampico`, `the Leo Twins`, `Trisha`.
- The low normalized source-form overlap understates semantic overlap. Sol folded `Alcamo`, `Juliaca`, `Fusion`, `Leo Twins`, `postphysical elevation`, `Purlap`, and `the executive` into longer terms or aliases such as `President Alcamo`, `Admiral Juliaca`, `Fusion with the Void`, `the Leo Twins`, `postphysical ascension`, `Purlap spaceport`, and `Gore`.
- Coverage was close to the baseline (53 versus 56 terms; 12 versus 12 observations), and Sol found plausible, locally validated additions including `Sol barrier`, `Second Chance`, `Darklake City`, `Digby`, `Marius`, `Trisha`, `restricted intelligence`, `Radical Highers`, `Protectorate`, and `elevation mechanism`.
- Some longer units conflict with the prompt's smallest-reusable-unit preference. `Admiral Juliaca`, `President Alcamo`, `Purlap spaceport`, and `Anomine homeworld` combine a proper name with a title or ordinary noun. In particular, treating `Purlap` as an alias of `Purlap spaceport` risks conflating the place with its spaceport.
- `daughter of our friend` and its variants are attested and potentially translation-critical, but require human confirmation that they function as a recurring designation rather than a temporary description.
- Candidate quality was mostly useful, but not uniformly safe. `kuba pamięci` for `memory kube` appears erroneous or at least unjustified Polish; `kostka pamięci` would be the ordinary compositional reading. The `Gore` alternative `egzekutywa` also denotes an executive body rather than a person and should not be selected.
- The observations were concise and translation-relevant. Particularly useful items resolved `Garamond`/`Gralmond`, `Bodant Park`/`Bodnant Park`, Gore/`the executive`, the naming of `Last Throw`, and family/coreference relationships without unsupported evidence references.
- Overall manual assessment: **the strongest compact result tested so far—fully valid and broad in coverage—but still requiring normal human terminology review for granularity and candidate quality**.

## Comparison with the preceding Sol/low run

The preceding Sol/low run retained 2,535 characters of collaboration instructions and failed primary validation. The following comparison is informative but not controlled: reasoning effort, harness framing, stochastic sampling, and provider load all changed.

| Metric | Sol/low, harness retained | Sol/medium, harness suppressed | Delta |
| --- | ---: | ---: | ---: |
| Input tokens | 41,321 | 40,941 | -0.9% |
| Output tokens | 4,952 | 7,215 | +45.7% |
| Reasoning output tokens | 222 | 2,446 | +1,001.8% |
| Total tokens | 46,273 | 48,156 | +4.1% |
| Client elapsed seconds | 94.026 | 134.311 | +42.8% |
| Terms | 45 | 53 | +17.8% |
| Observations | 9 | 12 | +33.3% |
| Primary validation | failed | passed | improved |


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

This run met the experiment's structural and primary-validation gates while materially reducing input size, total token use, and elapsed time relative to the accepted Luna/high baseline. It is the best compact sample so far, but one sample does not establish repeatability, and model/effort changed from the baseline.

The result supports further testing of the compact representation with Sol/medium and the verified scratch-only harness suppression. It does not yet justify a production migration because terminology granularity and candidate quality still need human review, and the efficiency/quality result should be repeated across several units. No production transport behavior was changed.

## Recommended next step

Repeat the same harness-suppressed Sol/medium configuration across several completed P1 units and require primary validation without repair. Compare semantic recall and reviewer acceptance, not only token and latency reductions, before drafting a production design.
