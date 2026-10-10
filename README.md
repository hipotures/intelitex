# Intelitex

**Intelitex** is a local workflow for translating a book with a configured LLM. It imports an EPUB (packed,
or unpacked together with its HTML files) once, analyzes the entire book, **stops for human terminology
approval**, then translates the next natural translation units through four more passes and publishes a
translated EPUB. Completed model calls survive interruption and are not repeated merely because you start
another session.

It runs in a browser (`uv run intelitex serve`: a React interface served by the Python server) and from the
command line (`uv run intelitex …`). Both operate on the same project directories. Models come from profiles:
llama.cpp, vLLM, the OpenAI Responses API or Codex app-server. The language pair currently offered in the
web setup is English → Polish.

See the [screenshots](#screenshots), the [quick start](#quick-start) and the [web interface](#web-interface).

## Screenshots

The web interface (`uv run intelitex serve`, see [web development](web/README.md)) on a real workspace:
*The Time Machine* by H. G. Wells, a public-domain Project Gutenberg book, mid-translation. The Library
is filtered to Project Gutenberg books (28 public-domain demo books). Interface labels are English; chapter
titles and the book title in the Reader follow the translation.

### Work

| Books in progress | Library |
|:--:|:--:|
| <img src="docs/images/work-top.png" width="440" alt="Work page: active workspaces"> | <img src="docs/images/work-bottom.png" width="440" alt="Work page scrolled to the Library"> |

### Workspace and phases

| Workspace overview | Section preview with model overrides |
|:--:|:--:|
| <img src="docs/images/workspace-overview.png" width="440" alt="Workspace overview"> | <img src="docs/images/workspace-section-preview.png" width="440" alt="Section preview drawer"> |

| Prepare | Analyse · P1 |
|:--:|:--:|
| <img src="docs/images/phase-prepare.png" width="440" alt="Prepare phase"> | <img src="docs/images/phase-analysis.png" width="440" alt="Analyse phase"> |

| Review | Translate · P2–P5 |
|:--:|:--:|
| <img src="docs/images/review-terminology.png" width="440" alt="Terminology review"> | <img src="docs/images/phase-translate.png" width="440" alt="Translate phase"> |

| Publish | Settings · Models |
|:--:|:--:|
| <img src="docs/images/phase-publish.png" width="440" alt="Publish phase"> | <img src="docs/images/settings-models.png" width="440" alt="Settings dialog, Models tab"> |

### Reader

The translated book, in the three themes and with the reading settings open:

| Reader (dark) | Reader (light) |
|:--:|:--:|
| <img src="docs/images/reader-dark.png" width="440" alt="Reader, dark theme"> | <img src="docs/images/reader-light.png" width="440" alt="Reader, light theme"> |

| Reader (sepia) | Reader settings |
|:--:|:--:|
| <img src="docs/images/reader-sepia.png" width="440" alt="Reader, sepia theme"> | <img src="docs/images/reader-settings.png" width="440" alt="Reader with the reading settings panel open"> |

### Updating the screenshots

The images are generated, not drawn: `web/scripts/screenshots.mjs` opens the pages in Chromium and writes
`docs/images/*.png` (1440 px wide at 2x density, so 2880 px files; full pages are 3280 px tall). Re-run it
after a UI change and commit the result:

```bash
npm --prefix web ci && npm --prefix web run build          # the server serves web/dist
SCREENSHOT_URL=http://HOST:PORT/work/workspaces/<workspace id> \
  npm --prefix web run screenshots                         # all shots, from a running server
SCREENSHOT_URL=... npm --prefix web run screenshots -- reader   # only shots whose name contains "reader"
```

Against a running server (`SCREENSHOT_URL`) the script only reads: every request other than GET is blocked.
In its own throwaway browser profile it expands the collapsed panels (Pipeline models, Live activity), turns on
the Project Gutenberg Library filter, selects the first Full (F) section on the Translate and Publish pages
instead of the translate-only Contents, scrolls the lower Work shot until the Library has loaded, and embeds the
Inter font so the result does not depend on installed fonts. Everything is dark except the Reader, which is
shown in dark, light and sepia. Without `SCREENSHOT_URL` it drives a disposable offline fixture server with
demo data.

The Work page lists every active workspace on the server, so run the script against a server whose workspaces
you are happy to publish. Add or change a shot by editing `LIVE_SHOTS` in the script. If Playwright's browser
is not installed, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to a system Chromium.

## Requirements

- Linux (the project lock uses `fcntl`), Python 3.11 or later, and `uv`.
- Node.js 22.12 or later, only to build and test the web interface (`web/`); the browser tests and the
  screenshot script also need a Chromium (Playwright's or a system one).
- A configured inference profile: a reachable llama.cpp or vLLM server, an explicitly configured OpenAI
  credential, or an isolated Codex app-server authentication source.
- A source book: a packed `.epub`, or an unpacked EPUB root with its OPF/NCX metadata (preferable to a folder
  containing only isolated HTML files). HTML, HTM and XHTML input files are supported.

`uv run intelitex ...` uses the dependencies declared in `pyproject.toml` and the project's isolated
environment; it does not install them into system Python. `translate.py` remains a backwards-compatible
launcher in that same environment. Keep `bookpipe/`, `prompts/`, `catalog/`, `settings.default.json` and the
built `web/dist` together. Cloud providers are called only when the user selects and configures such a profile.
Dependencies may need downloading on the first `uv` or `npm` invocation.

This is a new project, not an in-place upgrade of the earlier text-file script. Use a new project directory:
outputs of an older script are not deleted or silently imported.

## Quick start

### In the browser

```bash
npm --prefix web ci && npm --prefix web run build      # builds web/dist, which the server serves
uv run intelitex serve --workspace-root "$HOME/translations" --import-root "$HOME/books"
```

Open `http://127.0.0.1:8780/work`. Put the books to translate under `--import-root` (packed `.epub` files or
unpacked EPUB/HTML folders); the server reads sources only from there. Each child directory of
`--workspace-root` is a workspace.

1. In **Work → Library**, open a book and choose **Add to workspace**. Pick a label, the language pair and a
   model profile for each pass, then save. This creates a draft and does not touch the book.
2. Open the workspace and run **Prepare**: it imports the book into the workspace, freezes its structure and
   stops. Check the sections and their processing modes (F, T, E).
3. **Run** the analysis (P1). It reads the whole book and stops at the terminology gate.
4. Open **Review**, choose or enter a Polish form for each term, then **Approve glossary**.
5. **Run** again to translate the book unit by unit (P2–P5). Read the finished part in the **Reader** while the
   rest continues. When the last unit is done, the translated EPUB is published automatically (**Publish**
   retries it).

### On the command line

From the repository root, run:

```bash
PROJECT="$HOME/translations/the-time-machine"

uv run intelitex import /path/to/unpacked-book \
  --project "$PROJECT" \
  --host localhost \
  --port 8080

uv run intelitex analyze --project "$PROJECT"
```

**Analysis ends here. It does not start translation.**

Open the local review application:

```bash
uv run intelitex review --project "$PROJECT"
```

Review the terminology in the browser. The application writes choices atomically to
`terms.review.json`; when every term has been reviewed, press **Confirm glossary**.
Then return to the terminal and run:

```bash
uv run intelitex approve --project "$PROJECT"
uv run intelitex translate --project "$PROJECT" --continue 5
```

Later:

```bash
uv run intelitex translate --project "$PROJECT" --continue 5
uv run intelitex translate --project "$PROJECT" --continue 0
```

`--continue 5` finishes the next five unfinished translation units, including an
interrupted unit. `--continue 0` processes all remaining units. A number greater
than the remaining count also stops at the end. The default is five units.

When the last pending or stale unit reaches a valid P5 checkpoint, Intelitex
automatically publishes the current translated EPUB. If package construction or
validation fails, the completed translation stays committed and publication can
be retried without a model call:

```bash
uv run intelitex publish --project "$PROJECT"
```

Publishing requires a real unpacked EPUB package with `mimetype`,
`META-INF/container.xml`, and the imported OPF. A generic HTML-directory project
continues to support analysis, translation, text export, and Reader, but cannot be
faithfully published as EPUB by this version.

No repeated input path, manual source cutting, or copying intermediate JSON is
needed. Host and port are saved at import. They may be overridden on `analyze` or
`translate` without repeating the import.

### Continue a series

To initialize a later volume with memory from the immediately preceding approved
Intelitex project, add `--previous-volume` to its otherwise normal import:

```bash
uv run intelitex import /path/to/unpacked-volume-2 \
  --project "$HOME/translations/series-volume-2" \
  --previous-volume "$HOME/translations/series-volume-1"
```

The first legacy predecessor is atomically given only a `series.json` sidecar as
volume 1; its frozen `book.json`, database, prompts, checkpoints and translations
are not rewritten. Each continuation automatically uses the same opaque series ID
and the next volume number. Intelitex deterministically compacts the predecessor's
canonical approved terminology and reusable P1 observations into
`series.seed.json` without an LLM call, then seeds the new Store before its first
analysis request. The predecessor must have completed P1 and terminology approval
(`book_memory.json` plus a consistent `lexicon.approved.json`), but its prose
translation need not be complete.

Inherited choices are already selected and reviewed in the next volume. A recurring
term is reopened for review only when current P1 prefers a different Polish form or
changes its category; new terms follow the ordinary review flow. The new book still
requires its own `analyze -> review -> approve -> translate` cycle. Imports without
`--previous-volume` remain standalone and create no series artifacts.

Continuation imports also inherit the predecessor's `settings.json`, optional
project-local `catalog/models.json`, and all `prompts/*.txt` (including the five
required pass prompts). Configuration is validated and saved before provider
discovery, so tuned models, reasoning efforts, pass assignments, budgets and
prompt text carry forward automatically. Missing or invalid inherited settings,
catalogs or required prompts stop the import.

Precedence is bundled defaults, then predecessor settings, then explicit CLI
overrides. As before, `--profile` and `--pass-profile` select profiles for this
command only. Explicit model/context overrides update the selected import profile;
host/port/thinking overrides apply to that profile when it uses llama.cpp.
An omitted `--whole-section-limit` keeps the predecessor's value;
standalone imports still default to 10000. A continuation destination that already
contains `settings.json`, `catalog/models.json`, or a `prompts/` directory is
rejected without overwriting those files. Use a fresh project directory, CLI
overrides, or edit the new project's configuration after import. After an
interrupted import that has materialized configuration, use a fresh destination.

Only known Codex path fields are remapped: `runtime_root`, `executable`, and
`options.auth_source`. Project/source-local runtime paths move to the corresponding
new project/source tree; local executables require an already provisioned executable
at the mapped destination. No runtime directory or executable is copied. Shared
external paths stay unchanged, and a bare executable name such as `codex` keeps
its `PATH` lookup. Other relative paths are rejected because the original working
directory is unknown; configure absolute paths before continuation. Environment
credential references are inherited, but literal credentials are rejected and
project/source-local `auth_source` files are never copied: configure a shared
external authentication path in the predecessor first.

## Web interface

`uv run intelitex serve` hosts the interface at `/work` (workspaces and Library), `/reader` (translated text)
and the Settings dialog (gear icon). Interface labels are English; the book text, chapter titles and an
optional book title in the Reader follow the translation.

- **Work.** *Active workspaces* lists every workspace with its stage, progress and a **Run**/**Open** action;
  the *Archive* drawer holds parked ones. Double-click a workspace's label to rename it. *Library* lists the
  source books under `--import-root` (packed EPUBs and book folders), grouped by directory as authors, series
  and categories ([Library hierarchy](docs/library-groups.md)), with sorting by author, title or language and
  a **Refresh** button. Every card has the same size: the source language is a badge on the cover's top-left
  corner, the title takes up to three lines (the full title is in the tooltip) and the author is always shown
  in full at the bottom; a count of active workspaces sits on the cover's bottom edge. A card opens the source
  details (**Inspect source**, **Add to workspace**). The optional **Project Gutenberg** checkbox (turn it on in
  Settings → Library) lists only books whose EPUB metadata names gutenberg.org as the source.
- **Workspace.** The phase rail runs Prepare → Preload (P0) → Analyse (P1) → Review → Translate → Publish.
  The *Sections* table shows each section's processing mode and the P0–P5 state; a section opens a preview
  drawer with optional per-section model overrides. *Pipeline models* assigns a profile to each pass and
  *Live activity* streams pipeline events and model waits.
- **Phase pages.** *Prepare*: source structure with the F/T/E processing choice per section (F: full P1–P5,
  T: translate only, E: excluded), a source preview, an optional **translated title**, source metadata
  ([book identity and corrections](docs/book-metadata.md)) and checks. *Preload (P0)* applies to workspaces
  that use persistent Codex source sessions; the others show it as not required. *Analyse*: P1 units with
  their terms and observations, and token and cost counters. *Translate*: per-chunk P2–P5 state, with a preview
  of the saved result and buttons to run or re-run a single pass. *Publish*: choose which sections go into the
  EPUB, preview the book text and see the validation result.
- **Review.** The terminology review described [below](#terminology-review): filters, evidence, custom forms,
  notes, bulk accept and the glossary approval.
- **Reader.** Checkpoint-verified translated text, chapter by chapter, with prose markers; see
  [Reader and prose markers](#reader-and-prose-markers).
- **Settings.** *Paths* (server configuration), *Models* (which profiles appear in model lists), *Library*
  (the Project Gutenberg filter), *Interface* (dark, light or sepia theme; reset browser preferences) and
  *Debug* (panel identifiers, listed in [debug IDs](docs/web/debug-ids.md)). The choices are stored in the
  browser; *Paths* only shows the server's configuration.

Build, development and test instructions: [web development](web/README.md). Differences from the visual
mockup: [intentional differences](docs/web/differences.md).

## Server

`uv run intelitex serve --workspace-root DIR [--import-root DIR] [--bind HOST] [--port N]` runs one local HTTP
server (default `127.0.0.1:8780`) that serves the web interface, a JSON/SSE API and vetted EPUB downloads, and
supervises independent background worker processes. Different workspaces may analyze or translate
concurrently; each workspace has at most one active mutating job. Browser navigation, refresh and disconnects
do not stop workers, status and publication queries stay available during translation, and the final
translation still publishes its EPUB automatically. Without `--import-root` the Library is not configured.

The server has no authentication. It binds to loopback by default and checks the Host, Origin and Fetch
Metadata headers, but binding to another address (for a phone on a trusted LAN, say) lets anything that can
reach the port edit your projects: do not expose it on an untrusted network.

Runtime jobs live in a separate XDG state database; restarting the server marks unfinished records
`abandoned` and does not re-adopt old workers. `uv run intelitex reload` asks a running server to reload once
active translation passes have checkpointed. Legacy state is migrated only on request with `migrate-state`
and `migrate-jobs` ([state](docs/state-json-migration.md), [jobs](docs/job-json-migration.md)).
See [server runtime and API](docs/server-runtime.md) and [endpoints](docs/server-api.md) for cancellation,
checkpoint ownership, security and restart behavior.

## Exactly what the stages mean

| Stage | Scope | Output / behavior |
|---|---|---|
| Import | Entire folder | Reading order, extracted text, literary sections/scenes and a frozen natural translation-unit manifest. No generation. |
| Preload (P0) | Each analysis unit or chapter | Only with persistent Codex source sessions (`source-session-v1`, see below): loads the immutable source into a private session; its answer is a readiness acknowledgement, not book content. |
| P1 | Each chapter/section in order | Cumulative lexical proposals, alternatives, evidence and scoped observations. |
| Human review | Entire accumulated terminology | Select a candidate or enter a custom form. |
| Approve / consolidation | Local code, no LLM call | Commit the selected lexicon. This is **not** P2 or a sixth model pass. |
| P2 | One natural translation unit | Semantic / pragmatic / technical audit. |
| P3 | The same unit | Polish draft. |
| P4 | The same unit | Independent correction ledger. |
| P5 | The same unit | Final edited Polish text. |
| Publish | Completed, current translation | Validate and atomically build the final EPUB. No LLM call. |

P1 walks the **whole book** before any translation. Later P1 calls receive a
relevant slice of accumulated memory and a bounded name catalogue. The complete
memory stays in the JSON checkpoint state; it is not regenerated or copied wholesale into
every request. Directly matching entries are never silently discarded to meet a
budget. A limited catalogue is explicitly marked incomplete when necessary.

After approval, P2 -> P3 -> P4 -> P5 is executed for one natural translation unit before moving to the
next. With llama.cpp, vLLM and OpenAI every call gets its own explicit inputs, not a growing chat history; Codex uses persistent source sessions or paired conversations as described below. The five
prompts derive from the roles of the supplied Pipeline V2, with compact schemas,
program-assigned source IDs, a human approval gate, and actual checkpointing.
P3/P5 return block-indexed JSON internally so missing/duplicate/reordered blocks
can be detected; their readable text is also saved as TXT.

## Persistent P0-P5 source sessions

New workspaces select `pipeline_execution.codex_mode: source-session-v1`.
When a selected Codex pass has no accepted checkpoint, existing Analyze and
Translate Start operations prepare P0 internally. P0 binds only immutable source
and a structured readiness acknowledgement. Each P1-P5 turn sends its active
instructions, references and required canonical dependencies, persists its outcome,
and returns native history to P0. P1 retains ordered memory and the existing
book-wide review gate; P3 depends on P2, P4 on P2/P3, and P5 on P3/P4.

A compatible source scope owns one private persistent Codex home, SQLite directory,
working directory and native thread. Cold resumes preserve that binding. Private
runtime state lives outside the project and checkout; restore the complete runtime
including SQLite/WAL for recovery. Copy authentication only with explicit
permission. A missing runtime, ambiguous submission, live foreign turn or changed
source history stops inference with recovery diagnostics. Provider prompt-cache
hits are independent of source persistence; zero cached tokens are valid.

Existing projects without `pipeline_execution` remain on their legacy transport.
New continuation volumes select the new mode and copy P0 from the bundle when
the predecessor is legacy, without migrating that predecessor.
Opting in requires copying `prompts/pass0.txt` and adding the contract below to
project settings. Successful analysis, review and translation checkpoints remain
valid; P0 is prepared only for missing requested work. Other providers keep complete
standalone source requests.

```json
"pipeline_execution": {
  "version": 1,
  "codex_mode": "source-session-v1",
  "source_scope_policy": "exact-existing-unit",
  "revert_policy": "after-each-attempt",
  "p0_output_reserve": 256
}
```

Inspect slots without inference using
`uv run intelitex source-sessions --project "$PROJECT"`. Explicit maintenance uses
`--action archive|rebuild|purge --slot SLOT_ID`; rebuild retires the slot and lazily
allocates a new generation, while purge requires prior retirement and preserves
ordinary results and attempt evidence. P1 reset and review changes preserve P0.
New-mode attempts are chapter-first; legacy pass-first attempts remain readable.
Usage reports count P0 separately, retain archived P1 consumption, and include
submitted failures, with missing
usage marked unknown. Full retained context is checked before any source preload.

See [architecture](docs/architecture.md), [validation report](docs/validation-p0-p5-persistent-sessions.md)
and [measured live evidence](docs/validation-p0-p5-live.json).

## Terminology review

Human terminology decisions live in one file, `terms.review.json`. The web **Review** page and the standalone
`intelitex review` command edit that same file; nothing is translated until the glossary is approved.

### Review application

`terms.review.json` is the single source of truth for human terminology decisions.
Run `uv run intelitex review --project "$PROJECT"` to start a loopback-only web
application (default `http://127.0.0.1:8765/`). The UI groups all terms by Pass 1
category, supports search and reviewed/unreviewed/uncertain/**notes** filters, shows candidate
reasons and source evidence, accepts custom Polish forms, and saves edits atomically
back to `terms.review.json`. Changing a previously reviewed lexical choice reopens that term.
Reviewer notes are independent annotations: they autosave without reopening an approved choice,
and annotated entities can later be browsed with the **Notes** filter.

For people/entities the detail pane has an **At a glance** summary. Pass-1 observations
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
uv run intelitex review --project "$PROJECT" --review-port 8765
uv run intelitex review --project "$PROJECT" --review-port 0 --no-browser
```

The review server has no authentication and binds to `127.0.0.1` by default. Do not
expose it on an untrusted network.

### Scoped counters, filters and bulk accept

The review category chips show a **single matching-term count**, not a remaining/matching fraction. For example, under `Uncertain`, `✓ Status & social 1` means one uncertain term matches that category and all of it has been reviewed. Green always means that every term matching the current status/search/category scope is reviewed. The header line separately reports reviewed and remaining counts.

The two filter rows intersect. Category badges are calculated from the current
status filter and search; status-filter counts are calculated from the selected
category and search. Each badge shows the number of matching terms, while its color
indicates whether that scope is fully reviewed. Switching to All recalculates both
the count and completion color.

Green means the *matching scope* is fully reviewed, not that its entire category
is finished under every filter. A genuinely empty scope stays gray. Under Unreviewed,
an empty queue is green only if its nonempty category/search parent is completed.
The Accepted filter includes individual, bulk, inherited and legacy entries.
Uncertain records remain uncertain after review: model confidence is not overwritten
by the human decision. Notes remain available for a later contextual check.

**Bulk accept remaining in this view (N)** marks only pending records in the
intersection of status, category and search. It asks for confirmation with the exact
count and scope, preserves each current selected/custom form and all notes, and
backs up the review JSON under `history/review_before_bulk_<hash>.json` before one
atomic write. It does not choose candidate 1 again, approve hidden categories, call
a model or globally confirm the glossary. Selecting All + All first deliberately
expands this operation to all remaining records. Bulk-accepted records are tagged
with `review_method: "bulk"`; individual decisions use `"individual"`. The web
Review screen reports those methods separately, including inherited and unknown
legacy methods. Glossary approval commits selected forms without changing a term's
review method or claiming individual review of bulk-accepted terms.

Browser writes are serialized. Revision tokens reject stale-tab overwrites instead
of silently replacing another tab's edits. The token is API metadata and is not
persisted into the review file. Pending custom forms and notes are flushed before
changing filters or selecting another entity. The footer remains at the bottom of
the detail pane, and the shortcut label reads `Ctrl+Enter`.

### Reviewing a provisional choice in translated context

A practical workflow is to keep a provisional lexical choice, add a reviewer note
such as `Check this term in the Polish context`, and mark the choice reviewed.
After the remaining choices are reviewed, confirm the glossary and run:

```bash
uv run intelitex approve --project "$PROJECT"
uv run intelitex translate --project "$PROJECT" --continue 5
uv run intelitex review --project "$PROJECT" --review-port 9000
```

Stop the review server before invoking approve or translate: the existing CLI
project lock intentionally allows only one such command at a time.

The evidence pane shows **English original / Polish P5** side by side when a
committed final translation exists. On narrower screens these columns stack.
It reads the immutable source blocks from `book.json` and the registered final paths
from JSON checkpoint state through a separate **read-only snapshot**. It checks artifact
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
search-and-replace or a new paragraph-only AI correction pass.

### Choosing a Polish name

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
uv run intelitex approve --project "$PROJECT" --accept-defaults
```

Each P1 response proposes up to three candidates per term, but the persistent
candidate history is **not** truncated to three. New proposals from later chapters
are retained. Selection does not delete alternatives or their evidence. In normal
translation requests only the approved choice and relevant, temporally available
meaning notes are supplied, rather than inviting the model to choose again.

Model confidence is not calibrated probability. An explicit definition can establish
a concept but does not prove that the model has found the published Polish name.
The human choice is therefore the final lexical decision in this version.

### Changing a term after translation

Edit `terms.review.json` and run `approve` again. The old/new choices are logged,
all candidates remain, and a JSON state backup is taken before approval.

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

## Reader and prose markers

After P5 translation units have completed, open the Reader (**Reader** in the web interface, or the standalone command below) to read the
checkpoint-verified Polish text chapter by chapter and quickly mark a suspicious
word or span without interrupting the reading flow:

```bash
uv run intelitex reader --project "$PROJECT"
```

The Reader defaults to `http://127.0.0.1:8766/` and opens the browser. It may run
while `translate` continues: project manifests, JSON checkpoints, and P5 artifacts
are read-only in the Reader, while marker writes are isolated in
`translation.review.json` under a separate Reader lock. A second Reader process for
the same project is rejected. Use `--reader-port 0` to select a free port or
`--no-browser` to suppress browser launch. For a phone on a trusted LAN, bind
explicitly beyond loopback, for example:

```bash
uv run intelitex reader --project "$PROJECT" --bind 0.0.0.0
```

This is an unauthenticated local editing service. A non-loopback bind makes it
reachable to hosts allowed by the machine and network firewall; do not expose it
on an untrusted network.

Reader markers are written atomically to `translation.review.json`. Format 1
records the source-book fingerprint and a minimal list of marker IDs, canonical
chapter/block IDs, Unicode code-point offsets, and the exact captured Polish
substring. Reader appearance, gesture choices, and reading position remain in the
browser's `localStorage`. Marker and Context Helper gestures are assigned
independently; the defaults are horizontal drag for a marker and long press for
context. Assigning an occupied gesture swaps the two assignments, and either action
may be turned off.

The Reader header is a fixed overlay, so hiding it never moves the prose or changes
the saved reading position. `Header auto-hide` may be Off (the default) or 5, 10,
or 15 seconds; open header panels suspend the timer. When hidden, the thin progress
line remains at the physical top edge: a single tap there opens progress details,
while a double tap within 300 ms reveals the header without first opening progress.
The end of each readable chapter links to the next chapter in the currently
available translated prefix, or reports the end of the available translation.

The read-only Context Helper recognizes entities from `lexicon.approved.json` and
then joins the resolved term ID to contextual knowledge in `book_memory.json`. A
gesture supplies a position in one checkpoint-verified P5 block; only that block is
checked for the longest approved source, Polish, or safely established alias form.
Exact normalized matches win; a small Polish inflection matcher permits only
diacritic folding and recognized case/number endings while preserving token order,
phrase width, and exact distinctive-name tokens. Inflected candidates must be backed
by term evidence from the current or an earlier safe block. Ordinary prose and
ambiguous records are not recognized. A recognized entity still opens the card when
no earlier context exists.

For a later occurrence in the same block, only complete Polish P5 sentences from the
prefix before the touched code-point position may supplement the card. Cross-block
mentions still come only from that term's evidence block IDs. The title prefers a
normal full identity form only after that form has appeared in checkpoint-verified
prose; unrelated aliases require explicit earlier identity evidence, and honorific
forms do not outrank ordinary personal names. Safe high-confidence gender observations
are shown as compact attributes, preserving non-binary or cycling gender systems.
At canonical block order `N`, meanings and observations remain eligible only when
every cited non-structural evidence block has order `< N` (and an observation's
`available_from_order` is also `< N`). Contents/navigation blocks, unread current-block
facts, later evidence, and future identity inference are excluded. Normal Reader
startup and chapter loading do not initialize morphology or build occurrence indexes.
The helper does not call a model, change marker data, or modify translation artifacts
or pipeline state. Marker classification, prose review, and correction remain future
work.

In the web Reader, chapter names (the sidebar, the top bar and the chapter title) are shown in the target
language when the translation provides them: a chapter's own translated heading, or its title taken from a
translated table of contents. Otherwise the source title is shown, and interface labels such as "Awaiting
translation" stay English. An optional, hand-entered translated book title (Prepare → Translated title) replaces
the source title in the Reader. The reading settings (font, line height, width, gestures) and the theme are
kept in the browser.

## Native LLM transports and communication evidence

Intelitex supports four independent transports behind the same five-pass
pipeline contract:

- `llamacpp`: the existing llama.cpp HTTP/SSE client, native tokenizer and
  complete-request preflight;
- `openai`: the native foreground Responses API with strict structured output,
  `store: false`, `truncation: disabled`, and `/v1/responses/input_tokens`;
- `vllm`: the vLLM OpenAI-compatible Chat Completions API, streamed usage and
  native `/tokenize` chat-template preflight. Causal models use structured JSON
  schema; diffusion models use a schema instruction and post-response validation;
- `codex`: isolated thin inference over JSONL `codex app-server`, with persistent
  source sessions in new workspaces and the retained legacy per-attempt/pair modes
  in projects without the new execution contract.

Models, capacities, efforts, endpoints and rates are profile/catalog data. The
default settings contain a working local profile and disabled cloud templates;
they deliberately do not claim that any cloud model is available to the user.
Enable cloud profiles only after setting an explicit opaque model ID, verified
context capacity, and credential reference (OpenAI), or an explicitly authorized
Codex authentication source.

A built-in `vllm-diffusiongemma` profile, which targets the author's own vLLM server, is available to
every workspace; select it for a pass in the web workspace or with `--profile vllm-diffusiongemma`, or ignore
it. A project-local profile with
the same name overrides it. To use another server, add a profile like this to
the project's `settings.json` under `profiles`. The endpoint may end in `/v1`;
the transport uses `/v1/models`,
`/tokenize` and `/v1/chat/completions`. An optional `credential_env` names an
environment variable containing the server's bearer token.

```json
"vllm-diffusiongemma": {
  "provider": "vllm",
  "enabled": true,
  "model": "diffusiongemma",
  "endpoint": "http://127.0.0.1:8080/v1",
  "context_size": 131072,
  "planning_output_reserve": 16000,
  "request_timeout": 1200,
  "options": {"diffusion": true}
}
```

The transport checks the selected ID and server capacity through `/v1/models`.
`/tokenize` measures the actual chat messages before generation; the output cap
and a 1,024-token margin must also fit. A missing tokenizer response stops the
attempt before generation.
vLLM currently rejects per-request temperature, seed and structured-output
constraints for diffusion models. The `diffusion` option omits those fields;
the known `diffusiongemma` model ID also selects this behavior by default.
The pipeline still checks returned JSON against its canonical schema and retries
invalid output according to the project settings.

Selection precedence for a new call is: `--pass-profile P=NAME`, `--profile
NAME`, saved `pass_profiles`, then `default_profile`. Command overrides never
modify saved settings. Existing accepted checkpoints keep their provenance and
are not regenerated merely because profile selection changes.

Every physical attempt creates `attempt.json` before provider preflight and
retains the exact semantic request, trusted instructions, source payload,
canonical and transport schemas, native outbound/inbound messages, partial and
final output, usage snapshots, immutable pricing snapshot, safe diagnostics and
independent generation/validation/evidence/checkpoint states. This happens with
debug disabled. Credentials are redacted before persistence. If recording fails,
generation stops; missing provider usage is represented as unknown, never zero.

Useful offline and no-generation commands:

```bash
uv run intelitex profiles --project "$PROJECT"
uv run intelitex doctor --project "$PROJECT"
uv run intelitex attempts --project "$PROJECT"
uv run intelitex attempts --project "$PROJECT" --attempt artifacts/pass1/ch0001_a001/<fingerprint>/attempt_001
uv run intelitex usage --project "$PROJECT"
uv run intelitex usage --project "$PROJECT" --by-unit
uv run intelitex usage --project "$PROJECT" --by-unit --unit ch0016_c0004
uv run intelitex catalog-import --project "$PROJECT" /path/to/validated-models.json
```

The original `usage` output remains grouped by pass/provider/model. `--by-unit`
uses the detached `app.operations.usage_by_unit(UsageByUnitCommand(...))`
application result; adapters do not scan attempt directories. It groups P1
analysis units and P2-P5 chunks without pretending that P1 has a chunk ID.
Each pass retains provider, configured profile, requested model, physical
attempts, the checkpoint-producing attempt, recovery status, elapsed time,
preflight measurement and normalized provider usage.

Physical totals include every submitted attempt with reported usage, including
failed or invalid-output attempts. A preflight failure did not submit a model
request and contributes no consumption. Recovering or reusing a checkpoint adds
no new physical attempt. When any submitted attempt lacks a measurement, totals
sum the known values and explicitly report `unknown_attempts`; unknown is never
silently represented as zero. For example (abridged):

```json
{
  "units": [{
    "unit_id": "ch0016_c0004",
    "chapter_id": "ch0016",
    "chunk_id": "ch0016_c0004",
    "analysis_unit_id": null,
    "passes": [{
      "pass_no": 2,
      "task_key": "pass2/ch0016_c0004",
      "provider": "codex",
      "profile": "codex-sol-medium",
      "requested_model": "gpt-6.1-sol",
      "reported_model": "gpt-6.1-sol",
      "physical_attempt_count": 1,
      "accepted_attempt_id": "<fingerprint>-001",
      "input_tokens": {"value": 27318, "known_attempts": 1, "unknown_attempts": 0},
      "elapsed_seconds": {"value": 31.8, "known_attempts": 1, "unknown_attempts": 0}
    }],
    "input_tokens": {"value": 112244, "known_attempts": 4, "unknown_attempts": 0}
  }]
}
```

`catalog-import` validates a versioned JSON catalog and atomically activates it
under the project. A failed import leaves the previous catalog untouched. Price
changes never rewrite historical `pricing.json` snapshots. Unknown prices do not
block inference. Codex API-equivalent estimates are labeled estimates, not
subscription invoices or provider-reported charges.
The bundled catalog includes dated public API-equivalent rates for built-in Codex
profiles. The web Analyse/P1 view shows the cost of recorded attempts as they
arrive. Older P1 attempts whose saved snapshot has no rate are estimated at read
time with the current catalog and labeled accordingly; their evidence is not
changed. Other passes continue to use their saved pricing snapshots.

Example enabled profiles (replace model IDs and capacities only with values
verified for the intended account/runtime):

```json
{
  "default_profile": "local",
  "pass_profiles": {"1": "codex-analysis"},
  "profiles": {
    "local": {
      "provider": "llamacpp",
      "enabled": true,
      "model": null,
      "endpoint": "127.0.0.1",
      "context_size": null,
      "planning_output_reserve": 16000,
      "request_timeout": 1200,
      "seed": 42,
      "options": {"port": 8080, "thinking": "off", "request_extra": {}}
    },
    "codex-analysis": {
      "provider": "codex",
      "enabled": true,
      "model": "<verified-model-id>",
      "context_size": 100000,
      "planning_output_reserve": 16000,
      "max_output_tokens": null,
      "request_timeout": 1200,
      "reasoning_effort": "<verified-effort>",
      "executable": "codex",
      "options": {"auth_source": "/explicitly/authorized/auth.json"}
    }
  }
}
```

Codex uses private application-owned homes and an empty work directory outside
the project/source tree. Only an explicitly configured `auth_source` is copied;
user configuration, skills, rules, memories, plugins and trust settings are not.

Intelitex also provides central Codex profiles to every project without copying
them into each book's `settings.json`. Profile names follow
`codex-{astra|sol|luna}-{effort}`, using `gpt-6-astra`, `gpt-6.1-sol`, and
`gpt-6-luna`, respectively. All three include `low`, `medium`, `high`, `xhigh`,
and `max`, verified through Codex `model/list`. The Codex profiles exclude
`none`, which is not advertised by these models in the app-server. They use
`~/.codex/auth.json` as the authentication source. A project-local profile with
the same name overrides the built-in definition. For example:

```bash
uv run intelitex translate --project "$PROJECT" \
  --profile codex-sol-medium --continue 1
```
All discovered skills are disabled and re-listed before a thread starts. The
saved rollout is copied into the attempt. Legacy modes remove the temporary
authentication copy after the child process is reaped; `source-session-v1` retains
refreshed authentication in its durable private home for cold resume. App-server can still add its small
platform-owned read-only sandbox instruction and date/timezone wrapper, so this
mode is not advertised as a completely bare model request.

Codex app-server does not expose a verified no-turn input-token counter in the
installed protocol. Before submission, Intelitex therefore reports a
conservative upper bound measured in UTF-8 bytes and uses it for context
safety. Real cumulative app-server token-usage notifications are exposed live
as structured progress and the final normalized snapshot is retained in
`usage.json`. The historical `memory_tokens` setting is
measured with this same conservative byte counter when a Codex profile is used.

### Compact Codex wire formats

Codex profiles default to [cache-shared-v2](docs/codex-cache-shared-v2.md) for
P1–P5: shared developer/schema/source prefixes, separate SOURCE/COMMON/DRAFT user
messages, and a final active-pass message. Every pass retains its complete canonical
task and validation. Explicit profile wire options override the defaults; `compact-v1` and
[cache-v2](docs/codex-cache-v2.md) remain available for rollback. Canonical
fingerprints and checkpoints remain unchanged. Prefix equality does not
demonstrate provider cache reuse. A normal server restart reloads the defaults.

Codex translation defaults to
`translation_thread_strategy: paired-passes-v1`: P3 resumes accepted P2, while
P5 resumes accepted P4 in a separate conversation. P4 starts a fresh root and
never inherits P2/P3 conversations. Each attempt owns a new isolated runtime
and works from immutable accepted native snapshots. P1 is unchanged. The
`p2-parent-ephemeral-fork-v1` strategy remains available for rollback; `fresh-root`
opts out. See the [three strategies, retries and fallback](docs/codex-paired-passes.md).

The supported Pass-1 `compact-v1` rollback format is only the physical app-server
wire representation: source blocks use local integer indices, the response has
short field names, and the flat transport schema does not repeat the canonical
block-ID enum. Intelitex decodes the raw response before its existing Pass-1
repair and validation steps.

Canonical project and storage formats are unchanged. The task fingerprint is
still computed only from the project Pass-1 prompt, canonical input, and
canonical response schema. Consequently old accepted checkpoints and compatible
completed verbose Codex attempts remain reusable, including after an interrupted
translation resumes at P4 when P2/P3 are already accepted. Mixed verbose and
compact attempt artifacts require no conversion or project migration.
Validation-only retry fields are excluded narrowly from recovery identity, so a
completed second attempt can be checkpointed after restart without another model
turn. Source, prompt, model, effort, and other execution-significant settings
remain part of compatibility checks.

Each compact attempt retains both layers: canonical `request.semantic.json` and
`schema.canonical.json`, actual compact `request.transport.json` and
`schema.transport.json`, raw compact `answer.txt`, decoded
`decoded.canonical.json`, and the canonical accepted `result.json`. Response
metadata records `wire_format: compact-v1`.

Project-specific instructions placed around the canonical output contract are
preserved by the wire transformation. If that contract itself is customized in
a way `compact-v1` cannot recognize exactly, the request fails before submission
instead of silently dropping text. Likewise, a local evidence-write failure is
not classified as invalid model output and never triggers another model turn.

For rollback or debugging, set the Codex profile option below. It changes only
transport and does not change semantic task identity:

```json
{
  "options": {
    "p1_wire_format": "canonical"
  }
}
```

Codex P2–P5 use compact local block/sentence indices, reversible memory/artifact
encoding and a fixed flat structured schema. Profiles without a wire option
inherit `cache-shared-v2` globally. The [cache-v2 codec](docs/codex-cache-v2.md),
canonical and cache-v1 remain explicit rollback options.
The llama.cpp and native OpenAI transports retain their existing representations.

Existing format-1 local settings migrate narrowly to a `llamacpp` profile. When
JSON checkpoint state exists, Intelitex first creates a JSON state backup under `backups/`.
Frozen source IDs, review choices, approvals, checkpoints and old artifacts are
not rewritten. Old token counts without matching tokenizer provenance are not
reused for a different tokenizer.

## Chapters, sections, scenes, and HTML extraction

- With a package file, import follows the **OPF spine**, not filename order.
- NCX / EPUB navigation anchors and `h1` / `h2` headings create major section boundaries.
- For Calibre-converted editions, `calibre27` is also recognized as a named major
  section marker; this catches an embedded story that starts in the middle of an HTML file.
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

Translation does not use arbitrary token-sized chunks. The default structural
policy is:

```text
section <= 10,000 visible-text characters
    -> translate the whole section as one unit

section > 10,000 characters
    -> one translation unit per detected natural scene

individual scene
    -> never split merely because it exceeds 10,000 characters
```

This intentionally keeps compact multi-scene sections, such as a short embedded story,
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

Pass 1 planning reuses each narrative section's `source_tokens` count saved during import. A section that already fits the P1 source budget is scheduled immediately as one analysis unit, without re-tokenizing every paragraph through llama.cpp. Block-level `/tokenize` calls remain only as a fallback for an exceptionally large section or an older manifest without cached section token counts. Projects imported earlier need no re-import.

## Continuity and spoiler safety

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
- a JSON checkpoint only after valid complete output is on disk.

A response terminated by a token limit, connection loss or Ctrl+C is **not** marked
complete. Rerun the same command. Prior successful passes are reused. The interrupted
request itself restarts; this program does not restore an in-flight CUDA decoding
state. This is the only work that may need repeating.

JSON state transactions, atomic file replacement, filesystem sync, checksums and a
project lock protect normal checkpoint operations. Do not run two pipeline-changing
processes on the same project. The Reader is the narrow exception: it may run beside
`translate` because it only reads pipeline state and writes the separately locked
`translation.review.json`. Keep the project on a local filesystem with reliable
locking, and back it up. Hardware/filesystem failure still requires backups.

Changing model parameters affects future calls, not already accepted results. Exact
request parameters are saved with each attempt. Prompt/input changes create a new
versioned artifact instead of overwriting old results. For controlled comparisons
between models/prompts, use separate projects.

### Pass 1 recovery

Pass 1 validation repairs only mechanically provable lexical-grounding failures instead of paying for another full model decode. Unattested aliases are removed; a missing citation may be supplemented only with an exact-match source block from the same analysis unit; a term with no attested source form anywhere in the unit is dropped as an unsupported delta. Existing memory is never erased by these repairs. Completed failed attempts, including attempts in the current fingerprint directory, are reconsidered on restart before any new model call. All repairs are logged in `recovery.json` or `validation_repairs.json`.

Pass 1 builds a request-specific JSON grammar whose `evidence` fields may contain
only block IDs present in the current source section. This prevents the model from
inventing evidence IDs. If upgrading after an older completed Pass 1 response failed
only because an observation cited an out-of-section ID, `analyze` can recover that
completed response without another model generation: the unsupported observation is
dropped as a whole, the repair is recorded in `recovery.json`, and term evidence
remains strict and is never guessed or rewritten.

## Progress display

The terminal shows:

1. Global analysis-section or completed-book-unit progress.
2. Current narrative section, local translation unit, and progress through P2-P5.
3. Current pass, waiting/receiving state, and received answer/reasoning **characters**.

The web interface shows a weighted workflow percentage per workspace (explicitly not an ETA), per-phase
counters and the *Live activity* stream. There is no elapsed timer or ETA in either interface. Incoming stream events are not falsely
reported as exact generated tokens. Server token usage is saved when available.
Noninteractive logs print stage transitions instead of animated bars. Prefill is
shown as waiting; the server may not emit textual output during it.

## Settings and bounded generation

`settings.json` in the project controls model address, per-pass temperature/output
limits, continuity budget, and analysis-memory budget. `prompts/pass1.txt` through
`pass5.txt` (and `pass0.txt` for Codex source sessions) are editable project-local prompt files, not hidden constants in the
Python orchestration code.

Chat-compatible local servers such as Strata can run without `/tokenize` or an
input-token counting endpoint. Missing endpoints (HTTP 404, 405 or 501) select a
conservative UTF-8 byte bound for planning and request-capacity checks. These
bounds are recorded as bytes, never as token usage. Missing token usage is shown
as `—` and does not pause subsequent local passes; token counts returned by the
server remain visible. Authentication, connection and server errors still fail
normally. Strata receives the supported `json_schema` response format. Explicit
server rejections of completed invalid JSON or schema mismatches use the existing
`json_retries` budget and remain recorded as failed validation attempts. Output
limits and other server errors stop the run without automatic generation retries.

Default thinking is **off**: analysis is explicit JSON work, not unbounded hidden
deliberation. To test thinking only in P1/P2/P4:

```bash
uv run intelitex analyze --project "$PROJECT" --thinking analysis
uv run intelitex translate --project "$PROJECT" --thinking analysis --continue 5
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
uv run intelitex export --project "$PROJECT" \
  --encoding iso-8859-2 --output "$PROJECT/exports/translation-latin2.txt"
```

Conversion is strict and may reject smart punctuation or symbols absent from that
encoding. UTF-8 originals remain unchanged. Internal project files are protected
from being overwritten by additional exports.

## Text export versus EPUB publication

`export` is the existing technical text materialization operation. It rebuilds
`translation.txt`, `translated_chapters/*.txt`, and `translation.status.json` and
remains useful while translation is incomplete. Translation also maintains those
files after every completed unit.

`publish` is a separate final-product operation. It requires completed P1,
approved terminology, all chunks currently `done`, and intact P5 checkpoints. It
copies the original EPUB package, replaces only mapped translated prose blocks,
sets package language metadata to the requested target (`pl` by default), validates
the generated archive, and atomically promotes:

```text
PROJECT/published/<original title> [PL].epub
```

`publication.json` records the current publication fingerprint, output checksum,
metadata, validation checks, last successful edition, and latest failed attempt.
An older valid EPUB may remain after terminology makes chunks stale, but `status`
reports that edition as stale rather than current. `publish --target-language TAG`
keeps the application boundary language-neutral even though the current product
workflow defaults to Polish.

## Files to inspect

```text
project/
  book.json                    # frozen sources, sections, chunks, stable IDs
  series.json                  # optional immutable series/volume identity sidecar
  series.seed.json             # optional deterministic inherited-memory snapshot
  analysis_plan.json           # P1 work units
  workspace.json               # web setup: source, language pair, label, per-pass profile choices
  web.config.json              # web section choices (F/T/E processing, per-section overrides)
  translated_title.json        # optional hand-entered book title shown by the Reader
  settings.json
  state/HEAD.json              # authoritative checkpoint state commit pointer
  state/commits/               # immutable JSON revisions
  state/objects/               # complete JSON records, shared between revisions
  state.sqlite3                # retained legacy file only; no runtime access
  book_memory.json              # readable memory snapshot
  terms.review.json             # source of truth: choices, per-term review state, confirmation
  terms.review.html             # optional read-only snapshot/catalogue
  translation.review.json      # Reader prose markers
  lexicon.approved.json
  translation.txt              # growing contiguous completed prefix
  translation.status.json      # pending/done/stale status
  publication.json             # publication identity, validation and last attempt
  published/                   # atomically promoted current translated EPUB
  source-package/              # durable copy of a packed EPUB source
  extracted/                   # per-HTML source text
  chapters/                    # per-chapter source text
  translated_chapters/          # per-chapter available translations
  prompts/                     # pass0 (Codex source sessions) and pass1-pass5 prompts
  analysis_inputs/              # frozen P1 input snapshots for resumption
  artifacts/pass1/...           # every request/result/attempt
  artifacts/pass2/...           # likewise for P2-P5
  history/                     # review/state snapshots and verified migration backups
```

`translation.txt` is rebuilt from completed block artifacts; the program never
blindly appends duplicate text on resumption. It exposes only a contiguous readable
prefix. `status` and `export` work without a running LLM.

```bash
uv run intelitex status --project "$PROJECT"
uv run intelitex export --project "$PROJECT"
```

## Tests and limitations

Run:

```bash
uv run --group dev python -m pytest -q                      # Python
node --test tests/*.cjs                                     # plain-JavaScript logic
npm --prefix web run typecheck && npm --prefix web run lint
npm --prefix web run test:unit                              # components and helpers
npm --prefix web run test:e2e                               # Playwright browser tests (offline fixture server)
```

The browser tests need Playwright's Chromium and its system libraries; they use a disposable server with an
in-process offline provider and never touch your workspaces. See [web development](web/README.md).

The included tests use a local mock HTTP/SSE server. They test orchestration, not
translation quality: HTML/Unicode extraction, OPF order and NCX anchors, lossless
splitting, stop-before-approval, repeated analysis, candidate selection, incremental
continuation, interrupted P4 resumption without repeating P2/P3, bounded invalid-JSON
retries, source-ID coverage, candidate preservation, stale chunks after a custom
choice, no default cross-story continuity, artifact checksums and manifest integrity.
Reader tests additionally cover checkpointed chapter assembly, canonical/piece IDs,
incomplete and stale content, marker validation/conflicts/deletion, HTTP safety, and
Unicode word-range logic without a model call.

Structural validation detects
malformed/missing blocks and references, not every semantic mistranslation. P4 is a
model audit, not a guarantee of fidelity. Keep the first run small and inspect its
source, draft, correction ledger and final output before running the entire book.

EPUB round-tripping is deliberately conservative. Plain block text, line breaks,
and balanced emphasis/strong markers retained by the translation are reconstructed.
If a translated source block contains meaningful inline structures that cannot be
mapped safely (for example a text-bearing hyperlink or attributed inline span),
publication fails rather than silently deleting markup. Untranslated navigation,
non-narrative content, CSS, images, fonts, cover data, and other package assets are
copied unchanged. The browser Reader remains a local checkpoint-backed reading view;
it does not construct the EPUB itself.

## Documentation

- [Architecture](docs/architecture.md), [server runtime](docs/server-runtime.md), [server API](docs/server-api.md)
- [Web development](web/README.md), [debug IDs](docs/web/debug-ids.md), [intentional differences](docs/web/differences.md)
- [Library hierarchy](docs/library-groups.md), [book identity and corrections](docs/book-metadata.md)
- [Persistent P0-P5 sessions: validation](docs/validation-p0-p5-persistent-sessions.md), [measured live evidence](docs/validation-p0-p5-live.json)
- [Codex wire formats and cache strategies](docs/codex-cache-shared-v2.md), [paired passes](docs/codex-paired-passes.md)
- [State](docs/state-json-migration.md) and [job](docs/job-json-migration.md) JSON migrations, [workflow revisions](docs/workflow-revisions.md)

The implementation uses the llama.cpp server's documented chat, JSON response,
tokenizer and streaming endpoints. EPUB structure follows package spine order where
available. Relevant primary references:

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://www.w3.org/TR/epub-33/

The five translation roles are based on an earlier five-role design ("Pipeline V2").
The compact schemas, source IDs, checkpoints, approval gate and deterministic merge
are implementation changes, not claims that the original prompts guarantee them.
