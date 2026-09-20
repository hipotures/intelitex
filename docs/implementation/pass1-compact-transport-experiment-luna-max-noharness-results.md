# Pass-1 compact transport experiment results — gpt-5.6-luna/max cross-model variant

## Executive summary

The single `gpt-5.6-luna`/`max` compact call reached its configured 1,200-second timeout before app-server emitted terminal turn completion. The client then sent one `turn/interrupt`; it did not retry. No final structured answer or provider token-usage event was available, so schema, evidence, canonical, and semantic validation could not run. The request input changed from 192,150 to 146,364 minified UTF-8 bytes (-23.8%); the output schema changed from 15,399 to 1,717 bytes (-88.8%). This was a one-call experimental benchmark, not a production change.

## Repository/test identity

- Intelitex commit: `4a9977f6deafc497d28adc5b48bfd826316c39d6`
- Codex CLI: `0.155.1`
- Baseline: `pass1/ch0005_a001`, attempt 1
- Source blocks: 637
- Baseline requested model/effort: `gpt-5.6-luna` / `high`
- Compact requested model/effort: `gpt-5.6-luna` / `max`
- Baseline reported model/effort: `gpt-5.6-luna` / `high`
- Compact reported model/effort: `unavailable` / `unavailable`
- Experiment status: `failed after submission`; strict validation: `not_run`

## Safety statement

The live project `/home/user/translations/evolutionary-void-v3` was treated as read-only. The harness did not open its SQLite database, acquire its lock, invoke a pipeline command, or create any file below it. It selected a completed checkpointed artifact, verified a stable checksum snapshot while copying, then performed all later work from `/tmp/intelitex-pass1-compact-luna-max-noharness-jPfsSlqB`. Raw source, complete prompts, schemas, responses, transport logs, and rollout evidence remain only in that private scratch tree and are not committed.

## Representations tested

The baseline used canonical block objects, canonical memory objects, canonical long output keys, and the request-specific evidence-ID enums. The compact request used `[i,k,s,f,t]` block rows plus one `BLOCK_KINDS` lookup, compact lossless memory rows, short output keys, integer codes, and local integer evidence indices restored to canonical IDs before validation. The experimental output schema is flat: no `$ref`, `$defs`, recursion, schema composition, regex, or request-specific evidence enum.

The semantic Pass-1 rules, exact source strings and order, memory values and order, production Codex client, isolation, sandbox, approval policy, and output task were held constant. At the user's request, the compact call changed model/effort from `gpt-5.6-luna`/`high` to `gpt-5.6-luna`/`max` in addition to the compact input/output representation and schema. This cross-model comparison cannot attribute quality, token, or latency differences solely to transport encoding.

## Codex harness suppression

This run used a scratch-only model-catalog override for `gpt-5.6-luna`: `use_responses_lite` changed from `true` to `false`, `multi_agent_version` changed from `v1` to `null`, and app-server received `include_collaboration_mode_instructions=false` plus `include_environment_context=false`. The override and launcher remained below the private scratch directory; production transport configuration was not changed.

| Rollout check | Result |
| --- | --- |
| Automatic harness audit | not_run |
| Exact P1 prompt segments | unavailable (unavailable characters) |
| Platform permission segments | unavailable (unavailable characters) |
| Unexpected developer segments | unavailable |
| Forbidden Codex/collaboration/environment markers | unavailable |

The scratch override and request configuration were recorded, but no completed rollout was copied for inspection. Harness suppression therefore could not be verified from rollout evidence for this timed-out call.


## Request/schema size

All JSON sizes use minified UTF-8 serialization.

| Component | Baseline bytes | Compact bytes | Delta |
| --- | ---: | ---: | ---: |
| Unchanged source text only | 118,718 | 118,718 | +0.0% |
| Input payload | 192,150 | 146,364 | -23.8% |
| Developer instructions | 6,057 | 6,420 | +6.0% |
| Output schema | 15,399 | 1,717 | -88.8% |
| Raw structured answer | 22,851 | unavailable | n/a |
| Decoded compact canonical answer | n/a | unavailable | n/a |

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
| input_tokens | 57,334 | unavailable | n/a |
| cached_input_tokens | 0 | unavailable | n/a |
| cache_write_input_tokens | 0 | unavailable | n/a |
| output_tokens | 12,882 | unavailable | n/a |
| reasoning_output_tokens | 7,548 | unavailable | n/a |
| total_tokens | 70,216 | unavailable | n/a |
| model_context_window | 258,400 | unavailable | n/a |
| scope | last_internal_operation | unavailable | n/a |
| status | reported | unavailable | n/a |

## Timing

| Metric | Baseline seconds | Compact seconds | Delta |
| --- | ---: | ---: | ---: |
| Configured client timeout | n/a | 1,200.000 | n/a |
| `turn/start` to first agent output | 1.672 | 1.778 | +6.3% |
| `turn/start` to client interrupt | n/a | 1,199.822 | n/a |

First-output timing is reported only when a corresponding app-server event exists in the recorded JSONL.

## Validation and correctness

| Check | Result |
| --- | --- |
| Strict compact/canonical validation | failed: PipelineError: Codex app-server timed out before terminal turn completion. |


## Semantic differences

Strict comparison was unavailable because no validated compact result was produced.

## Observed schema/latency behavior

The compact schema removed the two request-specific arrays of 637 canonical evidence IDs and retained only three small numeric code enums. The failed call ended before terminal usage collection, graceful flush, or rollout copying. No recursive or indirect schema construct was introduced.

## Confounders and limitations

- This is one compact run compared with one earlier accepted baseline run, so sampling variance and provider load are confounded with representation changes.
- Model/effort were intentionally changed, making this a cross-model rather than transport-only comparison.
- The baseline and compact calls may experience different account-level queueing, cache state, and concurrent provider load.
- App-server is an agent runtime and may add its small platform-owned sandbox/environment wrapper even in the isolated thin configuration.
- Baseline first-output timing may be unavailable if that CLI event was not recorded; no timing is inferred.
- Mechanical overlap does not prove semantic equivalence, and the baseline itself can contain mistakes.
- The experiment changed input encoding, output encoding, schema, and their necessary contract wording together; it cannot isolate the causal contribution of each one.

## Conclusion

`gpt-5.6-luna`/`max` did not finish this workload within 20 minutes. Because app-server supplied neither terminal completion nor usage, the run provides latency/failure evidence but no quality or token-efficiency evidence. No production pipeline behavior was changed.

## Recommended next step

Follow the requested fallback sequence with `gpt-5.6-luna`/`xhigh` under the same 20-minute limit.
