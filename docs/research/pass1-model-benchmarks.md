# Pass-1 Model Benchmark Summary

## Purpose

This document preserves the useful empirical results from the Pass-1 compact-transport experiments while replacing the earlier one-file-per-run reports.

The measurements are historical research data, not current provider guarantees. Models, Codex app-server behavior, account load, and runtime versions can change. Re-run representative benchmarks before making a production profile decision that depends on current latency or model quality.

The original detailed reports and implementation prompts remain available in Git history.

## Workload and baseline

The main experiment series used one completed Pass-1 unit from the Evolutionary Void test project:

- task: `pass1/ch0005_a001`;
- source blocks: 637;
- unchanged source text: 118,718 bytes;
- canonical input payload: 192,150 minified UTF-8 bytes;
- compact input payload: 146,364 bytes (-23.8%);
- canonical output schema: 15,399 bytes;
- compact output schema: 1,717 bytes (-88.8%);
- Codex CLI during the series: 0.155.1.

The accepted verbose comparator was `gpt-5.6-luna/high`:

| Metric | Verbose Luna/high baseline |
| --- | ---: |
| Client elapsed | 286.624 s |
| Input tokens | 57,334 |
| Output tokens | 12,882 |
| Reasoning-output tokens | 7,548 |
| Total tokens | 70,216 |
| Terms | 56 |
| Observations | 12 |

The compact representation used local integer evidence indexes and was deterministically expanded back to the canonical Intelitex result before normal production validation.

These are mostly single-sample, cross-model experiments. Token, latency, and semantic differences cannot be attributed solely to the wire format.

## Consolidated results

Provider totals are used as reported; overlapping reasoning/cached categories are not added to them.

| Model | Effort | Commit | Elapsed | Input tokens | Total tokens | Terms | Obs. | Primary validation | Secondary result | Key finding |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| Luna | high | `1a8da94` | 283.765 s | 41,111 | 56,531 | 55 | 10 | failed | passed after 2 repairs | Compact greatly reduced input/schema size but did not improve latency in this sample |
| Terra | medium | `f1b2f4f` | 78.340 s | 42,626 | 46,563 | 28 | 5 | failed | passed after 1 repair | Fastest completed run, but roughly half the analytical coverage |
| Sol | low | `beec1eb` | 94.026 s | 41,321 | 46,273 | 45 | 9 | failed | passed after 2 repairs | Fast and cheap, but lower coverage and harness context was not fully suppressed |
| Sol | medium | `60eddc1` | 134.311 s | 40,941 | 48,156 | 53 | 12 | **passed** | not needed | Best balance in this series; first compact run to pass strict production validation directly |
| Astra | low | `da34444` | 197.533 s | 41,333 | 47,721 | 72 | 25 | failed | passed after 3 repairs | High recall, but over-extractive and uneven candidate quality |
| Luna | xhigh | `03afaf9` | 521.347 s | 40,941 | 69,685 | 64 | 14 | failed | passed after 1 repair | Large reasoning budget with serious entity-resolution regressions |
| Sol | high | `3a5c37c` | 309.919 s | 40,941 | 57,934 | 75 | 28 | failed | passed after 4 repairs | Much slower and more verbose than Sol/medium without a better strict-validity outcome |
| Astra | high | `ade83ea` | 333.574 s | 41,333 | 52,295 | 78 | 40 | failed | passed after 3 repairs | Stronger entity discipline than Astra/low/Luna-xhigh, but very over-extractive and heavy |
| Luna | max | `4a9977f` | >1,199.8 s | unavailable | unavailable | unavailable | unavailable | not run | timeout | Did not reach terminal completion within the 1,200 s client timeout |

## Interpretation

### Sol/medium was the strongest balance

The Sol/medium run was the only compact sample in this series that passed the unchanged production Pass-1 validator without repair.

Relative to the verbose Luna/high comparator it:

- reduced input usage by 28.6%;
- reduced total usage by 31.4%;
- reduced client elapsed time by 53.1%;
- produced 53 terms versus 56 in the comparator;
- produced the same 12 observation count;
- ran under the verified scratch-only harness suppression used in the later controlled runs.

Its result still required ordinary human terminology review. Strict structural validity does not imply that every extraction granularity or Polish candidate is optimal.

### Higher reasoning effort was not monotonically better

Sol/high produced more terms and observations, but:

- took 309.919 seconds versus 134.311 seconds for Sol/medium;
- used 57,934 total tokens versus 48,156;
- failed primary validation and required four deterministic repairs.

Luna/xhigh took 521.347 seconds and exhibited serious semantic identity errors despite its large reasoning budget. Luna/max did not finish within 20 minutes.

For this workload, increasing effort beyond the balanced setting did not produce a reliable quality/efficiency improvement.

### Terra/medium demonstrated a speed/coverage tradeoff

Terra/medium completed in 78.340 seconds and used 46,563 total tokens, but produced only 28 terms and 5 observations. It also failed strict validation on an apostrophe-normalized source form.

This sample supports treating Terra/medium as a lower-latency/lower-coverage mode, not as equal-quality evidence against the more complete runs.

The run also contained 2,535 characters of unexpected platform collaboration/multi-agent developer context, which makes direct comparison less controlled.

### Astra emphasized recall and breadth

Astra/low produced 72 terms and 25 observations; Astra/high produced 78 terms and 40 observations.

Both failed strict primary validation on normalized source forms and needed deterministic repairs. The high run showed better entity discipline than Astra/low or Luna/xhigh, but the output was substantially more expansive than the baseline and would impose more human review and downstream-memory load.

These samples are useful when maximum discovery breadth matters, but they did not demonstrate a better overall production balance than Sol/medium.

### Primary validation is the important quality gate

Several runs became valid after deterministic conservative repair. That is useful evidence that failures were often narrow lexical/source-form issues, but repaired validity is deliberately secondary.

For model/profile comparison:

1. strict pre-repair validation should be the primary structural gate;
2. repaired validity should be recorded separately;
3. semantic recall, entity resolution, terminology granularity, and reviewer burden must still be assessed manually.

A run that needs repair is not equivalent to a run that was valid directly.

## Compact transport conclusion

The compact representation itself showed durable mechanical benefits:

- input payload: 192,150 -> 146,364 bytes (-23.8%);
- output schema: 15,399 -> 1,717 bytes (-88.8%);
- compact evidence indexes round-tripped deterministically to canonical block IDs.

The experimental evidence supported productionizing compact-v1 for Codex Pass 1 while retaining canonical Intelitex data, fingerprints, validators, checkpoints, and persistence. The current production implementation should be treated as authoritative; this document preserves the experimental evidence that led to that decision.

## Harness/isolation notes

Later harness-suppressed runs used scratch-only catalog/startup overrides to remove Responses Lite collaboration framing, multi-agent metadata, and environment context. Rollout audits for the successful suppressed runs found:

- one exact application Pass-1 developer instruction;
- no coding-agent prompt;
- no collaboration or multi-agent developer block;
- no environment-context block;
- no skills/apps/plugins instruction;
- no tools/capability roots;
- read-only sandbox and `never` approval.

A small platform-owned sandbox permission instruction remained, so these were isolated thin-inference measurements rather than literally bare model requests.

Sol/low and Terra/medium were not fully comparable on this dimension because unexpected collaboration/developer context remained.

## Known qualitative findings

The following qualitative observations are important enough to retain beyond the headline metrics:

- Luna/xhigh incorrectly merged Bradley Johansson with Clouddancer and conflated `Firstlife` with `Firstlives`; additional reasoning did not prevent entity-resolution failures.
- Astra/low had high recall but included marginal reusable terms and at least one candidate whose relation was reversed in translation (`human friend`).
- Astra/high improved entity discipline but was strongly over-extractive.
- Sol/high expanded aggressively and required repairs despite the higher effort.
- Sol/medium found useful terms omitted by the baseline while remaining close to baseline coverage and passing strict validation.
- Terra/medium found some useful omissions but missed many reusable terms; its speed gain coincided with much less analytical output.
- The initial compact Luna/high run proved that a much smaller schema/request does not by itself guarantee lower latency.

## How to use these results

For future model/profile selection:

- use these measurements as historical priors, not current benchmarks;
- prefer repeated runs across several P1 units;
- keep the same source unit, compact format, prompt, validator, and isolation settings when comparing model/effort;
- record strict primary validity separately from repaired validity;
- score human reviewer burden and meaningful recall, not only raw term counts;
- retain provider-reported usage and elapsed time;
- inspect the effective Codex rollout context whenever app-server behavior changes.

If a new series materially changes the preferred production profile, update this consolidated document rather than adding another permanent one-off report.

## Historical report map

The deleted detailed reports remain recoverable from Git history. Their experiment commits were:

| Experiment | Commit |
| --- | --- |
| Initial compact Luna/high | `1a8da949ee9b36a2db56cf40f5d0d026d5c067ef` |
| Terra/medium | `f1b2f4ff59f07c506378e477793bc4a0f385f653` |
| Sol/low | `beec1ebee67351a462ecfdd7ee984c4aeff4e56d` |
| Sol/medium | `60eddc1527d2967c87c2e0f342a1b8a88f2de6a6` |
| Astra/low | `da3444432ca4ee6211f50a0f66dc7fa69706a7af` |
| Luna/xhigh | `03afaf9d5e8d95c9cdf2d34095186f9583f00913` |
| Sol/high | `3a5c37c848a30dd0932cdcd46838163b71e90282` |
| Astra/high | `ade83eaaed5026b9de23865f1b2969c3ddd961ab` |
| Luna/max | `4a9977f6deafc497d28adc5b48bfd826316c39d6` |
