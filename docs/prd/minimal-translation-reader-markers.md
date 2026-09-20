# PRD — Minimal local translation reader with fast review markers

## Status

Implementation specification for the first Intelitex prose-reading/review-capture UI.

This feature is intentionally narrow. It is a **reader first**: the user should be able to read the translated book normally and mark a suspicious location with one fast gesture without stopping to classify, explain, or correct it.

The future prose reviewer and LLM-assisted correction workflow are explicitly out of scope for this task.

## Product intent

After Intelitex translates prose, the owner wants to read it naturally in a browser on desktop or phone.

While reading, if something looks wrong — for example a speaker appears to have been confused, grammatical gender is wrong, a sentence feels mistranslated, or any other issue is suspected — the owner must be able to mark that location immediately and continue reading.

The interaction must be approximately:

```text
read
  -> notice something suspicious
  -> one configured gesture over/at that place
  -> brief visual acknowledgement
  -> continue reading
```

There must be no error taxonomy, note editor, correction dialog, AI call, dashboard, statistics, or required decision in the reading flow.

Later work will consume these markers in a separate reviewer. Do not implement that reviewer now.

## Existing repository facts to preserve

Inspect the actual checkout before modifying code.

Important current invariants include:

- `book.json` already contains stable block IDs such as `B0001234`;
- imported narrative blocks are the canonical paragraph/block identity;
- P5 `result.json` stores `translations[]` keyed by the corresponding block IDs;
- `state.sqlite3` registers the accepted final artifact path and checksum for each translation unit;
- `bookpipe/review_context.py` already demonstrates how to read P5 text through the checkpoint database and verify result hashes;
- `bookpipe/review.py` already provides a small local HTTP application using the Python standard library and atomic JSON writes;
- `terms.review.json` is terminology-review state and must not be overloaded with prose-location markers.

Do not create a second paragraph identity scheme. Reuse the existing block IDs.

Do not derive marker identity from rendered line numbers. A rendered line changes when viewport width, font, font size, or browser changes.

## Goals

1. Add a local browser-based reader for translated Intelitex projects.
2. Render translated chapters from canonical/checkpointed project data while preserving existing block IDs in the DOM.
3. Provide comfortable reading controls:
   - font size;
   - font family;
   - line height;
   - content width;
   - light theme;
   - sepia theme;
   - dark theme;
   - fullscreen.
4. Work responsively on current desktop Chrome/Chromium and Firefox, and on current mobile Chrome/Chromium and Firefox.
5. Preserve Polish and arbitrary Unicode text correctly as UTF-8.
6. Let the user choose the marker gesture in Reader settings.
7. Support at least:
   - tap/click at a text location;
   - long press at a text location;
   - horizontal drag/swipe over text.
8. Convert an imprecise gesture into a stable location inside an existing translated block.
9. Persist the marker atomically in a small project JSON file intended for later prose review.
10. Show a subtle marker in the text gutter/margin after saving.
11. Clicking/tapping that existing gutter marker must:
    - highlight the stored text/location;
    - expose exactly one marker action: **Delete marker**.
12. Deleting a marker must remove it atomically and immediately update the UI.
13. Remember reader presentation preferences and reading position locally in the browser.
14. Add deterministic tests for data assembly, marker persistence/validation, deletion, and the HTTP API.
15. Make no LLM/model/provider call.

## Non-goals

Do **not** implement any of the following in this task:

- prose review workflow;
- marker list or marker dashboard;
- marker counts in the table of contents;
- issue categories such as grammar/style/speaker/terminology;
- notes/comments attached while reading;
- severity or confidence;
- reviewer statistics;
- AI analysis of a marker;
- AI correction proposals;
- accepting/rejecting corrections;
- applying corrections to P5 output;
- automatic retranslation;
- EPUB generation;
- page-layout simulation;
- physical screen brightness control;
- downloadable/web fonts;
- account/authentication system;
- cloud service;
- collaboration/multi-user review.

The reader must remain deliberately small.

## Product principle: preserve reading flow

The reader is not a form.

Do not show a popup or menu after a new marker is created. A successful marker operation should produce only a short transient visual acknowledgement, for example a brief highlight/fade at the captured location, followed by the persistent small gutter marker.

The user should not have to type anything or choose anything after the gesture.

The only marker popup/action in this version is opened by deliberately activating an **existing gutter marker**, and its only action is **Delete marker**.

## CLI

Add a new command:

```bash
uv run translate.py reader --project "$PROJECT"
```

Use naming consistent with the existing CLI.

Suggested options:

```text
--bind ADDRESS
--reader-port PORT
--no-browser
```

Recommended defaults:

- `--bind 127.0.0.1`;
- a reader port distinct from the terminology review default, for example `8766`;
- open the browser unless `--no-browser` is supplied.

Phone access must be supported intentionally. The user can explicitly bind to a LAN-visible interface, for example:

```bash
uv run translate.py reader --project "$PROJECT" --bind 0.0.0.0
```

When binding to a non-loopback address, print a clear warning that this is an unauthenticated local editing service and is reachable by hosts allowed by the machine/network firewall.

Do not silently expose it outside loopback.

Use the existing project-lock discipline unless the actual repository architecture provides a safer equivalent. The Reader writes marker state, so it must not race project-changing CLI operations.

## Reader source of truth

### Do not render from `translation.txt`

`translation.txt` and `translated_chapters/*.txt` lose block identity.

The reader must construct its chapter model from:

1. `book.json` for canonical chapter/block structure and stable block IDs;
2. `state.sqlite3` for accepted translation-unit state/final paths;
3. checkpointed P5 `result.json` artifacts for Polish text.

Verify accepted result hashes before presenting text as valid translated prose, following the existing safety pattern in `bookpipe/review_context.py`.

Do not pick arbitrary result directories.

Do not infer alignment by text matching.

### Block identity

The DOM representation of every rendered translated prose block must expose its existing canonical block ID, for example:

```html
<p class="reader-block" data-block-id="B0001234">...</p>
```

No new persistent paragraph IDs should be created.

If the current project contains translation pieces with `parent_id`, reconstruct the visible canonical block deterministically from those pieces in reading order. Keep the canonical parent block ID as the Reader anchor. Do not change source/project IDs.

### Incomplete or stale translation

Never silently present missing/corrupt data as complete.

If a chapter is only partially translated, render only checkpoint-verified translated content and show a clear non-prose boundary that the remaining chapter is unavailable.

If an accepted artifact is missing or fails its saved checksum, return/report an error for that content rather than falling back to an unverified file.

Existing stale semantics should be represented accurately if encountered; do not invent a new translation result.

## Persistent marker state

Create a narrow prose-review handoff file:

```text
translation.review.json
```

Do not store reader markers in `terms.review.json`; that file is for terminology decisions and terminology notes.

Initial shape:

```json
{
  "format_version": 1,
  "book_fingerprint": "<book source fingerprint>",
  "markers": [
    {
      "id": "M000001",
      "chapter_id": "ch0001",
      "block_id": "B0001234",
      "start": 37,
      "end": 62,
      "text": "exact captured Polish text"
    }
  ]
}
```

This is intentionally minimal.

Do not add:

- issue type;
- note;
- status;
- severity;
- proposed correction;
- model metadata;
- review statistics.

The future reviewer may extend or consume this file in a separate task.

### Marker offsets

`start` and `end` are offsets inside the **rendered Polish text of the canonical block**, not source-English offsets and not visual-line coordinates.

Define them as Unicode code-point offsets so Python and browser logic have an explicit, browser-independent storage contract.

The browser may receive DOM offsets in UTF-16 code units. Convert them deliberately to Unicode code-point offsets before persistence.

Required invariant:

```python
rendered_polish_text[start:end] == marker["text"]
```

at marker creation time.

The server must validate:

- chapter exists;
- block exists in that chapter;
- block currently has checkpoint-verified Polish text;
- `start` and `end` are integers, not booleans;
- `0 <= start < end <= len(text)`;
- submitted `text` matches the exact server-side substring.

Do not trust client-provided block text.

### Minimal location is sufficient

The user is marking a place to revisit, not creating a precise editorial annotation.

For a tap/click or long press, capture the nearest word/location.

For a horizontal drag, capture the approximate span and expand/snap outward to whole-word boundaries.

It is acceptable for the marker to cover a few words around the problem. Precision must not come at the cost of reading flow.

### Duplicate behavior

An exact duplicate marker for the same block/range should be idempotent rather than creating visually stacked duplicates.

Do not attempt semantic or fuzzy deduplication of overlapping but different ranges.

### Atomic writes and conflicts

Use existing atomic JSON utilities.

Do not risk truncating/corrupting the file on process interruption.

Serialize browser writes. Reuse the existing review application's revision/conflict pattern where practical so two tabs cannot silently overwrite each other's marker changes.

## Gesture model

Reader settings must allow selecting the active marker gesture.

Implement at least these modes:

### 1. Tap / click

A tap/click on prose creates a marker around the nearest word.

Avoid marking when the interaction target is reader chrome, settings, navigation, or an existing gutter marker.

### 2. Long press

Holding on prose for a short configurable implementation threshold creates a marker around the nearest word/location.

When this gesture mode is active, prevent the browser context-menu/native long-press behavior only where required inside Reader prose. Do not globally disable browser behavior.

### 3. Horizontal drag / swipe

Pointer-down and pointer-up positions define the approximate text range.

Use standard Pointer Events.

Map viewport coordinates to a text caret with:

1. `document.caretPositionFromPoint(x, y)` when available;
2. `document.caretRangeFromPoint(x, y)` as a compatibility fallback.

The drag may begin/end imprecisely. Normalize direction, keep it within one canonical reader block for MVP, and snap the resulting range to complete word boundaries.

Use a browser-native segmentation mechanism such as `Intl.Segmenter` when available, with a small deterministic fallback. Do not add a large text-processing dependency for this.

### Scrolling interaction

Normal vertical touch scrolling must remain natural.

A horizontal marker gesture must not make vertical reading unpleasant.

Use appropriate Pointer Events / CSS touch-action handling and a direction threshold so a mostly vertical move remains scrolling, not a marker.

Do not introduce page-turn gestures in this task.

## Marker visual behavior

### After creation

On successful creation:

1. briefly highlight/fade the captured location;
2. do not open a dialog;
3. leave a small persistent marker in the gutter/margin aligned with the marked location.

The persistent marker should be visually quiet and must not disturb text reflow.

### Existing marker activation

When the user taps/clicks the gutter marker:

1. highlight the marker's stored range in the prose;
2. show a tiny anchored control/popover;
3. that control contains only:
   - `Delete marker`.

No edit, note, category, review, AI, or navigation actions belong in this popup.

Clicking elsewhere or pressing Escape may dismiss it.

### Delete

Activating `Delete marker`:

- deletes the marker through the server;
- atomically persists the new state;
- removes the gutter marker;
- removes the temporary highlight/control;
- does not modify translation artifacts.

A separate confirmation dialog is not required.

## Reader UI

Keep the UI deliberately quiet.

Required reading features:

- chapter title;
- previous chapter;
- next chapter;
- table of contents/chapter chooser;
- settings control;
- fullscreen control;
- reading area.

Do not add marker counts or a marker-review page in this version.

### Reading settings

Provide:

- font size;
- font family;
- line height;
- content width;
- theme: light / sepia / dark;
- marker gesture.

Use a compact settings panel that is hidden during normal reading.

Persist these presentation preferences in browser `localStorage`, not in canonical project JSON.

No server write is required when the user changes appearance.

Use system/generic font stacks; do not fetch external font assets.

### Reading position

Remember the last reading position per project/browser using `localStorage`.

Store a stable logical location where practical, for example chapter ID + block ID plus a small relative/offset value, rather than only raw document pixel scroll position.

Restoring position should be best-effort and must never alter project data.

### Fullscreen

Use the browser Fullscreen API from an explicit user action.

Fullscreen is presentation only.

### Mobile layout

Include the normal UTF-8 and viewport metadata.

The reading column must work on narrow phone screens without horizontal scrolling.

Controls must be touch-sized but visually unobtrusive.

Gutter markers must remain reachable on mobile without covering prose.

## Suggested implementation architecture

Prefer the repository's existing lightweight style over introducing a SPA framework.

A reasonable shape is:

```text
bookpipe/reader.py
    HTTP server / repository / page shell

bookpipe/reader_context.py
    read-only chapter + checkpointed P5 assembly

bookpipe/reader.js
    pointer gestures, range mapping, settings, localStorage, marker UI

bookpipe/reader.css
    themes and responsive typography
```

Exact file names may differ if the existing checkout has a better convention.

Do not add React, Vue, Svelte, ProseMirror, Tiptap, Hypothesis, or another large client framework merely to render plain translated blocks and capture locations.

The existing standard-library HTTP approach used by `bookpipe/review.py` is sufficient unless repository inspection reveals a concrete reason otherwise.

Keep data assembly, marker persistence, and browser interaction separable enough to test independently.

## Suggested HTTP API

Keep the API small.

A reasonable contract:

```text
GET    /api/reader
GET    /api/chapters/<chapter_id>
POST   /api/markers
DELETE /api/markers/<marker_id>
```

The exact route names are not important; the behavior is.

### Reader metadata

Return:

- book/project identity needed by the browser;
- ordered chapter IDs/titles;
- current marker state or marker data needed for the loaded chapter;
- a revision token if conflict protection is implemented at the API layer.

Do not send arbitrary filesystem paths to the browser.

### Chapter payload

Return ordered rendered blocks such as:

```json
{
  "id": "ch0001",
  "title": "Chapter 1",
  "blocks": [
    {
      "id": "B0001234",
      "kind": "paragraph",
      "text": "Polish checkpoint-verified P5 text..."
    }
  ],
  "complete": true
}
```

Markers for the chapter may be included in this response or loaded with reader metadata.

### Create marker

The browser submits only the location/range information and revision metadata required by the API.

The server resolves the canonical block text and validates the substring before writing.

### Delete marker

Delete by marker ID with revision/conflict protection.

Unknown marker IDs should fail clearly rather than silently deleting a different item.

## HTTP/local-service safety

This is local software, but do not make careless browser-write endpoints.

At minimum:

- no permissive CORS;
- accept mutation requests as JSON;
- validate request size;
- validate all IDs against project data;
- do not accept project paths from browser payloads;
- do not serve arbitrary files;
- reject malformed paths/IDs;
- keep loopback the safe default.

If the existing review server has same-origin/CSRF/revision helpers, reuse or factor them sensibly rather than weakening its behavior.

Do not turn this task into a general web-security framework.

## Unicode and text rendering

All HTML/API/JSON text must be UTF-8.

Polish characters such as:

```text
ą ć ę ł ń ó ś ź ż
Ą Ć Ę Ł Ń Ó Ś Ź Ż
```

must round-trip without normalization loss.

Do not apply Unicode normalization to translated prose merely for marker handling.

The text inserted into the DOM must be treated as text, not trusted HTML. Do not use model/translation content as `innerHTML`.

## Failure behavior

Fail closed for project-data inconsistencies.

Examples:

- marker references unknown block -> error;
- chapter/block has no verified P5 translation -> do not create marker;
- marker range outside block -> error;
- marker text does not match current block text -> conflict/error;
- final artifact hash mismatch -> show content error, not unverified prose;
- marker JSON belongs to another book fingerprint -> error with actionable message.

Do not repair/checkpoint/retranslate model output from the Reader.

## Testing requirements

Add deterministic tests without any model call.

### Chapter/read-model tests

Prove that:

1. chapter text is assembled from `book.json` + registered final P5 artifacts;
2. P5 result hashes are checked;
3. existing block IDs survive into the reader model;
4. parent/piece data is reconstructed deterministically if present;
5. incomplete translation is represented as incomplete, not fabricated;
6. corrupt/missing final artifacts are not silently served.

### Marker repository tests

Prove that:

1. first marker creates `translation.review.json`;
2. book fingerprint is recorded/validated;
3. valid marker uses the existing block ID;
4. exact substring and offsets are preserved;
5. Unicode/Polish text round-trips;
6. out-of-range offsets are rejected;
7. boolean/non-integer offsets are rejected;
8. mismatched submitted text is rejected;
9. unknown chapter/block IDs are rejected;
10. exact duplicate creation is idempotent;
11. deletion removes only the requested marker;
12. stale revision writes cannot silently overwrite newer state if revision protection is used;
13. writes use the atomic repository utility.

### HTTP tests

Prove that:

- reader root loads;
- chapter endpoint returns only validated project content;
- create-marker endpoint persists a valid marker;
- invalid marker request returns a clear 4xx response;
- delete-marker endpoint removes it;
- arbitrary file/path access is not possible through these endpoints.

### Browser logic

Keep gesture/range logic in small pure functions where practical.

At minimum make the implementation straightforward to exercise manually for:

- desktop Chrome/Chromium;
- desktop Firefox;
- Android Chrome/Chromium;
- Android Firefox.

If the repository already has a no-dependency way to execute JavaScript unit tests, test word snapping and code-point offset conversion there. Do not add a heavy JS toolchain solely for this MVP.

## Manual acceptance checks

Before completion, manually inspect the rendered application and verify:

1. light theme is readable;
2. sepia theme is readable;
3. dark theme is readable;
4. font-size control works;
5. font-family control works;
6. line-height control works;
7. content-width control works on desktop;
8. narrow/mobile layout does not horizontally overflow;
9. fullscreen can be entered/exited;
10. chapter navigation works;
11. reading position restores after reload;
12. configured marker gesture can be changed;
13. tap/click mode creates a marker at the intended location;
14. long-press mode creates a marker without an unwanted menu when active;
15. horizontal drag captures a reasonable whole-word span;
16. creation gives a brief transient acknowledgement only;
17. a subtle gutter marker remains;
18. clicking the gutter marker highlights its location;
19. the marker control contains only `Delete marker`;
20. delete removes the marker and persists after reload;
21. Polish diacritics render correctly.

Do not make a live LLM call as part of acceptance.

## Documentation

Update README narrowly with:

- what the Reader is for;
- the `reader` command;
- how to bind for phone/LAN access;
- the safety warning for non-loopback bind;
- what `translation.review.json` contains;
- the fact that marker classification/review/correction is future work.

Do not present this as a completed AI reviewer.

## Scope protection

This task should remain a small reader + marker capture feature.

If implementation work starts requiring:

- changes to P1-P5 prompts;
- new LLM calls;
- P5 mutation;
- terminology approval changes;
- EPUB parsing changes;
- SQLite schema migration;
- a frontend framework;
- prose-correction workflow;

stop and redesign. None of those are required for this feature.

## Completion criteria

The task is complete only when:

1. `reader` command starts the local app;
2. checkpoint-verified Polish P5 text is shown chapter-by-chapter;
3. existing canonical block IDs are used as anchors;
4. light/sepia/dark and typography settings work;
5. settings are local browser preferences;
6. reading position restores;
7. marker gesture is configurable;
8. tap/click, long press, and horizontal drag modes are implemented;
9. marker creation requires no follow-up dialog or classification;
10. marker location is snapped to a reasonable whole-word range;
11. markers persist in `translation.review.json`;
12. gutter markers are subtle and do not reflow prose;
13. activating a gutter marker highlights the captured location;
14. the only marker action shown is `Delete marker`;
15. deletion persists;
16. no marker list/count/dashboard exists;
17. no LLM call is possible from this feature;
18. deterministic tests pass;
19. the full repository test suite passes;
20. README is updated;
21. changes are committed coherently.

## Codex implementation instructions

Work in the current Intelitex checkout.

Read this entire PRD before modifying code. Inspect repository instructions and current implementation instead of assuming the checkout matches an earlier conversation.

Prefer the smallest implementation consistent with the existing architecture.

Do not stop after producing a plan: implement the feature, tests, and documentation.

Do not make live model calls.

Do not touch or rewrite existing translated artifacts merely to test Reader behavior; use synthetic temporary projects/fixtures.

Finish with committed changes unless repository instructions explicitly say otherwise.

In the final report to the owner, write in Polish and include:

- files changed;
- persistent marker format chosen;
- how P5 text is assembled and verified;
- gesture implementation and compatibility fallbacks;
- tests run and counts;
- manual browser checks actually performed;
- limitations/follow-up items;
- final commit SHA(s).
