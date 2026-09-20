# Codex implementation prompt — series continuation memory

Work in the current Intelitex repository.

Implement the complete PRD at:

`docs/prd/series-continuation-memory.md`

Read the entire PRD before modifying code. Inspect the actual checkout, repository instructions, current tests and current architecture. Do not assume the repository exactly matches an earlier conversation.

This is a production feature, not an experiment.

## Primary objective

Add an optional continuation mode to the existing book import so a later volume starts with deterministic compact memory inherited from the immediately preceding Intelitex project.

The user-facing entry point should be the PRD's `import --previous-volume PREVIOUS_PROJECT` flow unless current CLI conventions make an equivalent spelling materially safer.

When the previous project is a legacy standalone book with no series metadata, retroactively mark it as **volume 1**. The new project becomes volume 2. If the predecessor is already series volume N, the new project becomes volume N+1 with the same series ID.

## Critical invariants

### Preserve the existing pipeline

A continuation is still a normal Intelitex project:

```text
import -> analyze -> review -> approve -> translate
```

Only its initial Store state differs.

Standalone imports without the new option must behave exactly as before.

### Do not rewrite the predecessor's frozen state

For the legacy predecessor:

- do not modify `book.json`;
- do not migrate or rewrite `state.sqlite3`;
- do not change prompts;
- do not change checkpoints or artifacts;
- do not change translation output.

The intended retrofit is a small atomic `series.json` sidecar only.

Add a regression test that proves predecessor `book.json` is byte-for-byte unchanged after linking.

### Series compaction is local and deterministic

Build the seed only from canonical structured predecessor artifacts described in the PRD.

No LLM/model call is permitted for compaction or tests.

Do not read old attempt payloads or translated prose to synthesize series knowledge.

### Keep P1 contracts unchanged

This is especially important because Codex P1 `compact-v1` is already production-tested.

Do not change:

- `prompts/pass1.txt`;
- Pass-1 JSON schema;
- canonical top-level `EXISTING_MEMORY` shape;
- `bookpipe/p1_compact.py` compact schema/field mappings;
- Codex transport behavior.

The continuation must reuse the existing `matched/catalogue/catalogue_incomplete` memory contract.

Expose compact inherited observation context to P1 through the existing matched term `meaning_notes` view as specified by the PRD, not by adding a new memory field.

Add a deterministic test that the first continuation P1 input still passes the existing compact-v1 encoder unchanged.

### Use the existing Store

Seed inherited approved terms and reusable observations into the new Store before Pass 1.

Prefer extra JSON fields inside the existing `terms.data`, fact payloads and `kv` metadata instead of a SQLite schema migration.

The new continuation should have a non-empty `book_memory.json` immediately after import when the predecessor had memory.

Do not set the new book's global approval flag. New terminology still requires the normal analyze/review/approve workflow.

### Human-approved terminology is authoritative

Carry the predecessor's approved Polish choice.

Inherited unchanged terms should start reviewed in the new book's review state.

New terms should behave exactly as today.

An inherited term should require review when current-volume P1 materially disagrees at terminology level, at minimum when:

- the current preferred/first Polish candidate differs from the inherited approved choice; or
- its category changes materially.

Alias-only/new-evidence extension should not force review when the inherited Polish choice remains preferred.

### Preserve spoiler gating

Previous-volume facts are safe from the first page of the next volume.

Current-volume facts are not.

Update translation memory so relevant inherited observations are available from the beginning of the new volume while current-volume observations keep their existing chapter/order gating.

## Expected implementation footprint

Inspect and prefer a small change set centered on:

- new `bookpipe/series.py` or an equivalently focused module;
- `bookpipe/cli.py` for the continuation import option and orchestration;
- `bookpipe/store.py` for seed insertion and inherited-memory behavior;
- tests;
- narrow README documentation.

The design should not require changes to provider transports, P1 compact codec, schemas, prompts, EPUB parsing or Reader.

If implementation begins to require those, stop and re-check the PRD before expanding scope.

## Predecessor handoff

Validate the predecessor before using it.

It must have valid frozen `book.json`, supported `book_memory.json`, and approved terminology consistent with `lexicon.approved.json`.

The predecessor's prose translation does not have to be complete.

Use the predecessor project lock while reading/writing the handoff state. Do not race an active Intelitex writer.

Do not persist absolute predecessor paths in series metadata or seed artifacts.

## Artifacts

Implement the PRD's two sidecars or semantically equivalent names if a strong repository convention requires it:

- `series.json` — small project/volume identity sidecar;
- `series.seed.json` — deterministic inherited snapshot used to seed this volume.

The seed must retain reusable semantics and approved terminology while dropping predecessor-local evidence coordinates, candidate history, checkpoints and prose.

Volume N+1 must be seedable from volume N alone because volume N's `book_memory.json` contains its inherited state plus its own discoveries.

Do not add global series/entity registries.

## Tests that must exist

Implement the full PRD test matrix. At minimum make sure there are explicit deterministic tests for:

1. standalone import regression;
2. legacy V1 -> V2 retrofit;
3. predecessor `book.json` unchanged;
4. V2 -> V3 automatic numbering;
5. deterministic seed/hash;
6. seed strips old evidence coordinates/candidate history;
7. approved lexicon consistency failures;
8. Store is already seeded before P1;
9. first P1 matched memory contains inherited entity/context;
10. existing compact-v1 encoder accepts continuation input unchanged;
11. recurring inherited alias merges into one term;
12. inherited unchanged review term is reviewed by default;
13. changed inherited preferred candidate/category requires review;
14. inherited observations reach relevant translation memory immediately;
15. current-volume spoiler gating remains unchanged;
16. cumulative V1 -> V2 -> V3 memory works without reopening V1;
17. malformed/conflicting series metadata fails closed.

Run the entire repository test suite after focused tests.

Do not make a live model call.

## Documentation

Update README narrowly with:

- continuation import syntax;
- automatic volume numbering;
- legacy volume-1 retrofit;
- no-LL deterministic compaction;
- requirement for predecessor approved terminology;
- predecessor translation completion is not required;
- inherited review behavior;
- standalone behavior remains unchanged.

Do not copy the entire PRD into README.

## Completion discipline

Do not stop after producing a plan. Implement the feature, tests and documentation.

Use synthetic temporary projects only. Do not touch any real translation project paths that may exist on the machine.

Finish with coherent local commit(s), unless repository instructions explicitly require another workflow.

Do not push or open a pull request unless explicitly requested in the implementation session.

In the final report to the owner, write in Polish and include:

- files changed;
- exact artifact formats chosen;
- compatibility strategy;
- proof the old volume's frozen state is untouched;
- P1 compact-v1 compatibility proof;
- review semantics;
- tests and counts;
- final commit SHA(s);
- deferred follow-ups.

