# PRD — Series continuation memory for multi-volume translations

## Status

Proposed production feature for Intelitex.

## Summary

Intelitex currently treats every imported book project as an independent translation. This is correct for standalone books, but it wastes already-reviewed knowledge when translating later volumes of the same series.

Add an **optional series-continuation path** to `import` so a new volume can start from a deterministic, compacted snapshot of the previous volume's approved terminology and reusable Pass-1 knowledge.

The existing standalone workflow must remain unchanged:

```text
import -> analyze -> review -> approve -> translate
```

For a continuation, the workflow becomes:

```text
previous volume project
        |
        | deterministic local compaction; NO LLM call
        v
series seed
        |
        v
import next volume -> seed Store -> existing analyze/review/approve/translate
```

The first time a legacy standalone project is used as a predecessor, Intelitex must also write series metadata into that existing project and mark it as **volume 1**. This must not modify its frozen `book.json`, SQLite translation state, checkpoints, prompts, or completed artifacts.

The feature should make the next volume start with prior knowledge rather than an empty `Store`, while preserving the current P1-P5 contracts and minimizing code changes.

---

## User problem

A translated novel may be only the first volume of a series. Later volumes reuse:

- characters and aliases;
- organizations;
- ships, places, technologies, ranks, statuses and jargon;
- human-approved Polish terminology;
- stable semantic facts such as entity identity, gender/coreference, technical constraints, register and continuity observations.

Today, importing volume 2 creates a fresh project with no knowledge of volume 1. The user must effectively rebuild the same terminology and entity context from scratch.

For a three-volume series, the desired behavior is:

```text
Volume 1
  normal Intelitex project
  |
  | first continuation is created
  v
retroactively marked as series volume 1
  |
  | compact approved/reusable knowledge
  v
Volume 2 starts with inherited memory
  |
  | after Volume 2 analysis/review/approval
  | compact the cumulative Volume 2 state
  v
Volume 3 starts with knowledge from Volumes 1-2
```

The user establishes order explicitly by pointing the new import at the immediately preceding project. Intelitex does not need to discover series automatically.

---

## Goals

1. Allow a new project to declare that it continues an existing Intelitex project.
2. When the predecessor has no series metadata, retroactively mark it as volume 1.
3. Automatically number the new project as predecessor volume + 1.
4. Create a deterministic compact seed from already-structured predecessor artifacts.
5. Seed the new project's Store before Pass 1 so P1 starts with prior terminology/entity memory.
6. Preserve prior human-approved Polish choices.
7. Make prior-volume reusable observations available to translation passes from the beginning of the new volume.
8. Preserve enough compact prior semantic context for P1 entity resolution without changing the P1 input contract.
9. Keep inherited unchanged terminology out of the user's normal review workload by marking it reviewed by default.
10. Require review when current-volume P1 materially disagrees with an inherited terminology choice/category.
11. Keep current-book spoiler protection intact: current-volume observations remain gated by source order, while previous-volume observations are safe from the start.
12. Preserve all existing standalone behavior when no continuation option is supplied.
13. Avoid any LLM call for series compaction.
14. Keep the current Codex P1 `compact-v1` wire format, prompts, schemas, fingerprints and transport code unchanged unless implementation inspection proves a tiny compatibility adjustment is unavoidable. The intended design requires no such change.

---

## Non-goals

This feature is deliberately not a general knowledge graph or series database.

Do **not** add in this task:

- automatic series detection from EPUB metadata;
- global series registries outside book projects;
- vector databases, embeddings or semantic retrieval services;
- LLM summarization during continuation initialization;
- cross-volume prose retrieval;
- automatic plot summaries;
- stable global `S000123` entity IDs;
- a new review application or new review workflow;
- changes to P2-P5 output schemas;
- changes to Pass-1 output schema;
- changes to `prompts/pass1.txt` or any project prompt;
- changes to the proven Codex P1 compact transport contract;
- migration of all existing projects merely because the feature exists;
- requirement that the predecessor's prose translation be 100% complete.

Stable cross-volume entity IDs may be added in a future feature if they become necessary. For this MVP, existing canonical source forms + aliases are sufficient and substantially reduce implementation risk.

---

## Current architecture constraints

The implementation must respect the current repository architecture.

### Frozen book manifest

`book.json` contains the imported source/chunk manifest. `plan_fingerprint()` intentionally fingerprints only frozen source structure. Existing projects reject accidental changes to that structure.

Series metadata must therefore be stored in a **sidecar**, not by rewriting the predecessor's `book.json`.

This also gives a strong backward-compatibility property:

> Linking an existing volume 1 into a series must leave its existing `book.json` byte-for-byte unchanged.

### Store and exported memory

Current Pass 1 merges terms and observations into SQLite and exports:

- `book_memory.json`;
- `terms.review.json`;
- after approval, `lexicon.approved.json`.

`Store.analysis_memory()` already gives Pass 1 a bounded `EXISTING_MEMORY` containing:

- directly matched terms with candidates, chosen form and meaning notes;
- a bounded catalogue of other source forms and aliases.

The series implementation should reuse this mechanism rather than creating a parallel P1 memory channel.

### Codex P1 compact transport

`bookpipe/p1_compact.py` requires the current exact shape of canonical `EXISTING_MEMORY`:

```text
catalogue
matched
catalogue_incomplete
```

Do not add new top-level fields to canonical `EXISTING_MEMORY` in this feature.

Prior-volume observations that are useful to P1 should be folded into the inherited term's existing `meaning_notes` view by `Store.analysis_memory()` or equivalent internal preparation. This preserves the canonical and compact wire contracts.

### Translation memory

`Store.translation_memory()` currently returns:

```text
APPROVED_LEXICON
OBSERVATIONS
```

This is the correct place to expose inherited prior-volume observations to P2-P5.

Current-volume observations must keep their current source-order/chapter gating. Only observations explicitly marked as inherited from an earlier volume bypass that current-volume gate.

---

## User-facing CLI

Extend only the existing `import` command.

Recommended syntax:

```bash
uv run translate.py import \
  --project /home/user/translations/book-volume-2 \
  --previous-volume /home/user/translations/book-volume-1 \
  /path/to/unpacked-volume-2
```

No explicit series number is required.

Behavior:

- no `--previous-volume`: exact standalone behavior as today;
- predecessor has no `series.json`: predecessor becomes volume 1, new project becomes volume 2;
- predecessor has `series.json` with volume N: new project becomes volume N+1 with the same `series_id`.

For volume 3:

```bash
uv run translate.py import \
  --project /home/user/translations/book-volume-3 \
  --previous-volume /home/user/translations/book-volume-2 \
  /path/to/unpacked-volume-3
```

Do not require a series title/name for this MVP. The project paths and volume numbers already establish the intended chain. A human-readable series name can be added later without changing the memory contract.

---

## New project artifacts

### `series.json`

Each project that belongs to a series has a small sidecar:

```json
{
  "format_version": 1,
  "series_id": "series-0123456789abcdefabcd",
  "volume": 2,
  "source_fingerprint": "<this book source fingerprint>",
  "previous": {
    "volume": 1,
    "source_fingerprint": "<previous book source fingerprint>"
  },
  "seed_sha256": "<sha256 of series.seed.json>"
}
```

For the retroactively linked first volume:

```json
{
  "format_version": 1,
  "series_id": "series-0123456789abcdefabcd",
  "volume": 1,
  "source_fingerprint": "<volume-1 source fingerprint>",
  "previous": null,
  "seed_sha256": null
}
```

Requirements:

- UTF-8 JSON;
- atomic writes;
- no absolute project paths persisted;
- validate `source_fingerprint` against that project's `book.json`;
- reject unknown format versions;
- reject impossible/non-positive volume numbers;
- if an existing predecessor `series.json` conflicts with its `book.json`, fail closed;
- if an existing predecessor is already a series volume, never silently replace its `series_id` or volume number.

### Series ID

Use a stable opaque ID. Prefer a deterministic value derived from the first volume's source fingerprint, for example:

```text
series-<first 20 hex chars of SHA256({first_volume_source_fingerprint: ...})>
```

The exact prefix/length is implementation detail, but it must be:

- deterministic;
- stable across retries;
- opaque to the pipeline;
- identical in every volume of the chain.

No global registry is required.

### `series.seed.json`

Continuation volumes receive an immutable-at-import snapshot of the inherited knowledge used to seed their Store.

Recommended conceptual shape:

```json
{
  "format_version": 1,
  "series_id": "series-...",
  "from_volume": 1,
  "to_volume": 2,
  "source_snapshot": {
    "source_fingerprint": "...",
    "book_memory_sha256": "...",
    "approved_lexicon_sha256": "..."
  },
  "terms": [
    {
      "source": "Eldlund",
      "aliases": [],
      "category": "name",
      "polish": "Eldlund",
      "first_seen_volume": 1,
      "meaning_notes": [
        {"text": "...", "confidence": "high"}
      ],
      "series_context": [
        {"kind": "gender", "statement": "...", "confidence": "high"}
      ]
    }
  ],
  "observations": [
    {
      "about": ["Eldlund"],
      "kind": "gender",
      "statement": "...",
      "confidence": "high",
      "first_seen_volume": 1
    }
  ]
}
```

The exact internal field spelling may vary slightly if repository conventions strongly favor another shape, but the semantic content and invariants below are required.

`series.seed.json` is not an LLM output and must not contain newly invented prose.

---

## Preconditions for using a predecessor

A predecessor is eligible when:

1. `book.json` exists and its frozen `content_fingerprint` is valid;
2. `book_memory.json` exists and has the supported format;
3. `lexicon.approved.json` exists;
4. all terminology being inherited has an approved Polish choice;
5. approved lexicon entries agree with the choices in `book_memory.json`;
6. if `series.json` exists, it validates against the predecessor's `book.json`;
7. predecessor and new project are different paths.

The predecessor's P2-P5 translation does **not** need to be complete.

The feature depends on completed P1 + human terminology approval, not on completion of every prose chunk.

Do not open or mutate predecessor `state.sqlite3` merely to build the seed. The canonical JSON artifacts are sufficient and safer for continuation handoff.

Acquire the predecessor project lock while validating/reading the handoff artifacts and writing its retroactive `series.json`. If another Intelitex process is actively using that predecessor, fail with an actionable message rather than racing it.

---

## Deterministic series compaction

Series compaction is a **local deterministic transform** over structured predecessor artifacts.

It must make zero LLM/model calls.

### Inputs

Read only the canonical handoff artifacts needed for the seed:

- predecessor `book.json`;
- predecessor `book_memory.json`;
- predecessor `lexicon.approved.json`;
- predecessor `series.json` if present.

Do not read old provider attempts, prompts, translated prose or arbitrary chapter text for compaction.

### Terms to carry

For every approved predecessor term, retain:

- canonical English `source`;
- aliases;
- category;
- approved Polish form;
- `first_seen_volume` if already inherited, otherwise predecessor volume;
- at most the most useful/recent bounded meaning notes needed for identity/translation context;
- a small bounded set of relevant observation statements about that term as `series_context`.

The seed must not carry the full candidate-history payload merely because it exists.

The approved Polish choice is authoritative for inheritance.

When the seeded term is inserted into the new Store, ensure it has at least one valid candidate representation corresponding to the inherited human-approved choice so the existing review/approval machinery remains structurally valid.

### Observations to carry

Carry reusable Pass-1 observations of the existing kinds:

- `reference`;
- `gender`;
- `register`;
- `technical`;
- `continuity`.

Retain:

- `about`;
- kind;
- statement;
- confidence;
- first-seen volume/provenance needed for audit.

Strip predecessor-local source locations from the active inherited fact:

- old `block_id` evidence;
- old chapter IDs;
- old `available_from_order` values;
- evidence excerpts.

Those coordinates are meaningless inside the new volume and must never masquerade as current-book evidence.

### What compaction must drop

Do not inherit as active new-volume state:

- old P1 evidence block IDs;
- old evidence excerpts;
- old chapter IDs/order numbers;
- rejected Polish alternatives;
- complete candidate/reason history;
- P2/P3/P4/P5 checkpoints;
- translated prose;
- previous-context prose;
- chunk completion state;
- model/provider attempt data;
- pricing/usage evidence;
- review UI revision tokens;
- source file paths.

### Deduplication

Compaction must be deterministic.

Terms should already be merged in predecessor `book_memory.json`; still validate alias ambiguity and fail rather than silently conflating two records.

Observations may be semantically repeated across volumes. Deduplicate conservatively using normalized:

- `about` set/list;
- kind;
- statement.

When duplicates exist, preserve earliest `first_seen_volume` and the strongest supported confidence without inventing a new statement.

Do not use fuzzy semantic merging in this MVP.

### Cumulative behavior

A continuation project's `book_memory.json` will contain inherited seeded terms/facts plus knowledge learned in that current volume.

Therefore volume N+1 may compact the immediately preceding volume N only. It does **not** need to reopen every older project.

This gives:

```text
V1 book_memory
   -> seed V2
V2 seeded memory + V2 discoveries
   -> compact current V2 book_memory
   -> seed V3
```

The seed remains bounded because raw evidence and candidate history are discarded at each handoff.

---

## Seeding the new Store

Series seeding occurs during `import`, after the new Store exists and chunk registration succeeds, but before `book.json` is written as the completed-import marker.

The new Store starts with inherited memory rather than zero terms/facts.

### Seeded terms

Insert inherited terms into the existing `terms` table using the normal local `Txxxxxx` IDs generated by the new project.

Do not preserve predecessor local `Txxxxxx` IDs as global identity.

For each inherited term:

- data contains source, aliases, category, compact meaning notes and small `series_context`;
- `choice` is the prior human-approved Polish form;
- `approved=1` at the term level;
- mark it internally as inherited, e.g. `series_inherited=true`;
- store `series_first_seen_volume`;
- optionally store predecessor local term ID only as provenance, not identity;
- initialize `series_review_required=false`.

After seeding, export `book_memory.json` immediately. A newly imported continuation should therefore visibly have non-empty memory **before** its first `analyze` call.

### Seeded observations

Insert compact inherited observations into `facts` with explicit inherited metadata, e.g.:

```text
series_inherited = true
series_first_seen_volume = N
```

Do not attach fake current-book evidence.

The fact should be valid for all source positions in the new volume because it was known before that volume begins.

### Book-level approval remains false

Do **not** set the new project's global `approved` state merely because inherited terms are approved.

Volume 2 can introduce new terminology, so the normal workflow remains:

```text
analyze -> review -> approve -> translate
```

Inherited term rows are approved individually, but the new book still requires its own review/approval pass before prose translation.

---

## Pass-1 behavior

The first Pass-1 section of a continuation must receive inherited memory through the **existing** `Store.analysis_memory()` / `EXISTING_MEMORY` path.

This is a central acceptance requirement:

> The first P1 inference of volume 2 must no longer see an empty memory if volume 1 contained reusable approved terms.

### Do not change the P1 contract

Keep canonical `EXISTING_MEMORY` exactly:

```json
{
  "matched": [],
  "catalogue": [],
  "catalogue_incomplete": false
}
```

with the current row shapes.

Do not modify:

- `prompts/pass1.txt`;
- `bookpipe/schemas.py` P1 schema;
- `bookpipe/p1_compact.py` compact schema/mappings;
- Codex wire instructions;
- P1 canonical response schema.

### Making inherited facts visible to P1

`analysis_memory()` currently exposes bounded `meaning_notes` for directly matched terms.

For inherited terms, include a bounded amount of the term's `series_context` in that existing `meaning_notes` list/view. This allows useful prior facts such as entity identity, gender or stable technical context to reach P1 without adding a new field to `EXISTING_MEMORY`.

Requirements:

- use short existing observation statements only;
- no LLM-generated summary;
- cap the amount per matched term;
- do not add series context to the broad catalogue rows;
- preserve the current memory token limit behavior;
- if directly matched memory exceeds `memory_tokens`, keep the existing fail-closed behavior rather than silently dropping it.

### Merge with current-volume discoveries

Because inherited terms are already present in the Store, existing `merge_analysis()` alias matching should merge current-volume aliases/evidence/candidates into the inherited local term rather than creating a duplicate.

Preserve the current ambiguity safety rule: if an incoming alias matches more than one existing term, fail instead of conflating entities.

---

## Review behavior

The existing review UI should remain the review UI.

Do not create a separate series review page.

### Inherited unchanged terms

When `terms.review.json` is generated for the continuation:

- inherited terms should use the inherited approved Polish choice;
- inherited terms with no material current-volume disagreement should start as `reviewed=true`;
- optionally mark `review_method: "inherited"` or equivalent metadata if this does not disturb existing UI logic;
- new current-volume terms start unreviewed exactly as today.

This makes the user review only what changed/new rather than hundreds of already-approved terms.

### Material disagreement

When current-volume P1 re-emits an inherited term, do not automatically invalidate the prior human choice merely because there is new evidence.

Set `series_review_required=true` and make that term unreviewed when there is a clear terminology-level disagreement, at minimum:

- the current P1 preferred/first candidate differs from the inherited approved Polish choice; or
- the category changes materially.

Alias-only expansion, new evidence, or additional meaning detail should not by itself force review if the preferred translation remains the inherited choice.

Do not attempt sophisticated contradiction detection in this MVP.

### Approval

The existing `approve` command should remain the only book-level approval step.

It may re-approve inherited terms together with new/current terms. After approval, the normal cumulative `book_memory.json` and `lexicon.approved.json` become the canonical state that can seed the following volume.

---

## Translation-memory behavior

`Store.translation_memory()` should continue selecting only terminology relevant to the current source/context.

Seeded inherited terms participate naturally because they live in the existing term store and already have an approved `choice`.

### Meaning notes

Current-volume meaning notes remain source-order gated as today.

Inherited meaning/context notes are prior-volume knowledge and may be available from the beginning of the new volume. Implement this by explicit inherited metadata rather than fake block order.

### Observations

Current behavior roughly gates observations to the current chapter and source order. Preserve it for current-volume facts.

Add the inherited rule:

```text
include observation if:
  relevant to current source/context
  AND (
       observation is series_inherited
       OR existing current-volume availability rule passes
      )
```

Thus:

- a fact learned in volume 1 is safe anywhere in volume 2;
- a fact learned late in volume 2 must not leak into an earlier volume-2 chunk;
- volume 3 receives both via the cumulative seed generated from volume 2.

Do not inject all inherited observations into every request. Keep the current lexical relevance filtering using `about`/source forms.

---

## Import lifecycle

Recommended high-level sequence for `import --previous-volume`:

1. Resolve new project and predecessor paths.
2. Acquire the normal new-project lock.
3. Acquire predecessor project lock for handoff validation.
4. Validate predecessor `book.json`, `book_memory.json`, approved lexicon and optional `series.json`.
5. Determine:
   - series ID;
   - predecessor volume;
   - new volume = predecessor + 1.
6. Deterministically build the complete seed in memory.
7. If predecessor was a legacy standalone project, atomically write its `series.json` as volume 1.
8. Release predecessor lock after the required handoff artifacts are captured.
9. Run the existing new-book import/planning flow unchanged.
10. Write new project settings/prompts exactly as today.
11. Create Store and register chunks exactly as today.
12. Write `series.seed.json` atomically.
13. Seed Store terms/facts in one SQLite transaction.
14. Store minimal seed metadata in `kv` if useful for integrity/audit.
15. Export seeded `book_memory.json`.
16. Write the new project's `series.json` atomically.
17. Write `book.json` last, preserving the current completed-import marker contract.

If implementation constraints make a slightly different ordering safer, preserve these invariants:

- predecessor frozen artifacts are never rewritten;
- series sidecar writes are atomic and idempotent;
- new `book.json` remains the completed-import marker written last;
- no half-seeded project is presented as a successfully imported project.

A retroactively created volume-1 `series.json` is harmless even if the later new-volume import fails; it only records the intended series identity and does not affect volume-1 translation state.

---

## Failure behavior

Fail closed with actionable errors.

Examples:

- predecessor project missing `book.json` -> error;
- predecessor frozen manifest fingerprint invalid -> error;
- predecessor has no completed approved terminology artifacts -> error;
- `book_memory.json` and `lexicon.approved.json` disagree -> error;
- predecessor `series.json` source fingerprint does not match `book.json` -> error;
- predecessor `series.json` has unsupported format version -> error;
- predecessor is already volume N but metadata claims another series -> error;
- alias ambiguity during seed validation -> error;
- new project equals predecessor path -> error;
- previous project is currently locked by another Intelitex writer -> error;
- seed cannot fit the existing direct-match memory budget later -> retain current explicit memory-limit error;
- inherited fact has malformed kind/about/statement -> error rather than inventing a repair.

Do not silently downgrade a continuation to a standalone import.

Do not silently discard inherited records to make the seed smaller.

---

## Compatibility and checkpoint invariants

### Existing volume 1

When a legacy project is first linked as volume 1:

- `book.json` must remain byte-for-byte unchanged;
- `state.sqlite3` must not be opened for mutation by the linking operation;
- existing P1-P5 checkpoint fingerprints must not change;
- prompts must not change;
- artifacts must not change;
- translation output must not change;
- the only required new file in that project is `series.json`.

### Standalone books

Without `--previous-volume`:

- no `series.json` is created;
- no `series.seed.json` is created;
- Store starts empty exactly as today;
- existing deterministic tests/fingerprints remain unchanged.

### New continuation volume

P1 fingerprints in the new continuation will naturally differ from a hypothetical standalone import because `EXISTING_MEMORY` is different. This is intentional.

Once analysis begins, the imported seed is frozen as part of that project's initial state. Do not rebuild or silently replace the seed on restart.

Existing per-unit `analysis_inputs/*.json` snapshots remain authoritative for resumed P1 work.

### P1 compact transport

The exact canonical memory object shape must remain compatible with `compact-v1`.

Add a regression test proving the first continuation P1 input can still be encoded by the existing compact codec without changing the compact schema.

---

## Suggested implementation footprint

Prefer a small implementation concentrated in these areas.

### New module

A reasonable new module is:

```text
bookpipe/series.py
```

Responsibilities can include:

- series metadata validation;
- deterministic series ID creation;
- predecessor handoff validation;
- seed compaction;
- `series.json` / `series.seed.json` serialization helpers.

Keep this module pure/file-oriented where practical. It must not know about provider transports.

### `bookpipe/cli.py`

Minimal changes:

- add `import --previous-volume PATH`;
- prepare/validate continuation handoff;
- call Store seeding at the existing import boundary;
- optionally show the resulting volume number/series ID in the import completion message;
- optionally show series volume in `status` when `series.json` exists.

Do not redesign the CLI.

### `bookpipe/store.py`

Small targeted changes:

- add a transactional method for seeding inherited terms/facts into an empty new Store;
- expose bounded `series_context` through the existing matched `meaning_notes` view in `analysis_memory()`;
- mark inherited unchanged review terms reviewed by default;
- mark terminology-level inherited disagreements for review;
- make inherited meaning notes available from the beginning of the new volume;
- include relevant inherited observations in `translation_memory()` while preserving current-volume gating.

Do not change the SQLite schema unless the implementation absolutely requires it. Prefer extra fields inside existing JSON `data`/fact payloads and existing `kv` metadata.

### Files that should not require changes

The intended design requires no edits to:

- `prompts/pass1.txt` through `pass5.txt`;
- `bookpipe/schemas.py`;
- `bookpipe/p1_compact.py`;
- `bookpipe/codex_transport.py`;
- `bookpipe/openai_transport.py`;
- provider/profile code;
- importer text/chapter parsing;
- Reader.

If implementation starts requiring changes there, re-check the design before proceeding.

---

## Data-integrity details

### Atomicity

Use existing `atomic_json()` for sidecars.

Seed Store changes must happen in one SQLite transaction.

### Determinism

Given identical predecessor canonical artifacts, compaction must produce byte-equivalent semantic JSON apart from normal pretty-print formatting.

Do not include timestamps, random UUIDs or absolute paths in the seed.

### Unicode

Preserve existing UTF-8/NFC behavior. Do not normalize approved Polish prose beyond current identifier matching behavior.

### Provenance

The seed must retain enough provenance to answer:

- which series/volume supplied the seed;
- which predecessor source fingerprint supplied it;
- hashes of the exact predecessor `book_memory.json` and approved lexicon;
- first-seen volume for inherited records where available.

It does not need to preserve old block evidence inside the active new-volume memory.

---

## Tests

Add deterministic tests only. No live model call is required.

### 1. Standalone regression

Prove that ordinary import without `--previous-volume`:

- creates no series artifacts;
- seeds no terms/facts;
- preserves existing import/analyze behavior.

### 2. Legacy volume-1 retrofit

Create a synthetic predecessor representing a normal pre-feature project with:

- valid `book.json`;
- completed `book_memory.json`;
- approved lexicon;
- no `series.json`.

Import a new continuation.

Assert:

- predecessor receives `series.json` volume 1;
- new project receives same `series_id`, volume 2;
- predecessor `book.json` bytes/hash are unchanged;
- predecessor translation/checkpoint artifacts are unchanged;
- predecessor SQLite is not mutated by linking.

### 3. Volume increment

Given predecessor `series.json` volume 2, import continuation and prove new project is volume 3 with same `series_id`.

### 4. Deterministic seed

Run compaction twice over the same predecessor fixtures and assert identical semantic seed/hash.

### 5. Compaction contents

Prove that the seed:

- retains source, aliases, category and approved Polish form;
- retains bounded meaning/context notes;
- retains reusable observations;
- strips old block IDs, chapter IDs, order numbers and excerpts;
- drops rejected candidate history;
- contains no translated prose or attempt artifacts.

### 6. Approval consistency

Fail if predecessor `book_memory.json` approved choice disagrees with `lexicon.approved.json`.

Fail if any term required for inheritance lacks an approved choice.

### 7. Seeded Store

After continuation import, before `analyze`:

- `book_memory.json` exists;
- inherited terms are present;
- inherited term choices are approved at term level;
- inherited observations are present with explicit inherited metadata;
- global new-book approval remains false;
- no current-book P1 receipt exists yet.

### 8. First P1 memory

Construct the first analysis unit with a source mentioning an inherited entity.

Assert `Store.analysis_memory()` returns that entity in `matched` and includes inherited context through the existing meaning-notes field.

Also assert an unrelated inherited entity may appear only in the bounded catalogue.

### 9. Compact-v1 compatibility

Feed the resulting canonical continuation P1 input to the existing `p1_compact.encode_input()`.

Assert it succeeds without adding any new `EXISTING_MEMORY` field and without modifying the compact schema.

### 10. Merge recurring entity

Given a seeded inherited term, merge a synthetic current-volume P1 result adding a newly attested alias.

Assert:

- one local term remains, not two;
- alias is added;
- inherited approved choice is preserved;
- alias-only extension does not force terminology re-review if preferred translation remains unchanged.

### 11. Review defaults

Assert:

- inherited unchanged term -> `reviewed=true`;
- new term -> `reviewed=false`;
- inherited term with changed preferred Polish candidate -> `reviewed=false` / series review required;
- inherited category disagreement -> review required.

### 12. Translation memory

For a current-volume chunk mentioning an inherited entity, assert:

- inherited approved lexicon is selected;
- inherited observation is available from the first relevant chunk;
- unrelated inherited observations are not injected.

### 13. Current-volume spoiler gating regression

Create a current-volume observation available only at a later order.

Assert it remains unavailable to an earlier chunk exactly as before.

### 14. Failure cases

Cover at least:

- missing predecessor memory;
- missing approved lexicon;
- malformed series metadata;
- source-fingerprint mismatch;
- predecessor/new project same path;
- alias ambiguity;
- locked predecessor project if lock behavior is straightforward to exercise deterministically.

### 15. Cumulative V1 -> V2 -> V3 fixture

Build a small synthetic chain:

- V1 has term A + observation A;
- V2 inherits A and discovers term B;
- after V2 approval, compact V2 into V3.

Assert V3 seed contains A and B exactly once, with approved choices, and does not require reopening V1.

---

## README documentation

Add a concise section documenting:

- `import --previous-volume`;
- automatic volume numbering;
- retroactive volume-1 `series.json` creation;
- deterministic no-LLM compaction;
- requirement that predecessor terminology be approved;
- predecessor prose translation need not be complete;
- inherited terms are pre-reviewed unless current P1 disagrees materially;
- standalone projects remain unchanged.

Do not duplicate the full PRD into README.

---

## Manual acceptance scenario

Using synthetic/local projects is sufficient for implementation acceptance.

Expected user flow:

```bash
# Volume 1 already exists and is approved.

uv run translate.py import \
  --project /tmp/series-v2 \
  --previous-volume /tmp/series-v1 \
  /tmp/unpacked-v2

uv run translate.py status --project /tmp/series-v1
uv run translate.py status --project /tmp/series-v2
uv run translate.py analyze --project /tmp/series-v2
uv run translate.py review --project /tmp/series-v2
```

Expected observations:

- V1 is now identified as series volume 1;
- V2 is volume 2 with the same series ID;
- V1 existing book/checkpoints remain untouched;
- V2 has non-empty `book_memory.json` immediately after import;
- the first V2 P1 request has inherited `EXISTING_MEMORY`;
- recurring inherited terminology does not have to be reviewed again unless there is a material disagreement;
- new V2 terminology behaves exactly like current terminology;
- after V2 approval, V2 can serve directly as predecessor for V3.

---

## Completion criteria

The feature is complete only when all of the following are true:

1. `import --previous-volume PREVIOUS_PROJECT` exists.
2. A pre-feature predecessor with no series metadata is retroactively marked volume 1.
3. Its `book.json` remains byte-for-byte unchanged.
4. Its existing SQLite/checkpoints/prompts/artifacts are not mutated by the series-link step.
5. A continuation gets the same `series_id` and automatic next volume number.
6. `series.seed.json` is deterministic and contains only compact reusable knowledge.
7. Compaction makes no LLM call.
8. New continuation Store is seeded before P1.
9. `book_memory.json` is non-empty immediately after continuation import when predecessor memory was non-empty.
10. The first P1 request can see inherited terms through the existing `EXISTING_MEMORY` contract.
11. Existing Codex P1 compact-v1 encoding still works unchanged.
12. No P1 prompt/schema/transport contract is changed.
13. Inherited approved Polish choices are preserved.
14. Inherited unchanged terms are pre-reviewed in the continuation review state.
15. Material terminology disagreement makes the inherited term require review.
16. Prior-volume observations are available to relevant P2-P5 requests from the beginning of the new volume.
17. Current-volume observation spoiler gating remains unchanged.
18. V3 can be seeded from V2 alone and receives cumulative V1+V2 knowledge.
19. Standalone import behavior remains unchanged.
20. Deterministic tests for the above pass.
21. The full repository test suite passes.
22. README is updated narrowly.
23. Implementation changes are committed coherently.

---

## Codex implementation guidance

Read this PRD completely before modifying code.

Inspect the current checkout rather than assuming line numbers or behavior from this document are exact. The repository may have advanced after this PRD was written; preserve the stated invariants rather than mechanically forcing an obsolete patch shape.

Prefer the smallest implementation consistent with the current architecture.

The key design principle is:

> A series continuation is still a normal Intelitex book project. The only new behavior is a deterministic inherited-memory seed created at import time.

Do not create a second translation pipeline.

Do not make live model calls to implement or test this feature.

Do not touch real user translation projects. Use temporary synthetic projects/fixtures only.

Finish with coherent committed changes. Do not push or open a pull request unless explicitly requested by the owner in the implementation session.

In the final report to the owner, write in Polish and include:

- files changed;
- exact series metadata/seed format chosen;
- how legacy volume 1 is retrofitted without touching `book.json`;
- compaction rules;
- Store seeding boundary;
- P1 compact-v1 compatibility proof;
- review behavior for inherited/new/conflicting terms;
- translation-memory observation behavior;
- tests run and counts;
- final commit SHA(s);
- any deliberately deferred follow-up work.
