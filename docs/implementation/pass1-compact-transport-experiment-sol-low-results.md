# Pass-1 compact transport experiment results — gpt-5.6-sol/low cross-model variant

## Executive summary

The reported compact run did not produce a strictly valid canonical result; failure: `Unattested canonical source form: Chobamba franc`. A deterministic secondary assessment repaired two source forms by promoting exact, attested aliases and then passed full canonical validation. The request input changed from 192,150 to 146,364 minified UTF-8 bytes (-23.8%); the output schema changed from 15,399 to 1,717 bytes (-88.8%). Provider-reported input usage fell 27.9%, total usage fell 34.1%, and client elapsed time fell 67.2%, but this cross-model result is confounded by the change from Luna/high to Sol/low.

The attempt to remove Codex's collaboration harness was only partially successful. The environment wrapper was absent, but Sol still received two platform/model collaboration developer messages totaling 2,535 characters. The result therefore does not establish a fully harness-free Sol measurement and is not suitable as production acceptance evidence.

## Repository/test identity

- Intelitex commit: `beec1ebee67351a462ecfdd7ee984c4aeff4e56d`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-sol` / `low`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `gpt-5.6-sol` / `low`
- Experiment status: `completed`; strict validation: `failed`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-sol-low-noharness-6VgPnmsI`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-sol`/`low` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Codex harness and isolation result

The reported run requested both `include_collaboration_mode_instructions=false` and `include_environment_context=false` at app-server startup and supplied the P1 prompt explicitly in non-null `turn/start.collaborationMode.settings.developer_instructions`. The saved rollout nevertheless contained these developer items:

| Developer item | Characters | Verdict |
| --- | ---: | --- |
| Platform permission/sandbox instructions | 341 | remained; expected platform-owned restriction |
| Primary-agent collaboration instructions | 2,264 | remained unexpectedly |
| Multi-agent-mode instructions | 271 | remained unexpectedly |
| Environment-context message | 0 | absent |

The turn metadata recorded `gpt-5.6-sol` / `low` and the exact compact P1 developer prompt. Skills were verified disabled, dynamic tools and capability roots were empty, runtime workspace roots were empty, approval was `never`, and the sandbox was read-only. There was no retry, rate-limit failure, or context compaction. The active Sol catalog still selected Responses Lite, so the result is **partially isolated, not harness-free**.

No corresponding startup/configuration change was retained in production code after the experiment.

## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | 13,738 | -39.9% |
| Decoded compact canonical answer | n/a | 18,278 | n/a |

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
| input_tokens | 57,334 | 41,321 | -27.9% |
| cached_input_tokens | 0 | 0 | n/a |
| cache_write_input_tokens | 0 | 0 | n/a |
| output_tokens | 12,882 | 4,952 | -61.6% |
| reasoning_output_tokens | 7,548 | 222 | -97.1% |
| total_tokens | 70,216 | 46,273 | -34.1% |
| model_context_window | 258,400 | 258,400 | +0.0% |
| scope | last_internal_operation | last_internal_operation | n/a |
| status | reported | reported | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Client `elapsed_seconds` | 286.624 | 94.026 | -67.2% |
| `turn/start` to first agent output | 1.672 | 1.714 | +2.5% |
| `turn/start` to terminal completion | 284.870 | 92.420 | -67.6% |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Compact schema parse/validation | passed |
| Local evidence-index integrity | passed |
| Expansion to canonical form | passed |
| Production `validate_result(1, ...)` primary verdict | failed — PipelineError: Unattested canonical source form: Chobamba franc |
| Conservative repair, secondary only | passed (2 deterministic repair(s)) |
| Terms (baseline / compact) | 56 / 45 |
| Observations (baseline / compact) | 12 / 9 |
| Normalized shared source terms | 30 |
| Baseline-only / compact-only terms | 26 / 15 |
| Category agreement | 19 / 30 |
| Confidence agreement | 26 / 30 |
| Candidate-text overlap (intersection / union) | 31 / 60 (51.7%) |
| Alias overlap (intersection / union) | 3 / 7 (42.9%) |
| Evidence overlap (intersection / union) | 47 / 69 (68.1%) |
| Observation overlap by normalized `about + kind` | 2 / 19 (10.5%) |


## Semantic differences

The two calls are stochastic; the accepted baseline is a comparator, not ground truth. Mechanical disagreements were reviewed against the copied source evidence, with attention to unsupported terms, missed reusable terms, identity merges, and evidence errors.

- Baseline-only source forms: `absorbers`, `Alcamo`, `Bodnant Park`, `Chatworth`, `Chobamba francs`, `communion`, `Dyson Pair`, `Fusion`, `Garamond`, `H-congruous`, `I-sentient`, `inward migration`, `Juliaca`, `Laril`, `Mellanie Rescorai`, `Natural human`, `Orion`, `postphysical elevation`, `Purlap`, `Ragnar`, `scruitineers`, `semisentient`, `solidos`, `Starflyer War`, `the executive`, `Troblum`.
- Compact-only source forms: `Admiral Juliaca`, `carbotanium`, `Chobamba franc`, `Darklake City`, `Digby`, `H-congruous world`, `hyperluminal velocity`, `I-sentient personality`, `memory kube`, `President Alcamo`, `Purlap spaceport`, `restricted intelligence`, `Second Chance`, `Silfen communion`, `Tampico`.
- The strict failure was narrow but real. The model emitted normalized/composed forms instead of exact source forms. The secondary assessment promoted the attested alias `Chobamba francs` over `Chobamba franc` and `communion` over `Silfen communion`; it did not invent evidence or rewrite meanings.
- After that repair, every schema, evidence-index, expansion, and production canonical validation check passed.
- Extraction coverage was lower than the accepted baseline: 45 versus 56 terms and 9 versus 12 observations. Several baseline terms with translation value were absent, including `absorbers`, `Dyson Pair`, `Fusion`, `Natural human`, `postphysical elevation`, `scruitineers`, `semisentient`, `solidos`, and `the executive`.
- Some differences are granularity choices rather than outright misses: Sol used `H-congruous world`, `Admiral Juliaca`, `President Alcamo`, `Purlap spaceport`, and `I-sentient personality` where the baseline used smaller lexical units. These are locally attested after repair, but several conflict with the prompt's preference for the smallest reusable unit and against ordinary title/name or place/common-noun combinations.
- Sol also found plausible, locally validated terms absent from the baseline, including `carbotanium`, `memory kube`, `restricted intelligence`, `Second Chance`, `Darklake City`, and `Digby`.
- The nine observations were concise and useful. Strong examples included the `Garamond`/`Gralmond`, `SideStar Motel`/`StarSide Motel`, and `Bodant Park`/`Bodnant Park` continuity warnings, plus the distinction between `Firstlife` and `Firstlives`.
- Overall manual assessment: **structurally sound after two mechanical repairs, but lower-recall and less disciplined about lexical-unit boundaries than the accepted Luna/high baseline**.


## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. The measured latency above includes app-server startup, skill isolation, generation, late usage collection, graceful flush, and rollout copying in both runs. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- Sol retained 2,535 characters of collaboration-harness developer instructions despite the attempted suppression, so this is not a direct bare-model measurement.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

The compact representation delivered a substantial measured efficiency improvement for this Sol/low sample, especially in output/reasoning usage and latency. It did not meet the quality gate: the primary canonical validation failed, extraction coverage was lower than the accepted baseline, and several outputs used overly broad lexical units. The conservative secondary result is usable for diagnosis, not acceptance.

The experiment also failed to produce a fully harness-free Sol call. Do not attribute the measured token count solely to the P1 prompt and compact payload, and do not adopt Sol/low or the compact protocol in production from this sample alone. No production code or behavior was changed.

## Recommended next step

If this line of testing is resumed, first establish a supported non-Responses-Lite/harness-free configuration in an isolated scratch profile, then repeat multiple completed P1 units. Keep strict primary validation as the acceptance gate; do not treat deterministic secondary repair as a successful production response.

## Additional calls made during protocol diagnosis

The tables above use only the final reported Sol/low run in `/tmp/intelitex-pass1-compact-sol-low-noharness-6VgPnmsI`. Two additional calls were made while diagnosing the requested harness setting and are excluded from the comparison tables:

| Purpose | Requested/reported model | Requested/reported effort | Input / output / reasoning / total tokens | Elapsed | Outcome |
| --- | --- | --- | ---: | ---: | --- |
| Preliminary full compact run | `gpt-5.6-sol` / `gpt-5.6-sol` | `low` / `low` | 42,588 / 5,912 / 490 / 48,500 | 117.482 s | strict failed; collaboration messages remained |
| Minimal transport smoke test | `gpt-5.6-luna` / `gpt-5.6-luna` | `low` / `low` | 1,996 / 15 / 0 / 2,011 | 4.654 s | passed; no collaboration messages |

The preliminary full-run evidence remains in `/tmp/intelitex-pass1-compact-sol-low-edLFzkzI`; the smoke evidence remains in `/tmp/intelitex-codex-harness-smoke-8ghr60_j`.
