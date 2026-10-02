# PRD: P0-P5 with durable source sessions and revert-to-P0 execution

**Status:** Ready for implementation; documentation only in this commit.  
**Date:** 2026-10-02  
**Repository:** `hipotures/intelitex`  
**Reviewed baseline:** `e4b622fc447c1df71028aa114cff6fdbd41d7204`  
**Implementation branch:** `work/p0-p5-persistent-sessions`  
**Scope:** Complete P0-P5 backend, prompts, persistence, recovery, diagnostics and tests. No web interface redesign.

## 1. Implementation mandate

Implement the complete architecture in this document, not a P0-only prototype. Work exclusively in the dedicated branch/worktree. Do not modify, merge into, reset, restart, or deploy the production `main` checkout. Do not use an active production translation workspace as a test destination.

The target execution model is:

```text
One source scope + compatible Codex configuration
    -> one durable private runtime and one persistent thread
    -> P0 loads the source and acknowledges readiness
    -> P1 executes, its canonical output is persisted, history returns to P0
    -> human review remains a book-wide gate
    -> P2 executes, its canonical output is persisted, history returns to P0
    -> P3 executes with accepted P2, history returns to P0
    -> P4 executes with accepted P2 and P3, history returns to P0
    -> P5 executes with accepted P3 and P4, history returns to P0
```

P1 has a different dependency graph, not a different session lifecycle. P1 memory flows forward across ordered analysis units. P2-P5 artifacts flow between passes within a translation chunk. Their source sessions use the same preparation, execution, evidence, recovery and cleanup mechanism.

All implementation files, documentation, comments and diagnostics added by this work must be in English. The existing English-to-Polish translation behavior is unchanged.

## 2. Decisions and non-goals

### 2.1 Required decisions

1. **Revert after every P1-P5 attempt once its outcome is durable.** Before any subsequent attempt, verify that the active history contains only the accepted P0 baseline. Cleanup is idempotent; do not issue a redundant revert when already at baseline.
2. **Do not retain P2-P3 or P4-P5 pairs in the new execution mode.** Supply canonical dependency artifacts explicitly after each revert, including deterministic repairs. This trades some repeated dependency input for bounded context, simpler recovery, independent P4 auditing and one universal lifecycle.
3. **Do not fork.** The new mode must not call `thread/fork`, create ephemeral children, stage an exported parent into a new home per pass, or silently fall back to fresh-root generation.
4. **P0 contains source, not analysis or review state.** No `EXISTING_MEMORY`, approved lexicon, observations from prior analysis, translation continuity, draft, audit or correction ledger belongs in the P0 source baseline. Stage-specific P1-P5 instructions also belong after the baseline, not in its source payload.
5. **P1-P5 do not retransmit the current source as a new model-visible message.** They refer to P0 and send only their active instructions, source/sentence references, dynamic memory and required accepted artifacts. Offline semantic requests may retain full source for validation and recovery.
6. **Preserve canonical task semantics.** No changed block/chunk boundaries, new translations during P0/P1, skipped review, weakened validation, or automatic replacement of successful work.
7. **Persistent session state is not provider prompt cache.** Cache expiration does not invalidate P0. A zero-cache response is not an error and must not trigger another inference.
8. **No new web controls are required.** Existing web-started analysis/translation jobs must ensure the required P0 internally before new Codex inference. The frontend remains unchanged.

### 2.2 Out of scope

Web P0 rows, buttons, layouts, progress redesign, new framework adoption, whole-book conversation threads, provider cache keep-alive traffic, new local-model session protocols, a new workflow engine, automatic production migration, and benchmark-driven removal of review or validators are out of scope.

This is not a guarantee of zero repeated tokens at the provider. Codex may internally replay durable context to the inference service. The guarantee is that Intelitex adds the current source only once per accepted P0 session generation and preserves a stable reusable baseline.

## 3. Evidence and baseline audit

### 3.1 Previously reported live test

The preceding project discussion reported a successful source-only P0/revert/cold-resume probe on Codex `0.160.0`, model `gpt-6.1-sol`, effort `high`. It used 15,194 source bytes from `salvation-03/ch0022_c0001`, six live calls without retries, and retained the same thread across restart. Reported usage was 7,139 input / 2,688 cached tokens for P0 and approximately 7,173-7,189 input / 6,912 cached tokens for subsequent questions. After cleanup, only P0 remained in active history.

The previous discussion identified `/tmp/intelitex-revert-cache-test/result.md` and `usage_summary.json` as evidence. Those files were not available for independent inspection during this PRD preparation. Treat these numbers as prior reported measurements, not new measurements, an SLA, or proof that the complete production P0-P5 pipeline already exists. Reproduce the relevant lifecycle with a new scratch fixture during implementation.

The repository's `experiments/codex_session_cache_probe.py` and `experiments/codex_cache_v3_session.py` are useful references for persistent connections, isolated environments, event filtering, canonical validation and evidence. The latter is a sequential P2-P5 experiment, not the desired revert-to-P0 production implementation.

### 3.2 Current implementation and required changes

| Area | Observed baseline | Required treatment |
|---|---|---|
| `bookpipe/application/pipeline.py` | `execute_analyze` creates ordered P1 memory snapshots; translation and target-pass paths construct full source inputs independently. | Preserve graph and gates; route all new stage execution through one source-session-aware runner. Remove duplicated dependency construction where practical. |
| `bookpipe/engine.py` | `Runner.run` owns fingerprints, saved-result reuse, compatible-attempt recovery, retries, codecs, validation and acceptance. Artifacts use `artifacts/passN/<unit>/...`. | Preserve these guarantees; add the session boundary after no-generation reuse checks. Centralize paths and stage definitions. |
| `bookpipe/codex_transport.py` | `_runtime` creates a timestamp/PID-specific home, SQLite directory and work directory. `generate` launches a process and selects fresh-root, parent-fork or paired-resume paths. | Separate durable runtime ownership from per-attempt evidence/process ownership; add real same-home resume and revert. |
| `bookpipe/codex_pair.py` | P3 resumes an accepted P2 snapshot; P5 resumes P4; each continuation uses a new private runtime. | Keep historical readers/legacy behavior, but never select this path in the new mode. |
| `bookpipe/codex_parent.py` | Parent snapshot compatibility and fork strategy. | No runtime dependency in the new mode; retain historical evidence compatibility. |
| `bookpipe/codex_cache_shared_v2.py` | Separates source, translation-common, draft and active-pass messages, but source is still inserted for fresh roots. | Reuse proven transformations; split source preparation from source-free stage messages in a new versioned codec. |
| `bookpipe/codex_cache_shared.py` | Source-only compact encoding and P1-P5 output envelope; source maps are distinct from historical/retry data. | Preserve lossless mapping and strict decoders; extend in a new version, not by relabeling old evidence. |
| `prompts/pass1.txt` through `pass5.txt` | Each defines one bounded task and currently describes complete source-bearing input. | Update source-binding wording without weakening any editorial, identity, evidence or stopping rule. Add P0. |
| `bookpipe/store.py` | Verified result hashes, jobs, P1 merge records, receipts, selected translation passes and invalidation. Several policies distinguish `pass1/` from everything else. | Add explicit stage classification and session receipts; P0 is a source prerequisite, not downstream translation. |
| `bookpipe/project_config.py` | Inheritance requires exactly P1-P5 settings and copies required pass prompt files. | Version and adapt configuration/inheritance to P0 without changing old workspaces on read. |
| `bookpipe/application/workflow.py` | Read-only workflow projections and five-pass presentation assumptions. | Remain read-only and preserve existing public stages; tolerate hidden P0 state. |
| `bookpipe/runtime/worker.py` | Web jobs call the same application analyze/translate services. | Ensure those calls trigger P0 internally; preserve cancellation, reload and safe failure reporting. |

Additional integration points include `contracts.py`, `profiles.py`, `provider_registry.py`, `processing.py`, `application/projects.py`, `application/sessions.py`, `application/checkpoints.py`, `application/analysis_reset.py`, `evidence.py`, `usage.py`, `operations.py`, `state_files.py`, `cli.py`, runtime progress/protocol handling and workspace creation/inheritance. Inspect their actual call sites before editing; this list is a change map, not permission for unrelated refactoring.

### 3.3 Concrete hazards found in the baseline

- `Store.has_dependent_p1_work()` uses `jobs WHERE key NOT LIKE 'pass1/%'`. Adding P0 jobs without changing this policy falsely makes source preload count as translation-dependent work and can block P1 reset.
- `Runner.run` assumes every stage has `SOURCE_BLOCKS`, a P1-P5 schema, pass settings and ordinary model output. Do not insert stage 0 into these branches blindly.
- `Runner._semantic_execution_signature` currently removes legacy wire/strategy options. The new source-binding contract version must remain part of compatibility; do not accidentally recover an incompatible transcript merely because execution options were stripped.
- `_RpcSession` stores terminal messages, usage and IDs in mutable per-session state. Reusing it without explicit per-turn reset and thread/turn correlation can attribute late P0/P1 events to a later pass.
- `execute_target_pass` loads prerequisite passes with `allow_generate=False`. Those reads must never create a P0 or spend tokens.
- A chapter may contain multiple P1 analysis units and multiple translation chunks with different boundaries. A chapter ID alone is not proof that two calls have identical source.

## 4. Preserve the real dependency graph

Use an explicit stage definition/registry. Stage-specific validators and input builders are expected; stage-specific thread managers are not.

| Stage | Source binding | Dynamic semantic input | Canonical result / effect |
|---|---|---|---|
| P0 | Complete immutable source scope and stable local source map | Minimal readiness task only | Validated readiness receipt and durable native baseline |
| P1 | Its analysis unit's source scope | `SECTION_ID`, frozen `EXISTING_MEMORY` from previously accepted/merged P1 units | Terms and observations; idempotent merge and ordered analysis receipt |
| P2 | Its translation chunk's source scope | Approved lexicon, scoped observations, previous context, `CHUNK_ID`, sentence references | Semantic audit |
| P3 | Same scope as P2 | Same current common context, accepted P2 as `SEMANTIC_AUDIT` | Polish draft |
| P4 | Same scope as P2 | Current common context, sentence references, accepted P2 and P3 | Correction ledger |
| P5 | Same scope as P2 | Current common context, accepted P3 and P4 | Final Polish translation |

P1 unit 3 receives the appropriate accumulated P1 memory after units 1 and 2, not translation passes P1 and P2. Do not concatenate all raw previous answers or all previous chapter source. Retain the current bounded `Store.analysis_memory` behavior and immutable `analysis_inputs/<unit>.json` semantics, with explicit predecessor/revision provenance.

All required P1 units still finish before human review/approval enables translation. `translate` must not silently run analysis or approve terminology. For the normal full workflow, create P0 lazily immediately before each source scope's first actual inference. Do not preload every chapter at book import and let cache age before first use.

Existing `previous_context` continuity is an intentional cross-chunk dependency in translation. Keep its selection, bounded text and invalidation rules. The current source must not be confused with that previous-source excerpt. P5 does not acquire a new direct P2 dependency simply because older history once contained it.

## 5. Source scopes: chapter-first without changing boundaries

### 5.1 Immutable source identity

Introduce a versioned `SourceScope` independent of pass number and mutable memory. It includes project/source revision, chapter identity, ordered canonical source blocks, source-local ID maps and a digest of exact serialized model-visible source bytes. Include kinds, scene boundaries and identifiers required to decode results. Do not normalize away whitespace, emphasis or punctuation.

Use a stable source-only identity, not `SECTION_ID`, `CHUNK_ID`, profile display name, retry number, a timestamp, approved terms or translation output. Persist a `task -> source_scope` mapping so the relationship is inspectable and deterministic after restart.

If one P1 unit and one translation chunk cover identical canonical source, they share one P0 scope even though their task IDs differ. This is the ordinary complete-chapter case. Local block-map equality must also be proven; matching prose hashes alone is insufficient.

### 5.2 Split chapters

Preserve the existing `analysis_plan.json`, imported chunk boundaries and canonical IDs. When an analysis unit and a translation chunk cover different source, give them distinct source scopes under the same chapter. Reuse a scope only when its complete source and mappings are identical.

This intentionally means that a split chapter can have more than one P0. It does not mean P2-P5 each reload their source: all four passes for the same translation chunk share its scope. Do not silently load an entire larger chapter for a smaller P1 unit, exposing additional text or exceeding its context budget. Do not silently repartition a book to make sessions line up.

A later explicit re-planning feature is outside this change. Fail with a precise preflight error when a required unchanged source scope cannot fit its chosen model.

### 5.3 Sentences and compact maps

The source text appears once in P0. P2/P4 still need exact sentence identity and span validation. Reuse the existing lossless compact sentence-reference machinery where possible: source-local block references and exact offsets/ranges, plus a deterministic sentence-ID map. Do not resend a second textual copy of all source sentences.

Persist source maps separately from dynamic historical-term, lexicon and retry maps. Changing approved terms must not renumber source blocks or invalidate P0. Verify compact-to-canonical round trips for split blocks, Unicode, scene markers, repeated identical sentences and quotation spans. An unrepresentable sentence reference must fail locally, not cause an implicit full-source fallback.

## 6. Generic architecture and ownership

Suggested responsibilities, with names adjustable to existing conventions:

- `StageDefinition`: semantic input builder, dependency resolver, prompt selector, canonical schema, validator and acceptance hook.
- `SourceScopeResolver`: deterministic source packages and task bindings; no provider process or network calls.
- `PersistentSourceSessionManager`: durable slot selection, exclusive ownership, P0 readiness, resume, active-history verification and revert/recovery.
- `CodexSessionTransport`: version-checked RPC and per-turn event correlation; no book dependency policy.
- `ArtifactRepository` or equivalent existing storage abstraction: versioned path resolution and old/new attempt enumeration.
- `Runner`: preserve checkpoint-first execution, semantic evidence, strict decode/validation, bounded retries and canonical acceptance; invoke the manager only when a real Codex attempt is necessary.

The application boundary stays intact. CLI and HTTP adapters must not create homes, revert threads, merge P1 state or interpret JSONL. `OperationScope` continues to own the project writer lock and provider resources. New source-session locks add protection; they do not replace existing workspace locking.

Conceptual execution:

```text
resolve canonical task and dependencies
check selected/current result -> return without provider work when valid
recover compatible completed evidence -> accept without inference when possible
reject allow_generate=False when no valid result exists
freeze semantic inputs and retry additions
resolve source scope and effective provider/configuration
acquire session ownership
reconcile unfinished session/attempt state
ensure accepted P0; cold-resume the same native thread when needed
verify active history is exactly the P0 baseline
write the physical attempt and submission intent durably
execute only the requested stage using a source-free task message
persist terminal output, native evidence, usage and validation
accept canonical result and required domain receipts idempotently
revert to P0 and verify the surviving baseline
release/close resources without deleting the durable runtime
```

P0 uses the same evidence and durability infrastructure but a readiness validator, not the P1-P5 content validators. It is not a translation result or a terminology merge.

## 7. Durable layout and compatibility identities

### 7.1 Chapter-first artifacts

New-mode artifacts should use a chapter-first layout while retaining existing logical keys such as `pass1/ch0001_a001` and `pass3/ch0001_c0001`:

```text
PROJECT/
  source_sessions.json                     # versioned task/scope inventory; no secrets
  artifacts/
    ch0001/
      sources/SCOPE_ID/
        source.json                       # immutable source package
        source-map.json
        pass0/P0_FINGERPRINT/
          result.json                     # readiness receipt
          attempt_001/...                  # ordinary attempt evidence
        sessions/SLOT_ID/
          manifest.json                   # references and reconciliation state
          transitions.jsonl               # recoverable control journal
        pass1/ANALYSIS_UNIT/TASK_FP/attempt_001/...
        pass2/CHUNK_ID/TASK_FP/attempt_001/...
        pass3/CHUNK_ID/TASK_FP/attempt_001/...
        pass4/CHUNK_ID/TASK_FP/attempt_001/...
        pass5/CHUNK_ID/TASK_FP/attempt_001/...
```

The precise index representation can use the existing Store rather than duplicate authority in JSON. If a JSON inventory is exported, define it as a projection of one authoritative record. Do not maintain two independently writable session registries.

Preserve all historical pass-first paths. Do not mass-move existing artifacts, rewrite their hashes or create ambiguous symlink aliases. Centralize path creation and discovery so `Runner`, usage, recovery, checkpoint inventories, reset/archive and evidence readers support both layouts. Do not infer pass number from one character or fixed directory depth.

### 7.2 Private runtime

Keep live credentials and native state outside publicly served artifact paths and outside the source/repository working directory. Reuse `runtime_root` with a stable layout, for example:

```text
RUNTIME_ROOT/PROJECT_UUID/ch0001/SCOPE_ID/SLOT_ID/
  home/                    # CODEX_HOME and private authorized auth
  sqlite/                  # CODEX_SQLITE_HOME; preserve native DB and WAL state
  work/                    # empty, isolated, stable cwd
  owner.lock
```

The current default under the user's XDG state directory may remain, but the timestamp/PID attempt suffix must not define persistent identity. Persist the binding between project, scope, slot and runtime path. All runtime directories are private; auth files remain mode 0600 and directories mode 0700.

Closing a transport, ending a worker, pausing, reloading or hitting a validation error must never `rmtree` this runtime. Keep ephemeral model discovery separate. Distinguish `close_process`, `release_session`, `archive_session` and explicit destructive purge. No automatic cleanup of active or recoverable homes.

### 7.3 Fingerprints

Maintain separate identities:

- **Source fingerprint:** exact source package and source-map contract version.
- **P0/session compatibility fingerprint:** source fingerprint, generic base/session contract, fixed transport schema, relevant native protocol/storage compatibility, configured model/provider/auth identity and stable sandbox/workspace configuration.
- **Semantic task fingerprint:** active stage prompt, canonical inputs/dependencies, canonical schema and source binding version; retain current selected-output and rerun rules.
- **Physical attempt identity:** actual model/effort, request, native thread/session/turn, executable version and retry number.

Never hash credential bytes into published fingerprints. Store only a non-secret credential-source/account binding or configuration identity with explicit rotation rules.

A P1-P5 instruction edit changes its task fingerprint, not the source itself. A review/draft/audit change invalidates its real dependent tasks, not P0. Changing source or the generic P0/schema contract creates a new session generation; retain old canonical evidence. Cache age is not an identity component.

A different Codex model/provider configuration may need a separate compatible slot and its own P0. Do not promise cross-model computational cache reuse or silently override the user's selected model. Same-model per-turn effort changes may reuse a slot only when verified supported; preserve `codex_effort.py` selection verification and record the actual applied effort. Unsupported configurations fail before inference rather than downgrading silently.

For one unchanged source scope with compatible same-model settings, the acceptance target is exactly one successful P0 shared by P1-P5. For mixed providers, local/OpenAI transports retain their current stateless full-input behavior; a durable Codex thread cannot be transferred to another provider.

## 8. P0 and prompt/codec redesign

### 8.1 P0 behavior

Create `prompts/pass0.txt` and a minimal generic source-session contract. P0 must load the exact source package, produce only the bounded readiness acknowledgement, and finish. It must not summarize, extract terms, decide translations, invoke tools or begin P1.

Use an ordinary persisted `turn/start` to create an explicit P0 turn boundary. Prefer sending the source package in that P0 input, rather than adding unanchored raw history before it. Record and validate the resulting native turn identity. If adapting a tested injection-based fixture, prove the source is included in the durable retained baseline and that all later injections are removed by revert; do not assume this.

P0 readiness means successful terminal completion, valid acknowledgement, source/map binding verification and proven durable native history. A local source file, successful `thread/start`, an RPC response without completion, or an `OK` string alone is not readiness.

### 8.2 Stable transport schema

Introduce a new wire-format name, for example `source-session-v1`. Do not repurpose `cache-shared-v2` or change historical decoders in place.

Use one fixed transport output schema across P0-P5 to avoid stage-specific schema changes in the reusable prefix. A minimal extension of the existing compact envelope can admit `p=0` while retaining `a/o/c/i/e/t`. The following all-empty object is the structured READY acknowledgement:

```json
{"p":0,"a":[],"o":[],"c":[],"i":[],"e":[],"t":[]}
```

Only that valid stage-0 shape may mark P0 ready. P1-P5 retain their current active arrays and strict empty-inactive-array validation. Keep dynamic source-ID coverage constraints in the application validator and source maps, not in a newly changing per-turn transport schema. Add version-specific `encode_output` and `decode_output` tests.

The generic base/session instructions define trust boundaries, source reuse, task envelope interpretation and the output framing. They must not embed book-specific memory or the full P1-P5 editorial prompts. The P0 task itself remains source plus readiness.

### 8.3 Active-stage messages

Build one source-free application task envelope for P1-P5 containing the active stage, source binding, active instructions, required context/dependencies and bounded retry feedback. Example, schematic rather than a complete wire payload:

```json
{
  "SOURCE_SESSION_V1": 1,
  "SOURCE_REF": {"scope_id": "scope-id", "source_sha256": "source-hash"},
  "TASK_INSTRUCTIONS": "Active stage instructions and compact output rules",
  "TASK_DATA": {
    "CHUNK_ID": "ch0001_c0001",
    "APPROVED_LEXICON": [],
    "OBSERVATIONS": [],
    "PREVIOUS_CONTEXT": {},
    "SEMANTIC_AUDIT": {}
  },
  "ACTIVE_PASS": 3
}
```

The static trusted contract delegates stage selection only to the application-created outer envelope, never to text inside source, memory, quotations or previous output. Do not change `thread/start`/`thread/resume` base or developer instructions per pass. Put stage-specific instructions in the active task under this contract so revert removes them too. Do not add persistent developer messages between turns unless their removal semantics are verified.

`SOURCE_REF` identifies data already in the same thread; it is not a request to read a file or invoke a tool. All decoders continue to validate against the complete canonical semantic source retained locally.

### 8.4 Required changes to every prompt

| Prompt | Required source/session wording | Rules that must remain |
|---|---|---|
| P0 | Read the bound source; return only structured READY. | No analysis, translation, review or tool use. |
| P1 | Analyze the bound P0 source; current memory comes from this task only. | Entity separation, evidence-tied gender, real aliases, bounded candidates, new deltas, no prose translation. |
| P2 | Audit only the current scope's referenced sentences. | One check per sentence; exact quotes; no full translation or future revelations. |
| P3 | Translate each bound source block using current accepted P2 advice. | Exact block coverage/order, source authority, approved names, no recursive audit. |
| P4 | Independently compare bound source against explicitly supplied accepted P3; P2 is advice. | Do not trust draft/audit merely because plausible; finite correction ledger, exact spans. |
| P5 | Edit the supplied P3 draft using supplied P4, checking against bound source. | P3 remains the draft; no direct implicit P2 input; no new global audit or terminology decisions. |

Avoid saying simply "use the previous answer": after a revert the previous surviving answer is P0 READY, not the upstream pass. Update custom-prompt handling explicitly; never silently discard a user's prompt. Preserve canonical prompt semantics for legacy/stateless providers through a delivery binding or versioned prompt set. Do not leave local models with prompts that refer to inaccessible Codex history.

### 8.5 Serialization guarantees

Implement and test separate source and dynamic serializers. The active stage request must not contain `SOURCE_BLOCKS` text, the full current `SOURCE` message, or textual duplicates of `SOURCE_SENTENCES`. A P2 quote inside its accepted audit or a P4 correction span is legitimate dependency data, not a second source preload. Do not use a simplistic substring prohibition that rejects those artifacts.

Prove round-trip semantic equality: resolving P0 plus the active dynamic input must reconstruct the same canonical input consumed by the current validators. This applies to every pass and validation retry, including repaired upstream outputs and custom prompts.

## 9. Native protocol and exact revert boundary

Follow `AGENTS.md`: consult official developer documentation, generate schemas from the installed Codex executable and inspect the matching Codex source version for details not covered publicly. Do not substitute guesses from old examples.

The inspected Codex `rust-v0.160.0` type defines:

```json
{"method":"thread/revert","params":{"threadId":"THREAD_ID","beforeTurnId":"FIRST_POST_P0_TURN_ID"}}
```

`beforeTurnId` is an exclusive boundary: the named turn and every later turn are removed from the replacement active history. **Never pass the successful P0 turn ID when restoring P0.** Find the first surviving post-P0 turn in ordered active history. If none exists, cleanup is already complete. Re-derive this boundary after a crash; do not reuse a stale turn ID blindly.

The operation is for supported paginated threads. Detect capability and storage compatibility before spending model tokens. Do not substitute legacy `thread/rollback`, infer a `numTurns` parameter, or fall back to `fork` when `revert` is unsupported.

Required protocol capabilities include persistent `thread/start`, same-home `thread/resume`, supported active-history inspection with complete pagination, `turn/start`, terminal/usage events, `thread/revert`, and safe interruption where used. Generate experimental schemas when required by the installed build. Pin the tested executable version in evidence; a numerical minimum version alone is not a capability test.

Cold resume must retain the durable `CODEX_HOME`, `CODEX_SQLITE_HOME`, stable cwd, thread ID and native session binding. Use only fields accepted by that executable's resume schema; start-only fields cannot be blindly copied into resume params. Do not rely only on a JSONL export when native paginated storage requires its own database/index.

Before removing a suffix, preserve its recoverable evidence and reconcile every turn with application-owned submission records. Unknown or foreign turns require a recovery hold rather than blind deletion.

After revert, inspect all active turns and verify the expected P0 source/acknowledgement survives and no stage-specific task/answer remains. The native live file may change after revert; compare the logical retained baseline, not a whole-file checksum that includes evolving metadata. Immutable evidence exports have their own ordinary integrity hashes.

## 10. Recovery state machine and durable ordering

### 10.1 Persist sufficient state

Persist, through one authoritative session record and append-only evidence:

- format version, project UUID, chapter/scope identity and source/map hashes;
- slot generation, protocol/executable identity, model/configuration and instruction/schema hashes;
- private runtime binding and native thread/session/P0 turn IDs;
- P0 accepted attempt, source evidence reference and logical baseline digest;
- active task key/fingerprint, attempt ID, dependency hashes and submission-intent ID;
- current turn ID when known, terminal/validation/acceptance state;
- cleanup-required flag, selected revert boundary and last verified baseline state.

Suggested working states are `baseline_ready`, `submission_pending`, `running`, `terminal_uncommitted`, `accepted_cleanup_pending`, `reverting` and `recovery_required`. P0 independently has `absent`, `preparing`, `ready` or `invalid`. Implement explicit transitions rather than treating any existing home as successful.

A state transition must survive a process crash. Use the repository's durable state abstraction and atomic files; where power-loss durability is claimed, flush/fsync files and containing directories appropriately. Never copy only a live SQLite main file while ignoring WAL or native store semantics.

### 10.2 Acceptance before cleanup

Required order for completed valid output:

1. Durably save terminal response, correlation IDs, usage and native evidence sufficient for recovery.
2. Decode, repair conservatively and validate against the canonical task; retain raw and repaired forms.
3. Durably save the accepted canonical result and its receipt/selection. Reconcile P1 merge and analysis receipt idempotently using existing merge identity; no downstream P1 unit starts before that integration is complete.
4. Mark session cleanup pending.
5. Revert to P0, verify active history and mark baseline ready.

If storage/evidence recording fails, stop before destructive cleanup. Never destroy the only recoverable terminal output and then discover that `result.json` could not be written.

An accepted canonical result remains accepted when revert fails. Block a new inference on that dirty session until cleanup is reconciled. Do not regenerate the accepted pass to repair session state. Domain finalization and cleanup must both be replay-safe even when they cross separate existing Store transactions.

### 10.3 Crash matrix

| Interruption point | Required resume behavior |
|---|---|
| Before any P0 turn submission | Continue preparation; no false usage or readiness. |
| P0 request sent, acknowledgement unknown | Inspect the same native thread; recover completion when provable. Do not blindly submit a duplicate. |
| P0 completed, local readiness receipt missing | Validate retained terminal evidence/source binding and reconstruct the receipt without inference. |
| P0 ready, process stopped | Cold-resume same home/thread; no new P0 or source injection. |
| Pn submitted, response/turn ID uncertain | Reconcile ordered native history against the recorded intent/input; if ambiguous, stop rather than guess or resubmit. |
| Pn still active under a live owner | Reattach/reconcile through supported ownership, or report busy. No concurrent writer or duplicate turn. |
| Pn ended with only partial output | Preserve partial evidence; do not mark complete. After proving no live turn remains, revert and retry only this incomplete stage under the normal bounded policy. |
| Pn completed, application died before validation | Recover the exact completed answer; validate and accept without a new model call. |
| Pn result saved, P1 merge/selection incomplete | Reconcile domain acceptance exactly once, then cleanup. |
| Pn accepted, revert not sent | Revert only; never regenerate Pn. |
| Revert sent, acknowledgement lost | Inspect active history. If already at P0, commit local cleanup; otherwise reconcile before retrying revert. |
| Revert completed, local clean marker missing | Verify baseline and repair marker; no inference. |
| Native state missing/corrupt or source binding mismatched | Preserve evidence; fail closed with actionable diagnostics. An explicit rebuild creates a new P0 generation, never silently changes the old identity. |

Native inference is not assumed to have a client idempotency key unless the installed protocol actually supports one. Use local intents and history reconciliation; do not invent RPC fields. Promise exactly-once canonical acceptance, not impossible exactly-once remote execution after every network/process failure.

A hard-killed generation may have to restart the incomplete pass. Persistence guarantees that accepted earlier passes and P0 are not lost; it does not guarantee continuation at the exact partially generated token.

### 10.4 Validation retries

Failed completed output is recorded as failed, not acknowledged as an upstream artifact. Before a bounded validation retry, return to P0 and supply the same canonical dependencies plus targeted retry feedback. Preserve physical attempt numbering across restarts. Never append an invalid answer indefinitely or retry solely for missing cache. Existing completed compatible attempts remain recoverable before any new inference.

## 11. Ownership, interruption and safety

Hold an exclusive session lease/lock across reconciliation, submission, acceptance and cleanup. The runtime is project- and scope-specific. Two workspaces with identical text must not share native mutable state. Distinct source scopes can have independent sessions, but this PRD does not introduce new intra-book parallel scheduling.

Closing a worker must stop or intentionally hand off its owned process group while retaining native state. Track enough owner identity to avoid PID-reuse mistakes and detect orphaned app-server processes; never kill another workspace's process. A lost local lock is not proof that no provider turn remains active.

Honor stop/pause/reload at the existing durable boundaries. Once stop is requested, finishing P0 must not automatically start P1, and finishing a pass must not start another pass or another chapter. Cleanup may finish locally/RPC-only after acceptance when safe. If interrupted during cleanup, leave a recoverable marker. Do not start extra model turns to clean a session.

Preserve the current no-tools/thin-inference isolation: private auth source only, no inherited user config, skills, memories, plugins, project instructions, tools, roots or shell access. Recheck isolation on resumed runtimes and after runtime upgrades. Do not replace refreshed private auth on every turn with an older source token; authentication refresh/bootstrap must be lock-protected and deliberate.

Native source and model output are sensitive book content. Do not expose homes, credentials, private absolute paths, raw prompts or native logs through workflow progress or generic web artifact endpoints. Add explicit private-path exclusion tests.

## 12. Context budgeting and usage

A short active task is not the whole model context. Preflight must account for the complete retained P0 source, generic instructions, fixed output schema, necessary native wrappers, current dynamic dependencies, planned output reserve and safety margin. The existing Codex UTF-8 bound remains labeled as bytes/upper-bound, not exact tokenizer counts. Do not count only the newly submitted suffix.

Budget a scope for its known consumers before P0 where practical. Re-evaluate full effective context when memory, draft size or profile changes. No truncation, source summarization, automatic compaction, unrequested source splitting, or silent model substitution. If automatic native compaction changes the source baseline, treat the slot as unsafe and stop before another pass.

P0 is a real physical model call with its own attempt and usage. Reusing it, reading history, resuming or reverting must not fabricate model tokens. Record per-turn usage using thread/turn correlation; do not charge a thread's cumulative lifetime usage again on every pass. Retain the existing rule that missing usage is unknown, not zero.

Record at least:

- source scope, slot/generation, P0 attempt, thread/session/turn, parent consumer stage;
- actual source message bytes added this attempt (zero for normal P1-P5), retained source bytes and dynamic suffix bytes;
- local logical prefix/source hashes, not claims of observing undocumented provider internals;
- reported input/cached/cache-write/output/reasoning tokens and their availability;
- preparation, resume, inference, validation, acceptance and cleanup durations;
- recovery/reuse/revert outcomes, unexpected turns and compaction detection.

Physical totals include P0 and all submitted failed attempts. Keep source-preload usage distinguishable from P1 analysis usage, including when P1 and a translation chunk share the same scope. Update usage enumeration for both artifact layouts. Never estimate subscription charges as actual invoices.

## 13. Web-start compatibility without frontend work

Existing analyze/translate jobs continue to enter through `PipelineService`. P0 preparation occurs inside the common runner/session boundary for a real missing Codex pass. This covers a web-started analysis unit, full translation, targeted P2-P5 execution and resumption after reload.

Keep public workflow stages such as `analysis`, `review`, `translation`, `publication` and `complete`. A P0 preparation may leave the owning operation visibly running under its current stage. Add structured internal events such as `source_preload_started`, `source_preload_completed`, `source_session_resumed` and `source_session_cleanup_required`; make existing consumers tolerate them. Do not represent P0's billable usage as P1 merely to fit old UI fields.

Do not add P0 to frontend arrays, edit frontend styles, or require a new P0 button. Backend diagnostic DTOs can add optional source-session data if compatibility is demonstrated. Check integer-zero handling: `pass_no=0` must not disappear through truthiness or be interpreted as missing.

Read-only endpoints, status, workflow snapshots, usage, profile discovery and doctor must not create P0 or start inference. A target-pass request with missing semantic prerequisites must fail before source preparation. `allow_generate=False` and already accepted checkpoints remain strictly no-generation paths.

## 14. Versioning, existing books and other providers

Introduce an explicit versioned execution mode, for example:

```json
{
  "pipeline_execution": {
    "version": 1,
    "codex_mode": "source-session-v1",
    "source_scope_policy": "exact-existing-unit",
    "revert_policy": "after-each-attempt"
  }
}
```

This is a proposed new configuration contract, not a command that works on the reviewed baseline. New workspaces created by the implementation branch should select the new mode automatically. Existing projects without the mode remain legacy until explicitly opted in; read-only queries must never migrate them.

Do not combine the new mode with legacy `translation_thread_strategy` routing. The resolved new execution plan must report the source-session strategy unambiguously. Preserve legacy readers/codecs needed to inspect and recover old artifacts. Do not globally remove old code merely to make the new test green.

P0 profile/configuration resolution must be deterministic and visible in diagnostics. It derives from the compatible consumer model; do not choose a separate cheaper model invisibly for source warming. Support an explicit P0 readiness output budget and prompt configuration without forcing all legacy five-pass settings to acquire a new key on read. Update inheritance and new-workspace defaults, including required prompt copies and format validation.

For an explicit opt-in of an existing workspace, preserve accepted P1 and human review. Create missing P0 only when an incomplete requested Codex stage needs it. Do not rerun P1 merely because an old successful analysis lacks P0, and do not reinterpret an old P2 parent as a valid source-only P0. Preserve old final translations until genuinely dependent work is replaced through current invalidation rules.

The first end-to-end user test is a new book in a new workspace. No automatic conversion of the running `salvation-03` project is authorized.

## 15. Reset, invalidation, archive and cleanup policies

Add explicit categories: source prerequisite, analysis, translation and publication. Replace broad assumptions such as "anything except P1 is downstream translation" where P0 would violate them.

- Clearing P1 removes/reset its current analysis state under existing guards but does not destroy valid source-only P0 sessions.
- Changing review decisions preserves P0 and invalidates translation tasks according to their actual lexicon/context dependencies.
- Replacing an accepted P2/P3/P4 output retains old evidence and invalidates its current downstream consumers as today.
- Source or source-map changes create a new scope/session generation; never reuse an incompatible existing home.
- Changing selected models does not invalidate otherwise accepted semantic outputs merely to warm cache.
- Source preloading alone does not pretend analysis membership has completed. Re-preparation/source changes must explicitly retire incompatible P0 bindings without treating P0 as a human terminology decision.
- Archive and reset inventory must understand both artifact layouts and private runtime references. Destructive session purge must be explicit, locked, refuse active owners and preserve ordinary translation results.

Retain project/source protections, revision checks, review digests and series continuation rules. Do not silently copy a predecessor volume's live sessions or auth into a new volume.

## 16. Ordered implementation plan

The steps below are one complete deliverable. Do not stop after the initial P0 step and report this PRD complete.

### Step 1 - Establish isolation and baseline

Verify the current branch/worktree and reviewed baseline. Inspect `AGENTS.md`, current diff and relevant tests. Use a new scratch workspace, separate runtime root and separate server ports/state from production. Record the installed Codex version and generated RPC schema support. Run the baseline offline regression suite before changing behavior when the environment supports it.

### Step 2 - Define typed contracts and stage graph

Add `SourceScope`, task bindings, session receipts, transition states and stage definitions. Preserve existing canonical P1-P5 result schemas. Add a readiness result and explicit stage classification. Write graph, fingerprint and no-generation tests first.

### Step 3 - Introduce path/index abstraction and persistence

Centralize old/new artifact enumeration and result paths. Add chapter-first writes only for the new mode. Implement authoritative session storage, atomic transitions, persistent runtime ownership and locks. Prove legacy usage/checkpoint/reset readers still work.

### Step 4 - Build source and active-task codecs

Reuse proven compact source/output transformations. Add versioned source-session wire format, stable P0-P5 output schema, immutable source maps and source-free dynamic task messages. Add all-pass semantic round-trip tests and prompt-injection/isolation fixtures before live inference.

### Step 5 - Implement the native session lifecycle

Separate per-attempt recorder/process resources from durable homes. Implement capability preflight, P0, cold resume, paginated history inspection, exact exclusive-boundary revert, post-revert proof and turn-correlated notifications. No fork or fresh-root fallback in the new mode.

### Step 6 - Integrate all execution paths

Make `Runner` checkpoint-first and recovery-first before `ensure_p0`. Use the same mechanism for P1 and P2-P5. Preserve P1 snapshot/merge ordering and review; centralize full-run and target-pass dependency construction. Cover CLI, programmatic services and web worker start through these same use cases.

### Step 7 - Make acceptance and interruption crash-safe

Implement the crash matrix, outcome-first cleanup, orphan ownership checks, interrupted-turn recovery and bounded validation retries. Add fault injection after each persisted transition and before/after every relevant RPC. Do not remove old recovery behavior without replacement tests.

### Step 8 - Complete configuration, prompts and operations

Add P0 prompt/defaults and source-binding wording for all five prompts; retain stateless/legacy compatibility. Update new-workspace creation, inheritance, profile resolution, doctor, usage, reset/invalidation, archive and read-only projections. Keep web frontend untouched.

### Step 9 - Run regression and explicit live verification

Run the offline suite, fake-app-server integration tests and a bounded live lifecycle test in a fresh scratch project. The normal test suite must never spend tokens. Record pass/fail separately for correctness, persistence and cache performance.

### Step 10 - Deliver the full implementation

Commit only on the dedicated branch. Update maintained architecture/README after implementation and write an English validation report with exact commands, revisions, test results, real usage and remaining failures. Include the command for starting a new test workspace. Do not merge or deploy to production automatically.

## 17. Acceptance tests

### 17.1 Deterministic offline and fake-server tests

All of the following are required:

1. Same complete canonical source for P1/P2 resolves one source scope despite different task IDs; unequal/split scopes never share incorrectly.
2. Source changes, block-map changes and generic contract/schema changes retire incompatible sessions; review changes do not.
3. P0 contains no P1 memory, review, common translation context, draft or stage editorial instructions.
4. P0 terminal ACK without durable source/history proof is not accepted; partial/invalid ACK stays incomplete.
5. Source package plus each stage task reconstructs the exact canonical semantic input.
6. P1-P5 outbound task messages and validation retries add zero complete current-source messages; legitimate dependency quotes remain allowed.
7. All P0-P5 requests use the same versioned transport schema and unchanged generic base/developer instructions.
8. P1 preserves entity/gender/alias rules; P2/P4 preserve sentence coverage and quote validation; P3/P5 preserve nonempty ordered block coverage.
9. P1 memory flows only from accepted, merged predecessor analysis units; retries/restarts never merge a delta twice.
10. P3 uses accepted P2; P4 uses accepted P2/P3; P5 uses accepted P3/P4. Repaired canonical output, not raw history, is passed downstream.
11. Every accepted P1-P5 result leaves active history at P0 after cleanup; P4 cannot see an unapproved P3 self-audit or any unprovided prior answer.
12. The new mode never calls `thread/fork`, legacy paired resume, or unrequested fresh-root fallback.
13. `beforeTurnId` targets the first post-P0 turn. A test fails if the successful P0 turn is supplied.
14. Revert with multiple recorded application-owned later turns removes the whole suffix; unknown turns cause a recovery hold, and complete paginated inspection prevents a false-clean result.
15. Cold resume preserves home, SQLite state, cwd and thread identity; no source reload occurs.
16. All crash-matrix points are exercised, including lost `turn/start` and lost revert acknowledgements.
17. Completed output is recovered before a new inference; an accepted result followed by cleanup failure is not generated twice.
18. Storage failure before evidence/result acceptance prevents destructive revert.
19. Known interrupted output is not accepted as final; ambiguous/live submissions are not duplicated.
20. Per-turn buffers reset correctly; late old-thread/old-turn terminal and usage events cannot finish or charge a newer task.
21. Concurrent attempts for one slot serialize/refuse cleanly; two workspaces never share homes even with identical source.
22. Pause/stop/reload after P0 or Pn does not start a later pass or unit; cleanup can be resumed independently.
23. Full retained-context preflight rejects overflow before submission; short suffix alone cannot make an oversized session appear valid.
24. Compaction or source-history corruption fails closed; cache expiration/zero cached tokens does not.
25. Missing runtime state produces an actionable recovery error, not hidden regeneration or a synthetic old thread identity.
26. Existing successful checkpoints, `allow_generate=False`, workflow/status/usage/doctor reads and prerequisite errors never spend P0 tokens.
27. Existing web-started analyze, translate and target-pass jobs automatically prepare missing P0 without any frontend changes.
28. Hidden P0 events, `pass_no=0`, usage aggregation and worker reload remain compatible with existing projections.
29. Old pass-first and new chapter-first attempts are enumerated exactly once; reader/export/publication still resolve verified final artifacts.
30. P0 does not falsely block P1 reset via `NOT LIKE 'pass1/%'`; clearing P1/review edits preserve valid source sessions.
31. Explicit opt-in of a legacy fixture preserves accepted P1/review/finals and prepares only missing needed P0; no read-time migration.
32. Local/vLLM/OpenAI fixtures retain complete standalone source semantics and unchanged selected profiles.
33. New-workspace defaults, copied prompts and series configuration inheritance work without inheriting native sessions/auth.
34. Private homes/auth/native files are excluded from artifacts served to the web and from Git commits.
35. Every submitted attempt, including P0/failed attempts, has truthful usage; no cumulative double counting or unknown-as-zero conversion.
36. Unrequested production paths, locks, settings, artifacts, processes and service ports remain untouched.

Use existing suites such as `test_pipeline.py`, `test_application.py`, `test_transports.py`, `test_codex_cache_shared_v2_app_server.py`, `test_codex_pair.py`, `test_codex_effort.py`, `test_revision_workflow.py`, `test_runtime.py`, `test_server_api.py`, `test_profile_wire_defaults.py`, migration tests and usage tests as regression anchors. Add focused source-session tests; do not rewrite legacy assertions to conceal lost compatibility.

### 17.2 Live verification in a new scratch workspace

Use an explicit opt-in live test with a small multi-chapter fixture and authorized Codex profile. Include an additional chapter split differently for analysis/translation. Do not run it automatically in CI or on the currently translated production book.

Verify actual P0/P1, ordered P1 memory, the review stop, deliberate review approval, and P2-P5 outputs through the production runner rather than only a toy ACK loop. Keep source scope/model unchanged for the primary one-P0 assertion. Then verify profile-switch behavior separately.

Force a full process restart after P0 and after an accepted intermediate pass. Verify cold resume with the same native binding, source-free later requests, no duplicate accepted generation, correct recovered artifacts and P0-only active history after every cleanup. Include one failed validation followed by a repaired retry and one interruption/recovery case.

Report measured tokens and timings per physical call, source/dynamic bytes, full context bounds, retained active turns, source/thread IDs, actual executable/model/effort and any cache misses. Do not require the earlier approximately 96% number as a deterministic correctness assertion. A clean, bounded, explicitly authorized warm follow-up can demonstrate source-prefix cache reuse; no unlimited paid retry loop is allowed. Label unmeasured or environment-blocked live cases honestly.

### 17.3 Existing regression commands

The reviewed CI uses:

```bash
uv lock --check
uv run python -m compileall -q bookpipe translate.py
uv run --group dev python -m pytest -q
node --test tests/*.cjs
npm --prefix web ci
(cd web && npx playwright install --with-deps --only-shell chromium)
npm --prefix web test
```

Run in the dedicated worktree with its own virtual environment. Frontend regression is a compatibility check, not permission to redesign the interface. Record unavailable environment prerequisites instead of claiming an unrun test passed.

## 18. Worktree and production isolation

The branch exists on GitHub. A worktree is local state and must be created on the user's machine; a remote branch alone does not create it there.

From the existing repository:

```bash
git fetch origin
git worktree add -b work/p0-p5-persistent-sessions \
  ../intelitex-p0-p5 \
  origin/work/p0-p5-persistent-sessions
cd ../intelitex-p0-p5
git status --short --branch
```

These commands do not switch or merge the original checkout. The command assumes that the new local branch and destination directory do not already exist; inspect `git worktree list`/local branches before resolving a name conflict, and never use a forced reset as a shortcut.

Use separate book project paths, runtime root, server/supervisor state, sockets, PID files and ports for testing. Worktrees isolate code, not arbitrary external translation data or services. Never repoint or restart the service currently serving `main` as part of this implementation.

## 19. Definition of done

The complete P0-P5 production path is implemented behind the explicit new execution mode; new test workspaces use it automatically. P0 is durable, source-only, resumable and reused. P1 and P2-P5 share one lifecycle while preserving their distinct dependency edges and review gate. Every completed pass is safely persisted before revert; uncertain/interrupted states recover without losing accepted work. All prompts and codecs support source-bound input, full-context preflight is correct, physical usage is truthful, and legacy read/recovery behavior remains available.

The existing web Start operations work without frontend changes. The required deterministic tests pass; live verification has an explicit evidence-backed status. Only the dedicated branch/worktree and fresh test data have changed. A P0-only implementation, a mock-only success, a new thread per pass, or a paired/fork fallback is not completion of this PRD.

## 20. Reference sources

Repository observations above refer to baseline `e4b622fc447c1df71028aa114cff6fdbd41d7204`, not unpinned future `main`. Relevant inspected files include:

- `AGENTS.md`, `README.md`, `docs/architecture.md`, `.github/workflows/ci.yml`.
- `bookpipe/application/pipeline.py`, `bookpipe/application/workflow.py`, `bookpipe/runtime/worker.py`.
- `bookpipe/engine.py`, `bookpipe/store.py`, `bookpipe/project_config.py`.
- `bookpipe/codex_transport.py`, `bookpipe/codex_pair.py`, `bookpipe/codex_cache_shared.py`, `bookpipe/codex_cache_shared_v2.py`.
- `prompts/pass1.txt`, `pass2.txt`, `pass3.txt`, `pass4.txt`, `pass5.txt`.
- `experiments/codex_session_cache_probe.py`, `experiments/codex_cache_v3_session.py`.

External primary references inspected for this design:

- OpenAI Codex App Server documentation: <https://developers.openai.com/codex/app-server/> (redirects to the maintained ChatGPT Learn documentation).
- Version-pinned exclusive revert contract: <https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server-protocol/schema/typescript/v2/ThreadRevertParams.ts>.
- OpenAI prompt caching guide: <https://developers.openai.com/api/docs/guides/prompt-caching>.

The implementation must regenerate schemas from the actually installed executable and verify relevant native behavior. Public documentation, version-specific implementation details, earlier reported experiments and new production test measurements are distinct evidence categories and must remain labeled as such.
