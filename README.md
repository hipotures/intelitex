# Local book translation

A local Python workflow for an **unpacked EPUB / HTML folder** and a running
`llama-server`. It imports text once, analyzes the entire book, **stops for human
terminology approval**, and then translates the next requested number of natural translation units.
Completed model calls survive interruption and are not repeated merely because
you start another session.

### Clear scoped review counters / v1.10

The review category chips now show a **single matching-term count** instead of the previous `remaining/matching` fraction. For example, under `Uncertain`, `✓ Status & social 1` means one uncertain term matches that category and all of it has been reviewed. Green always means that every term matching the current status/search/category scope is reviewed. The header line separately reports reviewed and remaining counts.


The two filter rows now intersect. Category badges are calculated from the current
status filter and search; status-filter counts are calculated from the selected
category and search. Each badge shows the number of matching terms, while its color
indicates whether that scope is fully reviewed. Switching to All recalculates both
the count and completion color.

Green means the *matching scope* is fully reviewed, not that its entire category
is finished under every filter. A genuinely empty scope stays gray. Under Unreviewed,
an empty queue is green only if its nonempty category/search parent is completed.
The status Reviewed filter intentionally includes previously reviewed entries.
Uncertain records remain uncertain after review: model confidence is not overwritten
by the human decision. Notes remain available for a later contextual check.

**Review remaining in this view (N)** marks only still-unreviewed records in the
intersection of status, category and search. It asks for confirmation with the exact
count and scope, preserves each current selected/custom form and all notes, and
backs up the review JSON under `history/review_before_bulk_<hash>.json` before one
atomic write. It does not choose candidate 1 again, approve hidden categories, call
a model or globally confirm the glossary. Selecting All + All first deliberately
expands this operation to all remaining records. Bulk-reviewed records are tagged
with `review_method: "bulk"`; individual decisions use `"individual"`.

Browser writes are serialized. Revision tokens reject stale-tab overwrites instead
of silently replacing another tab's edits. The token is API metadata and is not
persisted into the review file. Pending custom forms and notes are flushed before
changing filters or selecting another entity. The footer remains at the bottom of
the detail pane, and the shortcut label is now centered and reads `Ctrl+Enter`.

#### Review a provisional choice in translated context

A practical workflow is to keep a provisional lexical choice, add a reviewer note
such as `Check this term in the Polish context`, and mark the choice reviewed.
After the remaining choices are reviewed, confirm the glossary and run:

```bash
uv run translate.py approve --project "$PROJECT"
uv run translate.py translate --project "$PROJECT" --continue 5
uv run translate.py review --project "$PROJECT" --review-port 9000
```

Stop the review server before invoking approve or translate: the existing CLI
project lock intentionally allows only one such command at a time.

The evidence pane now shows **English original / Polish P5** side by side when a
committed final translation exists. On narrower screens these columns stack.
It reads the immutable source blocks from `book.json` and the registered final paths
from `state.sqlite3` through a separate **read-only connection**. It checks artifact
hashes against saved checkpoints. It never guesses an alignment by matching an
inflected Polish term, picks an arbitrary result directory or makes an LLM request.
Without a translation it shows `Not translated yet`. Without a full project manifest,
it can still show the stored English excerpts from the review JSON.

The source is a full source block where available, not a newly generated excerpt.
A shortened Pass-1 analysis-piece reference can resolve to its known parent block;
no lexical or semantic rematching is performed. Corrupt, missing or partially
translated blocks are reported, not presented as a complete translation.

After translation, use **Notes** or **Uncertain** to revisit the provisional choices.
Notes are review annotations; they are not automatically instructions for P2-P5.
A changed lexical choice reopens its review as before. The comparison warns when
it differs from the currently approved lexicon, and labels old final text for units
already marked `stale`. The existing `approve` command marks dependent completed
translation units stale; `translate --continue N` regenerates those units through
P2-P5 and retains old artifacts. This is **unit-level retranslation**, not a global
search-and-replace or a new paragraph-only AI correction pass. No separate pre-approval
mode, automatic inflection substitution or Codex integration is introduced.

Upgrade by replacing the program files only. Keep the existing project directory,
`terms.review.json`, checkpoints and database. No import, P1 rerun or reset of human
choices is required. The changed modules are `bookpipe/review.py`, the pure browser
logic `bookpipe/review_filters.js`, and read-only evidence adapter
`bookpipe/review_context.py`. Model prompts and P1/P2-P5 orchestration are unchanged.

### Local terminology review application / v1.8

`terms.review.json` is now the single source of truth for human terminology decisions.
Run `uv run translate.py review --project "$PROJECT"` to start a loopback-only web
application (default `http://127.0.0.1:8765/`). The UI groups all terms by Pass 1
category, supports search and reviewed/unreviewed/uncertain/**notes** filters, shows candidate
reasons and source evidence, accepts custom Polish forms, and saves edits atomically
back to `terms.review.json`. Changing a previously reviewed lexical choice reopens that term.
Reviewer notes are independent annotations: they autosave without reopening an approved choice,
and annotated entities can later be browsed with the **Notes** filter.

For people/entities the detail pane now has an **At a glance** summary. Pass-1 observations
from `book_memory.json` are migrated into `terms.review.json` automatically, so gender/reference/
continuity facts can be shown next to the entity. Distinct icons highlight known gender and
coarse entity type (for example human, alien, or other entity) without inventing information.
Existing projects do not need to rerun Pass 1.

The global Confirm button becomes available only after every term is reviewed.
`Save & next` always advances to the next still-unreviewed term in the current category/search
scope, skipping terms already reviewed earlier. The review action bar is permanently pinned to
the bottom of the detail card, so its buttons remain at one screen position while variable-length
entity content scrolls above it. `terms.review.html` remains a read-only snapshot/export; it is
no longer the working UI.

Useful options:

```bash
uv run translate.py review --project "$PROJECT" --review-port 8765
uv run translate.py review --project "$PROJECT" --review-port 0 --no-browser
```

The review server has no authentication and binds to `127.0.0.1` by default. Do not
expose it on an untrusted network.

### Pass 1 lexical-grounding recovery / v1.5

Pass 1 validation now repairs only mechanically provable lexical-grounding failures instead of paying for another full model decode. Unattested aliases are removed; a missing citation may be supplemented only with an exact-match source block from the same analysis unit; a term with no attested source form anywhere in the unit is dropped as an unsupported delta. Existing memory is never erased by these repairs. Completed failed attempts, including attempts in the current fingerprint directory, are reconsidered on restart before any new model call. All repairs are logged in `recovery.json` or `validation_repairs.json`.

### Pass 1 evidence safety / v1.4

Pass 1 now builds a request-specific JSON grammar whose `evidence` fields may contain
only block IDs present in the current source section. This prevents the model from
inventing evidence IDs. If upgrading after an older completed Pass 1 response failed
only because an observation cited an out-of-section ID, `analyze` can recover that
completed response without another model generation: the unsupported observation is
dropped as a whole, the repair is recorded in `recovery.json`, and term evidence
remains strict and is never guessed or rewritten.


This is a new project, not an in-place upgrade of the earlier text-file script.
Use a new project directory. Existing outputs from an older script are not deleted
or silently imported.

## Requirements

- Linux (the project lock uses `fcntl`), Python 3.11 or later, and `uv`.
- A running llama.cpp server reachable over HTTP. Model weights stay on that server.
- HTML, HTM, or XHTML input files. An unpacked EPUB root with its OPF/NCX metadata
  is preferable to a folder containing only isolated HTML files.

`uv run translate.py ...` installs script dependencies into an isolated environment;
it does not install them into system Python. Keep `translate.py`, `bookpipe/`,
`prompts/`, and `settings.default.json` together. No cloud model is called.
Dependencies may need downloading on the first `uv` invocation.

## Quick start

Extract the program archive, change into `intelitex`, and run:

```bash
PROJECT="$HOME/translations/evolutionary-void"

uv run translate.py import /path/to/unpacked-book \
  --project "$PROJECT" \
  --host localhost \
  --port 8080

uv run translate.py analyze --project "$PROJECT"
```

**Analysis ends here. It does not start translation.**

Open the local review application:

```bash
uv run translate.py review --project "$PROJECT"
```

Review the terminology in the browser. The application writes choices atomically to
`terms.review.json`; when every term has been reviewed, press **Confirm glossary**.
Then return to the terminal and run:

```bash
uv run translate.py approve --project "$PROJECT"
uv run translate.py translate --project "$PROJECT" --continue 5
```

Later:

```bash
uv run translate.py translate --project "$PROJECT" --continue 5
uv run translate.py translate --project "$PROJECT" --continue 0
```

`--continue 5` finishes the next five unfinished translation units, including an
interrupted unit. `--continue 0` processes all remaining units. A number greater
than the remaining count also stops at the end. The default is five units.

No repeated input path, manual source cutting, or copying intermediate JSON is
needed. Host and port are saved at import. They may be overridden on `analyze` or
`translate` without repeating the import.

## Exactly what the stages mean

| Stage | Scope | Output / behavior |
|---|---|---|
| Import | Entire folder | Reading order, extracted text, literary sections/scenes and a frozen natural translation-unit manifest. No generation. |
| P1 | Each chapter/section in order | Cumulative lexical proposals, alternatives, evidence and scoped observations. |
| Human review | Entire accumulated terminology | Select a candidate or enter a custom form. |
| Approve / consolidation | Local code, no LLM call | Commit the selected lexicon. This is **not** P2 or a sixth model pass. |
| P2 | One natural translation unit | Semantic / pragmatic / technical audit. |
| P3 | The same unit | Polish draft. |
| P4 | The same unit | Independent correction ledger. |
| P5 | The same unit | Final edited Polish text. |

P1 walks the **whole book** before any translation. Later P1 calls receive a
relevant slice of accumulated memory and a bounded name catalogue. The complete
memory stays in SQLite; it is not rebuilt from scratch or copied wholesale into
every request. Directly matching entries are never silently discarded to meet a
budget. A limited catalogue is explicitly marked incomplete when necessary.

After approval, P2 -> P3 -> P4 -> P5 is executed for one natural translation unit before moving to the
next. Every call gets its own explicit inputs, not a growing chat history. The five
prompts derive from the roles of the supplied Pipeline V2, with compact schemas,
program-assigned source IDs, a human approval gate, and actual checkpointing.
P3/P5 return block-indexed JSON internally so missing/duplicate/reordered blocks
can be detected; their readable text is also saved as TXT.

## Choosing a Polish name

`terms.review.json` contains entries shaped like this (illustrative names):

```json
{
  "id": "T000001",
  "source": "Example term",
  "candidates": [
    {"number": 1, "text": "Candidate A", "reasons": ["Reason A"], "evidence": ["B0000001"]},
    {"number": 2, "text": "Candidate B", "reasons": ["Reason B"], "evidence": ["B0000150"]}
  ],
  "select": 2,
  "custom": "",
  "reviewed": true,
  "user_notes": "Prefer the established Polish edition form.",
  "observations": [
    {"kind": "gender", "statement": "The character is female.", "confidence": "high", "evidence": ["B0000001"]}
  ]
}
```

The review application edits `select` (a **1-based** candidate number), `custom`, `reviewed`,
and `user_notes`. A nonempty custom value takes precedence. `observations` are read-only Pass-1
facts copied from book memory for quick entity inspection. The real file also contains meaning
notes, uncertainty, and source evidence. The application sets top-level
`confirmed: true` only after every term is reviewed. Manual JSON editing remains
possible, but the web UI is the normal workflow.

To deliberately accept every currently selected/default candidate without editing
that confirmation field:

```bash
uv run translate.py approve --project "$PROJECT" --accept-defaults
```

Each P1 response proposes up to three candidates per term, but the persistent
candidate history is **not** truncated to three. New proposals from later chapters
are retained. Selection does not delete alternatives or their evidence. In normal
translation requests only the approved choice and relevant, temporally available
meaning notes are supplied, rather than inviting the model to choose again.

Model confidence is not calibrated probability. An explicit definition can establish
a concept but does not prove that the model has found the published Polish name.
The human choice is therefore the final lexical decision in this version.

## Chapters, sections, scenes, and HTML extraction

- With a package file, import follows the **OPF spine**, not filename order.
- NCX / EPUB navigation anchors and `h1` / `h2` headings create major section boundaries.
- For this Calibre-converted edition, `calibre27` is also recognized as a named major
  section marker; this catches the embedded `Inigo's Twenty-first Dream` inside the
  HTML file that starts with chapter THREE.
- `calibre19`, `calibre21`, `calibre23`, image-only divider paragraphs, and `<hr>`
  provide natural scene-start hints. These hints are persisted in `book.json`.
- Without OPF, files use natural sorting (`2` before `10`) and import emits a warning.
- `--chapter-mode file` deliberately disables internal section splitting and keeps one
  section per nonempty HTML document.
- `--chapter-mode headings` uses heading/known-major-section boundaries instead of TOC anchors.
- `--chapter-selector 'p.chapter'` adds another CSS selector for nonstandard major headings.
- `--opf /path/to/content.opf` selects a package when several exist.
- `--include '*split_*.html'` restricts imported paths, preserving their resolved order.

Short front matter matching the OPF book title and `ABOUT THE AUTHOR` back matter are
kept under `non_narrative_sections` / `matter/` for inspection but are not sent to P1
or translation. The narrative-section numbering is therefore independent of HTML
filenames.

Inspect the import summary and `book.json` before long analysis. Automatic structure
recovery cannot guarantee arbitrary publisher-specific CSS conventions. The source/unit
plan is frozen. If the imported structure is wrong, create a new project before model
analysis; do not edit source blocks or unit IDs in place.

Scripts, styles, document heads, navigation blocks and explicitly hidden content are
removed. Inline text is concatenated without inserting spaces between spans inside a
word, so small-cap/drop-cap markup does not produce `T HE`-style corruption. HTML
source line wrapping is collapsed while explicit `<br>` is preserved. Emphasis is
represented with `*...*` / `**...**`. Image ALT text is **not** copied into prose;
image-only divider paragraphs are structural scene markers. The importer does not render
CSS, reconstruct image-only text, perform OCR, or remove DRM. Complex tables, poetry,
notes and CSS-only structure still need inspection.

`extracted/` contains one UTF-8 TXT per input HTML path. Original HTML files are
not modified. Add `--sidecar-txt` at import to also write `.txt` beside the HTML;
a different existing sidecar is never overwritten.

## Natural translation units and context budgets

Translation no longer uses arbitrary 1200/1600-token chunks. The default structural
policy is:

```text
section <= 10,000 visible-text characters
    -> translate the whole section as one unit

section > 10,000 characters
    -> one translation unit per detected natural scene

individual scene
    -> never split merely because it exceeds 10,000 characters
```

This intentionally keeps compact multi-scene sections such as `Inigo's Last Dream`
together, while a large ordinary chapter is divided at its authored scene boundaries.
A 30K-character scene remains one unit. Change the whole-section threshold at import
with `--whole-section-limit N`. The exact frozen plan, scene IDs, character/word counts,
and model tokenizer counts are stored in `book.json`.

P1 is different: it normally receives a whole major section. Its budget is derived from
the server context capacity minus memory/output reserves. Only an unusually huge section
or single paragraph is split for P1. `analysis_source_limit: 0` means no extra arbitrary
source cap. `analysis_plan.json` records the resulting P1 work units.

Every generation is preflighted using the rendered-input token endpoint, or an
older-server template/tokenizer fallback with a safety margin. Input plus output reserve
must fit. No silent context truncation is requested. Default P3/P5 output reserves are
16K tokens so the tested ~9K-token natural scenes can produce full Polish output without
a 5K generation ceiling.

## Reading lines / time lines

Continuity defaults to the preceding completed chunk **within the same chapter**.
It does not inject the previous unrelated chapter merely because it is adjacent
in file order. `thread_id` in a chapter entry in `book.json` may be set explicitly
to share continuity between known related chapters. The program does not invent
storyline links. Chapter display titles and thread tags may be edited; frozen source
text and chunk structure may not.

All lexical choices can be reviewed using the whole book. Factual observations and
meaning notes are filtered by their cited source order; observations are also
chapter-local. This reduces future-knowledge leakage but cannot guarantee complete
spoiler safety if the model misattributes evidence or a human-selected name itself
reveals a later identity. Keep analysis artifacts private while reading a new book.

## Checkpoints and interruption

Every successful call writes:

- exact model request and inputs;
- streamed output events;
- separate partial/final answer and reasoning text when the server separates it;
- response metadata, including available token usage;
- validated JSON and, for P3/P5, readable TXT;
- a SQLite checkpoint only after valid complete output is on disk.

A response terminated by a token limit, connection loss or Ctrl+C is **not** marked
complete. Rerun the same command. Prior successful passes are reused. The interrupted
request itself restarts; this program does not restore an in-flight CUDA decoding
state. This is the only work that may need repeating.

SQLite transactions, atomic file replacement, filesystem sync, checksums and a
project lock protect normal checkpoint operations. Do not run two processes on the
same project. Keep the project on a local filesystem with reliable locking, and
back it up. Hardware/filesystem failure still requires backups.

Changing model parameters affects future calls, not already accepted results. Exact
request parameters are saved with each attempt. Prompt/input changes create a new
versioned artifact instead of overwriting old results. For controlled comparisons
between models/prompts, use separate projects.

## Changing a term after translation

Edit `terms.review.json` and run `approve` again. The old/new choices are logged,
all candidates remain, and a SQLite backup is taken before approval.

Previously completed chunks whose tracked lexical dependencies include the changed
term are marked `stale`. Their old translations remain on disk and visible until
replacement. The next `translate --continue N` includes these stale chunks. Only
affected chunks are recomputed through P2-P5; the whole book is not restarted.

This implementation uses context-aware retranslation of affected chunks rather
than a global text replacement. Selection is based on source term identities and
attested aliases, not every accidental occurrence of a Polish substring. It cannot
guarantee every unrecorded alias is detected, so inspect terminology-sensitive
changes. An update can also affect grammatical agreement across a chunk boundary;
this version does not automatically propagate all such stylistic dependencies.

## Progress display

The terminal shows:

1. Global analysis-section or completed-book-unit progress.
2. Current narrative section, local translation unit, and progress through P2-P5.
3. Current pass, waiting/receiving state, and received answer/reasoning **characters**.

There is no elapsed timer or ETA in the UI. Incoming stream events are not falsely
reported as exact generated tokens. Server token usage is saved when available.
Noninteractive logs print stage transitions instead of animated bars. Prefill is
shown as waiting; the server may not emit textual output during it.

## Settings and bounded generation

`settings.json` in the project controls model address, per-pass temperature/output
limits, continuity budget, and analysis-memory budget. `prompts/pass1.txt` through
`pass5.txt` are editable project-local prompt files, not hidden constants in the
Python orchestration code.

Default thinking is **off**: analysis is explicit JSON work, not unbounded hidden
deliberation. To test thinking only in P1/P2/P4:

```bash
uv run translate.py analyze --project "$PROJECT" --thinking analysis
uv run translate.py translate --project "$PROJECT" --thinking analysis --continue 5
```

The model/template must support `enable_thinking`. The code does not pretend a
phrase such as "low reasoning budget" enforces a limit. `max_tokens` is a real total
output limit, and `request_timeout` is a wall deadline. If thinking consumes the
budget without a final answer, the request fails visibly and its partial output
remains saved. Increase the relevant output limit in `settings.json` or retry with
thinking off. Defaults are test starting points, not validated optimal settings.

A nonempty `LLAMA_API_KEY` environment variable supplies the Bearer token. Secrets
are not written to request artifacts. Proxy environment variables are ignored so
local book content is not accidentally sent through a proxy.

## Output encoding

All internal text, prompts, JSON, extracted source and translations use **UTF-8**.
Polish letters, smart quotes, dashes and scientific symbols are preserved. There is
no `errors="ignore"` conversion.

For a legacy consumer, explicitly export a separate ISO-8859-2 copy:

```bash
uv run translate.py export --project "$PROJECT" \
  --encoding iso-8859-2 --output "$PROJECT/exports/translation-latin2.txt"
```

Conversion is strict and may reject smart punctuation or symbols absent from that
encoding. UTF-8 originals remain unchanged. Internal project files are protected
from being overwritten by additional exports.

## Files to inspect

```text
project/
  book.json                    # frozen sources, sections, chunks, stable IDs
  analysis_plan.json           # P1 work units
  settings.json
  state.sqlite3                # authoritative checkpoints, memory, choices, history
  book_memory.json              # readable memory snapshot
  terms.review.json             # source of truth: choices, per-term review state, confirmation
  terms.review.html             # optional read-only snapshot/catalogue
  lexicon.approved.json
  translation.txt              # growing contiguous completed prefix
  translation.status.json      # pending/done/stale status
  extracted/                   # per-HTML source text
  chapters/                    # per-chapter source text
  translated_chapters/          # per-chapter available translations
  prompts/                     # five individual prompts
  analysis_inputs/              # frozen P1 input snapshots for resumption
  artifacts/pass1/...           # every request/result/attempt
  artifacts/pass2/...           # likewise for P2-P5
  history/                     # review snapshots and pre-approval DB backups
```

`translation.txt` is rebuilt from completed block artifacts; the program never
blindly appends duplicate text on resumption. It exposes only a contiguous readable
prefix. `status` and `export` work without a running LLM.

```bash
uv run translate.py status --project "$PROJECT"
uv run translate.py export --project "$PROJECT"
```

## Tests and limitations

Run:

```bash
uv run --group dev python -m pytest -q
```

The included tests use a local mock HTTP/SSE server. They test orchestration, not
translation quality: HTML/Unicode extraction, OPF order and NCX anchors, lossless
splitting, stop-before-approval, repeated analysis, candidate selection, incremental
continuation, interrupted P4 resumption without repeating P2/P3, bounded invalid-JSON
retries, source-ID coverage, candidate preservation, stale chunks after a custom
choice, no default cross-story continuity, artifact checksums and manifest integrity.

No full-book run or generation against the user's actual Gemma/CUDA installation
has been performed in the authoring environment. Structural validation detects
malformed/missing blocks and references, not every semantic mistranslation. P4 is a
model audit, not a guarantee of fidelity. Keep the first run small and inspect its
source, draft, correction ledger and final output before running the entire book.

There is no final EPUB reconstruction in this version; outputs are UTF-8 TXT and
structured artifacts. This is a translation/review harness, not yet an ebook reader.

## Reference documents

The implementation uses the llama.cpp server's documented chat, JSON response,
tokenizer and streaming endpoints. EPUB structure follows package spine order where
available. Relevant primary references:

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://www.w3.org/TR/epub-33/

The five translation roles are based on the Pipeline V2 supplied in the conversation.
The compact schemas, source IDs, checkpoints, approval gate and deterministic merge
are implementation changes, not claims that the original prompts guarantee them.

## v1.2 analysis planner fix

Pass 1 planning now reuses each narrative section's `source_tokens` count saved during import. A section that already fits the P1 source budget is scheduled immediately as one analysis unit, without re-tokenizing every paragraph through llama.cpp. Block-level `/tokenize` calls remain only as a fallback for an exceptionally large section or an older manifest without cached section token counts. Existing v1.1 projects do not need to be re-imported.
