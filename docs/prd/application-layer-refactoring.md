# Application Layer Refactoring

## 1. Document control and implementation mandate

- **Repository:** `hipotures/intelitex`
- **Document type:** architecture decision, refactoring requirements, and implementation plan for Codex.
- **Prepared:** 2026-09-21.
- **Audited baseline:** `87a13ee2a952f773dbecaabd4e01b5e802e96aff` on `main`.
- **Baseline package declaration:** Intelitex 1.11.0; Python >= 3.11.
- **Delivery represented by this document:** documentation only. The application-layer refactor is not implemented by this commit.
- **Verification basis:** static inspection of entry points, orchestration, persistence, review and reader services, configuration, continuation, provider integration, and selected regression tests. This document is not a report of an executed test suite or a completed live translation.

**Implement a shared, interface-independent Python application layer while preserving existing functionality, persisted data, and the current CLI/web division of responsibilities.**

The purpose is to make a later interface change an adapter-level project, not another extraction of business logic. This is not authorization to implement that later interface now.

The implementation must not stop at a facade that forwards arguments to `cli.main()`. Existing use-case orchestration and policies must move behind a real programmatic boundary. Equally, this is not an instruction to rewrite working algorithms or introduce a distributed architecture.

Before implementation, compare the working checkout with the audited baseline. Preserve functionality introduced since that baseline and inventory it explicitly. Existing local changes are not disposable. When this document describes the baseline, it does not authorize removing a subsequently implemented feature.

## 2. Scope and non-goals

### 2.1 Required outcome

The following must all be true:

1. Every existing application operation can be invoked through a documented Python service API without parsing CLI arguments, starting an HTTP server, or constructing a terminal UI.
2. Existing CLI commands use that API for application work.
3. Existing terminology-review and reader HTTP handlers use that API for application work.
4. CLI-only operations remain CLI-only in the user interface. Web-only interactions remain web-only. A method becoming callable from Python is not a requirement to expose it through another user interface.
5. Existing projects remain usable without reimporting, reanalyzing, discarding decisions, or regenerating successful model calls just because code was reorganized.
6. Application behavior is protected both by compatibility tests and by enforceable dependency boundaries.

### 2.2 Explicit non-goals

Do not introduce any of the following as part of this refactor:

- A new dashboard, responsive redesign, component framework, frontend build system, TUI, or desktop application.
- CLI equivalents of interactive review/reader features, or web equivalents of import/analysis/translation commands.
- A general REST API, endpoint versioning project, FastAPI migration, or replacement of the existing local HTTP servers.
- A background daemon, persistent process scheduler, task queue, distributed workers, new pause/resume/cancel commands, or a new lifecycle state machine for long-running processes.
- New language pairs, models, transport integrations, prompt changes, extraction heuristics, alias-resolution policies, retry policies, or translation-quality improvements.
- Direct EPUB archive import or translated EPUB export, unless those capabilities actually exist in the implementation baseline used by Codex. They do not exist in the audited CLI/pipeline described here.
- An ORM, a new database engine, an on-disk project format redesign, new checkpoint identities, or automatic migration of all project files into a different layout.
- Authentication, multi-user collaboration, broader network exposure, or a cross-platform locking rewrite.

Internal DTOs, ports, resource scopes, pure policy functions, tests, and compatibility wrappers are permitted architectural additions. They must not silently change user-visible behavior.

## 3. What the repository actually does

### 3.1 Current workflow

The audited implementation follows this sequence:

```text
Unpacked EPUB / HTML directory
    -> import and freeze source/chunk manifest
    -> analyze: Pass 1 over the whole book
    -> terminology review draft
    -> web edits and optional glossary confirmation
    -> approve: explicitly commit choices from CLI
    -> translate: P2 -> P3 -> P4 -> P5 for each requested unfinished chunk
    -> incremental text output and local reader
```

Important distinctions supported by [CLI], [Engine], [Store], [Review], and [Reader]:

- `import` accepts an unpacked directory. It performs discovery/token counting but not a translation/model generation turn.
- `analyze` stops after book-wide P1 and review preparation. It does not translate prose or automatically approve terminology.
- Browser confirmation changes the review draft. The separate `approve` command applies terminology to the committed application state.
- P2-P5 run sequentially for each selected unfinished translation unit, not as four whole-book passes.
- `export` produces text. Existing output includes `translation.txt`, `translated_chapters/*.txt`, and `translation.status.json`.
- The reader can use checkpoint-verified output while translation is in progress.

### 3.2 Existing boundaries and extraction targets

| Current location | Responsibilities observed | Refactoring decision |
| --- | --- | --- |
| `translate.py`, `bookpipe/cli.py` | Argument parsing, configuration resolution, import orchestration, continuation setup, model checks, operational commands, approval, export validation, locks, resource cleanup, and rendering | Keep entry points/parsing/rendering; move application decisions and orchestration into services |
| `bookpipe/engine.py` | P1/P2-P5 orchestration, request execution, checkpoint recovery, validation/repair, memory use, progress messages, and text export | Separate use-case orchestration, pure policies, and infrastructure dependencies; preserve algorithms and request identities |
| `bookpipe/store.py` | SQLite access, transactions, checkpoint integrity, terminology merge/memory selection, review generation, approval, backups, and static review HTML | Retain SQLite behavior behind capabilities; move policy/orchestration out of the storage implementation |
| `bookpipe/review.py` | Review rules, JSON persistence, revision conflicts, evidence access, HTTP server, and embedded UI | Extract application review operations and JSON storage; preserve routes and UI |
| `bookpipe/reader.py` | Marker rules/storage, HTTP endpoints, server startup, and HTML | Extract marker/application operations; preserve routes, assets, and browser behavior |
| `bookpipe/reader_context.py`, `bookpipe/review_context.py` | Read models, direct read-only SQL, artifact validation, canonical block alignment, contextual knowledge, and caches | Separate read-only persistence access from application assembly and pure matching/visibility rules |
| `bookpipe/profiles.py`, `bookpipe/project_config.py`, `bookpipe/series.py` | Profile resolution/migration, configuration inheritance, lineage, seed construction, and filesystem operations | Reuse validated behavior behind configuration and continuation use cases; separate pure transformations where needed |
| `bookpipe/provider_registry.py`, transport modules | Provider selection/construction, native requests, subprocess/HTTP lifetimes, and progress callbacks | Keep native transports; inject through explicit capabilities and remove concrete terminal dependencies |
| `bookpipe/operations.py`, `bookpipe/catalog.py`, `bookpipe/evidence.py` | Diagnostics, usage/attempt reports, pricing catalogs, evidence recording, and JSON printing | Move report orchestration behind services; printing stays in CLI; evidence remains durable infrastructure |
| `bookpipe/ui.py` | Rich-based progress and terminal output | Remain a presentation adapter; never be a dependency of application/domain code |
| `bookpipe/util.py` | Pure normalization/hashing mixed with file I/O and locks | Separate pure primitives from I/O as needed, preserving exact algorithms and compatibility imports |

This is already more than a single script. There are reusable engine functions, transport boundaries, read models, and useful tests. Preserve and reorganize these assets rather than replacing them wholesale.

### 3.3 Do not infer features from earlier sketches

The audited tree contains the terminology reviewer and translation reader, not a general processing dashboard with start/pause/stop endpoints. The sample-book README discusses a contents opt-out scenario, but that description alone is not proof of an implemented application operation.

Inventory the actual current code before implementing. Do not add imagined operations to satisfy an earlier architectural sketch, and do not add a new `Job` lifecycle merely because an earlier conversation used that word.

## 4. Architecture decisions

### AD-1: A modular monolith with a Python service boundary

Keep one Python application and the existing local execution model. Expose named use cases through `bookpipe.application`; compose concrete dependencies in `bookpipe.bootstrap`.

A service boundary is appropriate because CLI and HTTP adapters should share operations and coordination rather than independently encode them. The general service-layer pattern is described in [Service Layer]. The particular boundaries below are specific to Intelitex.

### AD-2: Interface independence does not mean interface parity

Review can remain a browser interaction and translation can remain a foreground CLI operation. Both use the same application boundary. Neither must emulate the other.

### AD-3: Preserve behavior before improving behavior

A refactoring commit must not also fix a known product defect, redesign a workflow, or change validation acceptance. Record discovered defects separately with a reproducer. Changes necessary to make the extraction safe must be isolated, explained, and regression-tested.

### AD-4: Keep synchronous execution and existing checkpoints

Application operations run in the caller's execution context. A future adapter can supply an execution host, but this refactor does not provide one.

The existing `jobs` table stores accepted semantic task results keyed by `(key, fingerprint)`. It is not a persistent record of a running CLI process. Preserve this meaning. Do not overload it with `running`, `paused`, process IDs, or a second truth about pipeline state.

### AD-5: Explicit dependency injection, not a framework

Use normal constructors/factories, small `typing.Protocol` interfaces where substitution is useful, and explicit context managers. No dependency-injection container, command bus, event-sourcing system, generic repository framework, or class-per-function architecture is required.

### AD-6: No persistence migration is expected

The target is a code-boundary change. Existing SQLite tables, JSON formats, paths, stable identifiers, fingerprints, and historical artifacts remain compatible. A proposed mandatory data migration is a reason to revisit the design, not an assumed step in this project.

## 5. Target dependency structure

```text
Existing CLI adapter        Existing review/reader HTTP adapters
          |                              |
          +---------------+--------------+
                          |
                  Python application API
                          |
                 Use-case orchestration
                    /             \
            Pure domain rules    Outbound capability ports
                                         ^
                                         |
                   Files / SQLite / native LLM transports / evidence

bootstrap: wires concrete adapters to application services
```

Arrow direction above represents dependency or invocation, not an instruction to introduce a network boundary.

### 5.1 Responsibilities

**Application layer:** operation validation, workflow preconditions, orchestration, resource/lock scopes, transaction coordination, result assembly, and progress publication. It exposes commands and queries but does not render a terminal or serve HTTP.

**Domain policies:** deterministic review decisions, merge/memory rules, identity/validation logic, canonical block assembly, and knowledge-visibility rules where they can be extracted cleanly. They do not open files, databases, sockets, subprocesses, or browsers.

**Infrastructure:** existing SQLite/filesystem operations, locking, artifact access, resource lookup, native providers, evidence recording, and no-turn runtime probes. Infrastructure performs I/O under application coordination and may use pure domain helpers.

**Presentation adapters:** argument and HTTP decoding, transport-specific validation, mapping results/errors to existing output formats, rendering existing UI assets, and server/browser lifecycle.

**Composition root:** selects concrete implementations. It may import application and infrastructure, but application and infrastructure must not import it back.

### 5.2 Preferred module organization

Use this as the default organization, not as a requirement to create empty files:

```text
bookpipe/
  application/
    __init__.py             # deliberately small public API exports
    api.py                  # service groups; no business-rule duplication
    commands.py             # explicit operation inputs
    results.py              # detached operation/query results
    ports.py                # capabilities actually consumed by services
    sessions.py             # operation, review, and reader resource scopes
    projects.py             # import/configuration/continuation/status
    pipeline.py             # analyze and translate use cases
    execution.py            # Runner orchestration and recovery coordination
    review.py               # draft editing, confirmation, committed approval
    reader.py               # reader queries and marker use cases
    operations.py           # profiles/doctor/attempts/usage/catalog/discover/smoke
    exports.py              # text-export orchestration and copy validation
  domain/
    identity.py             # unchanged pure hash/fingerprint primitives
    validation.py           # unchanged model-result rules and repairs
    terminology.py          # merge, review-choice, and memory policies
    reader.py               # pure block/formatting/context visibility rules
  infrastructure/
    project_files.py        # existing project paths and atomic file operations
    sqlite_store.py         # SQL, short transactions, backups, checkpoints
    review_files.py         # review draft storage and restore points
    marker_files.py         # marker storage
    read_models.py          # read-only checkpoint/evidence persistence access
    resources.py            # bundled defaults/prompts/catalog/resource lookup
  bootstrap.py              # dependency construction only
  cli.py                    # existing CLI entry point remains importable
  ui.py                     # existing Rich presentation adapter
  review.py                 # existing HTTP/UI adapter; compatibility exports
  reader.py                 # existing HTTP/UI adapter; compatibility exports
  reader.js
  reader.css
  review_filters.js
  ...                       # retained parsers, providers, codecs, and shims
translate.py
```

The file split may be reduced or adjusted when the implementation demonstrates a simpler equivalent. The dependency rules and acceptance criteria are mandatory; the number of files is not.

Transport modules, importer parsing, schema definitions, codecs, and other stable helpers may remain at their existing paths when they are already behind an appropriate boundary. Do not move every file just to make a directory tree look uniform. Conversely, retaining the filename `store.py` is not permission to retain application policy inside its concrete storage implementation indefinitely.

### 5.3 Forbidden dependencies at completion

- Application/domain code must not import `bookpipe.cli`, `bookpipe.ui`, the review/reader HTTP adapter modules, Rich, `argparse`, `http.server`, or `webbrowser`, including type-only imports.
- Application code must not call `cli.main`, inspect `sys.argv`, call `print`/`sys.exit`, or construct a subprocess that runs `translate.py`.
- Public application results must not expose `sqlite3.Connection`, cursors, `Store`, HTTP request/response objects, `Display`, or provider processes.
- CLI/HTTP handlers must not perform application SQL, edit project JSON directly, decide terminology invalidation, build model requests, or independently enforce workflow transitions.
- Application services must depend on capability contracts rather than constructing a concrete SQLite store, provider client, or filesystem lock themselves.
- Infrastructure must not depend on presentation modules. Pure domain code must not depend on application orchestration or infrastructure.

Serving static UI resources is presentation work, not a violation of the prohibition on handlers modifying application files. The existing Codex transport and protocol doctor may continue to use subprocesses; the prohibition is against implementing use cases by invoking the CLI.

## 6. Python application API

### 6.1 API shape

Provide explicit, typed service groups. Suggested operation names below are design names, not existing APIs:

| Service group | Operations to expose |
| --- | --- |
| Projects | `import_book`, `status`; shared configuration resolution used internally by the relevant commands |
| Pipeline | `analyze`, `translate` |
| Review | `open_session`, `approve`; session methods `load`, `summary`, `evidence`, `patch_term`, `review_terms`, `set_confirmed` |
| Reader | `open_session`; session methods `load`, `metadata`, `progress`, `chapter`, `context`, `create_marker`, `delete_marker` |
| Exports | `export_text` including the existing optional strictly encoded copy |
| Operations | `profiles`, `doctor`, `attempts`, `usage`, `import_catalog`, `discover`, `smoke` |

Do not create separate public CRUD operations for internal SQL rows merely to make every low-level function public. The API exposes meaningful existing operations.

A conceptual calling pattern is:

```python
# Proposed API illustration, not code that exists at the audited baseline.
from bookpipe.bootstrap import create_application
from bookpipe.application import AnalyzeCommand, ApproveCommand, TranslateCommand

app = create_application()
app.pipeline.analyze(AnalyzeCommand(project=project_path))

with app.review.open_session(project_path) as review:
    draft = review.load()
    # A browser or a test makes the same explicit decisions through this service.
    # Draft editing and confirmation do not commit the approved lexicon.

app.review.approve(ApproveCommand(project=project_path, accept_defaults=True))
app.pipeline.translate(TranslateCommand(project=project_path, chunk_limit=1))
```

The explicit `accept_defaults=True` above illustrates the existing CLI opt-in. It must not become the default for tests, review confirmation, or normal translation.

### 6.2 Commands, results, and validation

Use Python 3.11-compatible dataclasses for operation inputs/results where they improve clarity. Use typed mappings for existing nested JSON contracts when converting them would create unnecessary churn. Follow [Python typing]; annotations alone are not runtime validation.

Requirements:

- Inputs are explicit values: project `Path`, source directory, selected profile, per-pass overrides, limits, encoding, and existing flags. Do not pass `argparse.Namespace` or a web request into the application.
- Preserve omitted-versus-explicit distinctions. In particular, an omitted whole-section limit differs from an explicit value for continuation imports.
- Preserve validation details such as rejecting booleans where an exact integer is required. Do not broaden accepted inputs through automatic coercion.
- Results are detached snapshots. A caller mutating a result must not mutate live service state or persist changes. A frozen dataclass containing a mutable dictionary is not sufficient by itself; use defensive copies or appropriate immutable values at boundaries.
- Avoid repeatedly copying an entire book for a small progress event. Copy at ownership boundaries, not indiscriminately inside hot loops.
- Native `Path` values are acceptable internally; adapters serialize them deliberately. Never make HTTP URL syntax part of domain identifiers.
- Do not introduce a project registry or replace filesystem project identity with new UUIDs.
- Do not run all existing JSON through a new serializer that changes field omission, ordering relevant to bytes, Unicode handling, newline preservation, numeric values, or fingerprints.

Keep business validation inside the application/domain boundary so direct Python callers cannot bypass it. Keep transport validation, such as JSON content type or malformed CLI syntax, in the relevant adapter.

### 6.3 State queries must preserve separate facts

A status result should represent existing facts independently: analysis completion, committed approval, chunk statuses, section counts, and retained output. Review snapshots separately carry draft confirmation and revision.

Do not collapse these into a new global enum that changes behavior. In particular:

- Draft `confirmed` is not SQLite `approved`.
- A draft choice may differ from the committed choice while old translations remain valid under the earlier approved snapshot.
- A completed chunk can become `stale` while retaining a readable final artifact.
- An accepted task checkpoint is not a running process.

The current translation gate uses committed application state. Changing a draft in the reviewer must not silently become a new gate for translation. Future UI state can be derived from these facts without changing their meaning.

### 6.4 Exceptions

Keep actionable application errors distinct from adapter transport errors. Preserve the identities or compatible re-exports of `PipelineError`, `ReviewConflict`, and `MarkerConflict` while existing callers/tests depend on them.

CLI continues to render its existing error messages and exit behavior. HTTP continues to map conflict errors to 409 and ordinary request/application errors to the existing statuses. Do not normalize every failure into a generic success envelope or catch all exceptions and return an empty result.

`KeyboardInterrupt` is not a new pause command. It must unwind resources, preserve accepted work, and reach the CLI boundary that returns 130 as before.

## 7. Outbound capabilities and composition

Introduce small ports for the capabilities services need, not a generic `Repository[T]` layer for every table.

At minimum, make the following boundaries explicit:

| Capability | Purpose | Constraint |
| --- | --- | --- |
| Project files/resources | Read manifests/configuration/prompts and atomically write existing artifacts | Preserve paths and bytes; no hidden reimport |
| Project persistence | Read/update metadata, terminology, receipts, dependencies, and accepted checkpoints | SQL and raw connections remain private |
| Transaction/backup scope | Short atomic SQLite mutations and existing backup points | Do not span model calls or a server lifetime |
| Review/marker document storage | Load and atomically replace versioned document snapshots and backups | Whole mutation under the session mutex, not only the final write |
| Verified read persistence | Read registered P5 artifacts and associated read-only data | No Store initialization or DB creation on a read path |
| Provider factory/pool | Resolve and construct existing native providers lazily | No inference or credential access during API construction |
| Attempt/evidence access | Retain current request, output, usage, and pricing evidence | Preserve mandatory recording/failure semantics |
| Progress sink | Deliver presentation-independent progress to a caller | No durable event bus, no inference state recovered from log parsing |

Interfaces should be driven by concrete consumers. Separate read-only access from mutating access where it matters, particularly for the reader. Do not expose `.db` through a protocol just to make existing direct SQL compile.

`create_application()` constructs a lightweight facade and dependency factories. It must not open a project, create SQLite files, migrate settings, contact a provider, resolve credentials, bind a socket, or launch a process. Those actions happen only when the corresponding explicit operation or session is entered.

Use context managers or `ExitStack` for deterministic cleanup; see [Python contextlib]. Keep dependency injection explicit and testable. No module-global current project, global Store, shared mutable settings, or singleton provider pool spanning unrelated operations.

### 7.1 Progress without a terminal dependency

Replace concrete `Display` dependencies in engine/importer/provider integration with a small neutral contract. A `ProgressEvent` plus `ProgressSink.emit()` is the preferred end state.

Events should carry available structured values such as operation, pass number, task key, chapter/chunk IDs, completed/total counts, and received character counts. The CLI renderer preserves existing wording, stderr behavior, quiet mode, and progress appearance. A null sink makes direct calls usable without a terminal.

Legacy display bridges may be used during extraction, but do not parse labels such as `P3/5 | ...` to recover canonical state. Application return values and persisted checkpoints, not human-readable messages, determine completion.

These callbacks are ephemeral observations, not an event log or command/control channel. They must not launch inference, alter checkpoint acceptance, or introduce new persistent state. Keep mandatory evidence recording separate from optional UI progress.

## 8. Resource ownership, locks, and concurrency

Moving use cases out of `main()` also moves responsibility for executing them safely. A direct Python caller must not be required to know which undocumented file lock to acquire.

### 8.1 Required resource model

Use three explicit scopes:

1. **Single-operation project scope.** Import, analysis, translation, approval, export, status, and operational commands acquire the existing project lock for the duration corresponding to the baseline command. Store/provider resources are lazy and owned by that operation.
2. **Review session scope.** Entering the session acquires the project writer lock, checks the same prerequisites as the existing review command, and prepares the draft. The lock remains held until the review server/session closes. Draft operations share one session mutex.
3. **Reader session scope.** Entering the session acquires only the reader lock and preserves the reader startup validation. Its marker operations share one session mutex. Checkpoint queries use read-only, request-owned connections and do not take the project writer lock.

| Existing invocation | Existing process lock to preserve | Resource lifetime |
| --- | --- | --- |
| All CLI commands except `reader` | `.lock`, exclusive, nonblocking | Foreground command; for `review`, the whole server session |
| `reader` | `.reader.lock`, exclusive, nonblocking | Whole reader session |
| Continuation handoff | Destination `.lock`, plus predecessor `.lock` during handoff | Same nonblocking handoff policy as baseline |
| Individual review/marker HTTP mutation | Session's existing process lock plus shared thread mutex | Mutex covers read, revision check, validation, backup, and atomic replace |

Do not acquire a second independent instance of the same non-reentrant lock when a bound service calls another service inside its operation/session. Model a session-owned execution context or use private unlocked helpers with clearly bounded ownership. Test nested paths for self-deadlock.

Remove duplicate lock acquisition from CLI once application scopes own it. Likewise, compatibility server entry points must either own a session themselves or receive an already-bound session; they must not accidentally do both.

### 8.2 SQLite and threads

A SQLite connection must not be created in the CLI thread and then shared across `ThreadingHTTPServer` request threads. Preserve request-owned read-only connections used by the current reader/evidence code. Do not set `check_same_thread=False` as a substitute for designing connection ownership; see [Python sqlite3].

The review session may create a Store during preparation in its owning thread. Close or keep it confined there; HTTP draft operations should use the extracted draft service and read-only evidence gateway, not that mutable connection.

Session exit must close providers/stores and server-owned resources in a defined order and release locks on success, error, bind failure, and interruption. Application sessions do not themselves bind sockets or open browsers; those remain adapter concerns.

### 8.3 Preserve supported concurrency, not an imagined stronger model

The reader is deliberately permitted alongside translation. Review and other writer commands are mutually exclusive under `.lock`. Preserve those properties.

Do not start allowing simultaneous writer commands by shortening locks to a single SQL statement. Conversely, do not make reader queries acquire `.lock`, as that would remove existing concurrent reading.

The existing reader/context caches are part of observed behavior. Move them with an explicit session owner and test their refresh behavior. Do not rely on an outdated comment about the reader holding the project lock when executable code uses the separate reader lock.

External manual edits to files are not a transactional collaborator. Preserve existing revision checks and their documented limitations rather than claiming that a JSON hash alone makes arbitrary external concurrent writes safe.

## 9. CLI compatibility inventory

Preserve `translate.py`, `bookpipe.cli.main(argv)`, command names, argument spelling, defaults, error/exit conventions, and scriptability. No new command is required.

| Command | Application operation | Behavior that must survive |
| --- | --- | --- |
| `import` | Projects.import_book | Directory import, planning, extraction, optional sidecars, model identity, configuration, and optional predecessor handoff |
| `analyze` | Pipeline.analyze | Book-wide P1; resume receipts; generate review; stop without prose translation |
| `review` | Review.open_session | Prepare review and launch the existing browser application |
| `reader` | Reader.open_session | Launch the existing reader under its separate lock |
| `approve` | Review.approve | Commit choices explicitly; backup; invalidate affected completed chunks only |
| `translate` | Pipeline.translate | Process the next N unfinished chunks, including stale chunks, with P2-P5 per chunk |
| `status` | Projects.status | Local progress with no provider contact |
| `export` | Exports.export_text | Existing text products and optional strictly encoded copy |
| `profiles` | Operations.profiles | Existing redacted configuration/resolution report |
| `doctor` | Operations.doctor | Existing configuration and optional no-turn Codex protocol checks |
| `attempts` | Operations.attempts | Offline historical evidence listing/detail and path validation |
| `usage` | Operations.usage | Historical physical-attempt accounting without generating new rows |
| `catalog-import` | Operations.import_catalog | Validate and atomically activate a catalog without rewriting historical pricing |
| `discover` | Operations.discover | Explicit provider metadata discovery for selected pass/profile |
| `smoke` | Operations.smoke | One explicitly authorized live structured-output request, not a full pipeline E2E test |

### 9.1 Options and defaults to freeze

All commands keep `--project` and `--quiet`.

For `import`, `analyze`, and `translate`, preserve `--host`, `--port`, `--model`, `--context-size`, `--thinking`, `--allow-model-change`, `--profile`, and repeatable `--pass-profile`.

Import-specific behavior includes the positional folder and `--previous-volume`, `--opf`, `--input-encoding`, `--chapter-mode`, `--chapter-selector`, `--include`, `--sidecar-txt`, and `--whole-section-limit`. Preserve `chapter-mode=auto` and the omitted limit's continuation inheritance versus standalone 10000 behavior.

Keep the review defaults `--bind 127.0.0.1`, `--review-port 8765`, and `--no-browser`; reader defaults use `--reader-port 8766`. Port zero still selects a free port.

Keep `translate --continue` default 5; zero means all unfinished chunks; a negative value is rejected. Keep `approve --accept-defaults` opt-in, export's UTF-8 default and `--output` requirements, attempts' optional `--attempt`, and discover/smoke's `--pass` default 1 with allowed values 1-5.

`smoke --live` remains mandatory. Do not authorize a live turn merely because the caller uses Python instead of the CLI; the application command must contain an explicit acknowledgement corresponding to that flag.

Preserve argparse help/error exits and the current runtime success/error/interruption exits, including 130 for interruption. Snapshot stdout/stderr for representative non-TTY and quiet cases. Moving `print_json` out of operations must not move JSON output to stderr or mix progress into it.

## 10. Existing HTTP contracts

Do not change these endpoints, payloads, response fields, error envelopes, or status codes while replacing their implementation dependencies.

### 10.1 Terminology reviewer

| Method and path | Application target / preserved meaning |
| --- | --- |
| `GET /api/review` | Review draft snapshot including `_revision` |
| `GET /api/terms/{id}/evidence` | Canonical source/P5 evidence with existing availability and pending-choice warnings |
| `PATCH /api/terms/{id}` | Patch the allowed draft fields; optional legacy `_revision` guard |
| `POST /api/review/bulk` | Review explicit `term_ids`; mandatory loaded `revision`; all-or-nothing |
| `POST /api/confirm` | Set draft `confirmed`; optional legacy `revision`; does not run committed approval |

Keep `/` and `/assets/review_filters.js`, the existing embedded UI, category labels, filtering/count logic, autosave/draft flushing, keyboard behavior, theme persistence, and error display.

Preserve the current JSON response envelope and conflict mapping. Do not silently make the optional single-item revision mandatory while bulk already requires it. Do not standardize `_revision` and `revision` across routes in this refactor.

### 10.2 Reader

| Method and path | Application target / preserved meaning |
| --- | --- |
| `GET /api/reader` | Metadata, progress, and marker snapshot |
| `GET /api/chapters/{id}` | Checkpoint-verified chapter view |
| `POST /api/context` | Context for exact `chapter_id`, `block_id`, and `position` |
| `POST /api/markers` | Create marker with mandatory revision; 201 for new, 200 for a duplicate |
| `DELETE /api/markers/{id}` | Delete with exact revision payload |

Keep `/`, `/assets/reader.js`, `/assets/reader.css`, chapter navigation, formatting, marker gestures, Context Helper, reading progress, settings, and browser-local persistence keys.

Reader mutation bodies retain the existing JSON content-type requirement, origin check, 64 KiB limit, field validation, and status/error mapping. Preserve reviewer-specific limits and behavior separately; the two servers currently do not have identical HTTP validation policies.

Both servers retain their default bind addresses, no-browser options, port-zero behavior, security headers/CSP, and warnings for unauthenticated non-loopback exposure. Do not change network exposure while extracting application logic.

## 11. Behavior and persistence invariants

### INV-1: Frozen source and planning identity

Preserve `book.json` format and the exact `plan_fingerprint` algorithm. Source/chunk structure remains frozen. Existing permitted in-place edits, including titles and `thread_id`, must not become forbidden merely because a new DTO is stricter.

Import must preserve OPF/NCX/navigation reading order, encoding handling, inline emphasis, source extraction, current matter classification, natural section/scene planning, stable block/chapter/chunk/sentence IDs, and sidecar overwrite protection. Do not improve publisher heuristics here.

`book.json` remains the final completed-import marker. Do not publish it before required configuration, chunk registration, and continuation initialization are complete.

### INV-2: Exact request/checkpoint identity

Preserve the canonical task fingerprint computed from the project prompt, canonical inputs, and canonical schema. Refactoring paths, new DTO type names, presentation messages, application method names, and facade versions must not enter that identity.

Keep artifact paths and task keys such as `pass1/<analysis-unit>` and `passN/<chunk>`. Existing successful checkpoints must be reusable with their original provenance.

Do not reserialize project prompts or alter response schemas as a side effect of moving files. A changed serializer or resource path can accidentally invalidate every checkpoint even when the new code looks functionally equivalent.

### INV-3: Resume and recovery

Preserve `analysis_plan.json`, `analysis_inputs/*.json`, analysis receipts, idempotent merges, accepted checkpoints, and compatibility recovery of completed attempts.

Keep the narrow recovery normalization of transient retry fields. Do not weaken semantic compatibility to accept a different source/prompt/model/effort, and do not strengthen it accidentally with new wrapper metadata.

Missing or corrupt registered results are actionable errors. They are not permission to rerun completed inference silently. A code refactor must never hide corruption by treating it as a cache miss.

### INV-4: Pipeline ordering and approval gate

P1 runs over the full analysis plan before translation. Review preparation follows P1. Browser confirmation and committed approval remain separate. P2-P5 remain per selected chunk in the current order.

Re-running a completed command must reuse accepted work. `--continue N` counts unfinished translation units, not model calls, sections, passes, or total units ever processed.

Preserve continuity selection by prior source order, chapter, and explicit thread; preserve memory budgets and spoiler restrictions. Do not inject future narrative context during a resumed run.

### INV-5: Draft review semantics

Extract these rules from `ReviewRepository` without changing them:

- Only `select`, `custom`, `reviewed`, and `user_notes` are patchable through the existing route.
- Candidate selection and custom values retain their current validation and whitespace/CR handling.
- A changed lexical decision requires review again unless the same operation explicitly marks it reviewed; lexical edits or reopening a term clear draft confirmation.
- Notes are annotations. Editing a note alone does not reopen a confirmed glossary.
- Individual and bulk review methods retain their provenance fields.
- Bulk review validates the whole selection before writing, affects only unreviewed selected IDs, preserves existing choices/notes, and creates its current restore point.
- Confirmation requires valid choices and all terms reviewed in the browser workflow.
- Draft revisions remain derived from document content and are not persisted as `_revision` fields.
- Legacy hydration of missing review fields/observations is preserved without overwriting lexical decisions.

A review load currently may hydrate and write missing fields. Do not claim that every operation named `load` is byte-read-only, or remove that compatibility behavior without an explicit separate change.

### INV-6: Committed approval and invalidation

`approve` validates book fingerprint, analysis revision, exact term IDs, and selected/custom choices. It preserves the explicit accept-defaults behavior. It uses stored candidates as the authority where the existing implementation does so.

Retain the SQLite backup API before approval, human-choice history, committed term updates, approved flag, memory/lexicon exports, and review revision refresh. Mark only completed chunks dependent on changed committed choices as `stale`. Keep their old final artifacts and readable output until regeneration.

Do not make a draft patch write directly into committed terminology. Do not equate `reviewed=True`, `confirmed=True`, and `approved=True`.

### INV-7: Reader and review evidence integrity

Read translated text only from P5 result paths registered in the checkpoint database and verified against stored hashes. A newer unregistered artifact on disk is not an authoritative replacement.

Preserve canonical block/piece reassembly, inline formatting ranges, read-only evidence alignment, partial/unavailable/error/stale statuses, and the distinction between a draft choice and the committed translation's choice.

Context Helper remains local and respects current source-position visibility, aliases, ambiguity handling, inherited context, and exclusion of structural contents evidence. Do not turn it into a model call or silently change matching rules.

### INV-8: Marker semantics

Keep `translation.review.json` format, stable marker IDs, required revisions, duplicate-location behavior, create/delete validation, and exact quote matching.

Offsets are Unicode code-point offsets in the displayed canonical translated text, not JavaScript UTF-16 code units or bytes. Preserve existing browser conversion/range tests.

Persisted markers with old quotes must not be automatically deleted because a newer translation differs. Keep the distinction between structural validation of saved markers and validation of a newly submitted location.

### INV-9: Text products and export safety

Keep the contiguous available prefix, chapter text files, status output, and visibility of old stale finals. Preserve strict legacy-encoding conversion and errors instead of replacement characters.

Protect internal project files from `--output` writes; in-project additional copies remain restricted to `exports/`. Preserve atomic replacement and the timing of incremental exports. Do not add an EPUB writer.

### INV-10: Model calls and mandatory evidence

Preserve all three native transports and existing selection semantics. Keep canonical and compact P1 representations, decoding, validation, conservative repair, accepted checkpoint reuse, and physical-attempt accounting distinct.

Every physical attempt retains current mandatory evidence: semantic and transport requests/schemas, raw and partial output, response metadata, usage, pricing, validation diagnostics, and accepted-checkpoint state. Preserve credentials redaction and the separation between generation, validation, evidence durability, and acceptance.

Keep unknown/unavailable usage distinct from zero. Preserve `observability_hold`, including the existing ordering that allows reuse of an accepted checkpoint before considering a new generation.

Keep bounded validation retries, existing llama.cpp physical-attempt sampling variation, and the prohibition on blindly retrying transport, length, preflight, or evidence-write failures. An evidence-storage failure must not be reclassified as bad model JSON and cause another model turn.

### INV-11: Configuration and continuation

Preserve profile precedence: command per-pass override, command profile override, saved pass assignment, then saved default. Existing project definitions continue to override same-named built-ins. Command-only profile overrides must not become persistent settings changes.

Preserve model-change acknowledgement, tokenizer identity, context/planning budgets, settings migration timing, catalogs, credential references, and provider-specific options. Offline/no-generation commands must not acquire new inference side effects through a shared factory.

Continuation preserves inherited settings, optional catalog, exact prompt content, compact approved memory, lineage, seed hashes, and known path-remapping rules. A continuation destination with conflicting configuration remains an error rather than being overwritten.

Do not open/migrate the predecessor database as part of a convenience project constructor. Preserve the existing limited legacy predecessor mutation: creating first-volume `series.json` when missing. Do not claim that predecessor handoff is completely write-free.

### INV-12: Existing files and durability

Keep at least the following contracts recognizable and compatible:

| Artifact | Role to preserve |
| --- | --- |
| `book.json` | Frozen import manifest and completed-import marker |
| `settings.json`, project `prompts/*.txt`, optional `catalog/models.json` | Effective saved configuration and exact project instructions |
| `state.sqlite3` | Existing application facts, accepted jobs, merge receipts, terminology, chunk dependencies, and history |
| `analysis_plan.json`, `analysis_inputs/*.json` | Resumable P1 planning and input snapshots |
| `book_memory.json`, `lexicon.approved.json` | Existing exported memory and committed lexicon |
| `terms.review.json`, `terms.review.html` | Editable review draft and generated read-only catalog |
| `translation.review.json` | Reader markers |
| `translation.txt`, `translated_chapters/*.txt`, `translation.status.json` | Existing text products |
| `series.json`, `series.seed.json` | Continuation lineage and immutable inherited seed |
| `history/`, `backups/`, `artifacts/` | Existing restore points, checkpoints, and communication evidence |

Use SQLite's backup facility rather than copying a live WAL database file. Preserve WAL/FULL/foreign-key settings and short transaction boundaries. Preserve atomic JSON/text replacement, flushing, and directory durability behavior in the existing utilities.

There is no automatic atomic transaction spanning SQLite and all JSON/text files. Do not claim that adding a Unit of Work makes them one transaction. Preserve current write ordering and recovery behavior; test failure points. Do not wrap a whole model turn, translation run, or interactive server session in one SQLite transaction.

The generated static review HTML is an existing output, even though its current renderer lives in Store. Move rendering to an outbound presentation/export component without deleting the artifact or changing when it is generated.

## 12. Concrete extraction plan

The implementation should make vertical, verifiable cuts. Do not create a second parallel pipeline and later attempt to reconcile it with the old one.

### 12.1 CLI and project operations

Extract `effective_settings`, model compatibility checks, import orchestration, status data assembly, strict export policy, and the live smoke procedure from `cli.py`. Keep CLI-specific parsing such as the textual `P=PROFILE` grammar at the adapter boundary, passing a validated mapping to services.

Use a shared configuration service rather than reproducing precedence in CLI and application methods. Preserve the existing difference between scalar overrides, command-only profile selection, standalone import configuration, and inherited configuration.

Do not make a universal project-opening helper perform every migration/validation for every command. The current diagnostics branch does not have exactly the same Store initialization and manifest checks as analyze/translate. Characterize these differences before centralizing them.

### 12.2 Engine, persistence, and policies

Move use-case orchestration to application services and Runner coordination to a focused execution component. Extract pure validation, hashing, merge-choice, and memory-selection transformations incrementally.

Replace `with store.db:` and raw SQL outside infrastructure with explicit transaction/capability operations. The application decides which mutations belong together; infrastructure implements the transaction. Avoid one unrelated repository class for every existing SQL table.

Keep native transport behavior and `SemanticRequest` compatible. Retain existing schema/codec modules if their location is not an architectural obstacle. Do not change their public JSON contract during the move.

### 12.3 Review and approval

Split `ReviewRepository` into application operations and a JSON document adapter. The application owns allowed changes, revision semantics, confirmation rules, and atomic bulk mutation orchestration. The document adapter owns bytes, atomic replace, and restore-point storage.

Do not put a callback containing arbitrary presentation-layer policy inside the storage adapter. Business rules should be ordinary independently testable functions or service methods.

Extract committed approval from Store into the review application service using persistence/backup/export capabilities. Draft editing and committed approval share domain concepts but remain distinct operations.

### 12.4 Reader and evidence

Move marker validation and reader query orchestration behind the reader service. Extract read-only SQL/artifact access from `ReaderContext` and `EvidenceReader` behind read capabilities; preserve their different response and error-handling semantics.

Share low-level hash/path/block primitives only where semantics are genuinely identical. Do not merge the two read models into a single generic function that loses reviewer partial-evidence behavior or reader prefix/formatting behavior.

### 12.5 Compatibility and resource lookup

Keep thin compatibility re-exports/wrappers for existing imported symbols where needed by tests, experiments, and scripts: engine entry points/Runner, Store access used by fixtures, review/reader server helpers and conflicts, and CLI main.

Wrappers delegate in one direction and do not contain a second implementation of the rules. Application code must never call back through these presentation compatibility modules.

Audit all `__file__`-relative resource lookups before moving code. Defaults, prompts, catalogs, assets, and the Codex base instruction resource must resolve to the same files. Centralize bundle/resource discovery without changing project-specific override behavior or prompt newline bytes.

## 13. Test strategy and preservation evidence

### 13.1 Existing tests are assets, not obstacles

Retain coverage in:

- `tests/test_pipeline.py`
- `tests/test_review.py`, `tests/test_review19.py`
- `tests/test_reader.py`
- `tests/test_series.py`
- `tests/test_transports.py`
- `tests/test_p1_compact.py`, `tests/test_pass1_compact_experiment.py`
- `tests/test_review_filters.cjs`, `tests/test_reader_ranges.cjs`
- `tests/browser_review_smoke.py` when its optional browser dependencies are available.

Moving imports/monkeypatch seams can require test changes. Preserve behavioral assertions and explain any seam adjustment. Do not delete a failing test, relax an assertion, or replace a real integration test with a mock solely to make the refactor pass.

The pipeline suite already has a local HTTP/SSE fake provider, call counters, resume tests, and validation/invalidation tests. Reuse that infrastructure. Its P1 example recognizes `Relay`; it can produce no terms for an Andersen story. Therefore an empty-review smoke alone is insufficient evidence that the extracted reviewer works. Add a fixture-aware scripted response or a separate nonempty-review scenario.

### 13.2 Fixture tiers

| Fixture/tier | Required use |
| --- | --- |
| Existing synthetic fixtures and fake provider | Fast deterministic regression gate; no real model required |
| `andersen-smoke.epub` | Small full-path application smoke using disposable extracted input and deterministic provider responses |
| `andersen-mini.epub` | Multi-section ordering, P1 aggregation, per-unit limits, and restart/resume exercises |
| Small smoke with explicitly configured local model | Opt-in real transport/prompt/parser verification after deterministic gates pass |
| `time-machine-demo.epub` and copies of existing work | Opt-in acceptance/quality exercise and realistic project compatibility verification |

The sample README records one section/384 words for smoke, three sections/2601 words for mini, and 18 translation-eligible units/32445 words for Time Machine including Contents. Nominal model-call counts assume the documented planning and successful first attempts; do not hard-code those counts into tests without fixing provider budgets and retry behavior.

The audited importer consumes an unpacked directory. Extract the repository-owned EPUB fixtures into a disposable test directory with a path-safe test helper. Do not add archive import to the product to make tests convenient. Keep input extraction outside the project output directory.

The deterministic end-to-end acceptance path ends with valid current text outputs and reader data, not with a translated EPUB. The fixture's Contents discussion must not become a requirement to implement a new exclusion interface.

### 13.3 Required test groups

| ID | Test group | Required evidence |
| --- | --- | --- |
| T01 | Direct application use | Import/analyze/review/approve/translate/export and queries work without CLI parsing, Rich construction, HTTP serving, or a browser |
| T02 | CLI contract | All 15 commands, relevant flags/defaults, help/errors, stdout/stderr, quiet behavior, and exit codes remain compatible |
| T03 | HTTP contracts | Existing reviewer/reader routes keep payload shapes, revisions, statuses, error envelopes, and static-resource behavior |
| T04 | Pipeline ordering | Full P1 before approval; confirmation alone does not commit; P2-P5 per unfinished chunk; limit semantics unchanged |
| T05 | Restart and checkpoint identity | Baseline-generated projects resume with unchanged accepted artifacts/fingerprints and no redundant successful model calls |
| T06 | Review rules | Nonempty draft, individual/bulk edits, notes, custom forms, optional/mandatory revision differences, confirmation, stale revisions, and restore points |
| T07 | Approval and invalidation | SQLite backup, exact term coverage, accepted defaults opt-in, dependency-only stale marking, and preserved old output |
| T08 | Reader/context/markers | Read-only checkpoint access, hash checking, canonical pieces, Unicode offsets, formatting, partial/stale views, no spoilers, duplicate markers, and revision conflicts |
| T09 | Configuration/series | Saved/command precedence, built-ins, legacy migration, inheritance, path remapping, prompts, seed/lineage, and predecessor restrictions |
| T10 | Transport/evidence | Existing native transports, compact/canonical recovery, usage/pricing/redaction, observability hold, and retry distinctions |
| T11 | Resources/concurrency | No nested self-locking; second writer/reader refusal; reader alongside translation; no cross-thread mutable SQLite connection; cleanup on errors/interruption |
| T12 | Architecture | Forbidden import/call edges fail tests; application factory is inert; adapters actually call application services |

Use integration tests with real temporary SQLite/files for persistence guarantees. A mock repository returning a desired result does not prove that backup, locking, hash verification, or atomic updates still work.

### 13.4 Old-to-new compatibility, not only new-to-new smoke

Before moving production code, create reproducible snapshots using the audited/pre-refactor implementation and deterministic responses. Cover at least:

- Imported, before analysis.
- Partially analyzed, with P1 receipts/input snapshots.
- Analysis complete with nonempty pending review, custom choices, and notes.
- Draft confirmed but not committed through `approve`.
- Approved with no prose generated yet.
- Interrupted P4 with P2/P3 accepted.
- One completed chunk and additional unfinished chunks.
- A completed project and a project with stale output after a terminology change.

Run the new code against copies of those states in fresh processes. Prove that accepted work is reused, old drafts survive, and the next command behaves as before. Also preserve existing completed-attempt recovery tests, including compact/canonical cases.

Compare immutable artifact bytes, canonical identities, and relevant logical SQLite rows. Do not compare raw SQLite database bytes as a general logical-equivalence test because transactions/WAL representation can differ. Normalize only explicitly incidental values such as a disposable root path or timestamps in newly generated test records; do not normalize away source IDs, request content, retry counts, or meaningful metadata.

### 13.5 Failure and concurrency tests

Test missing/corrupt accepted artifacts, stale revisions, invalid bulk selections, changed model without acknowledgement, unavailable usage, strict encoding failure, and local evidence-write failure. Confirm that these failures do not erase successful work or trigger an unintended extra model call.

Exercise real lock scopes in separate processes where appropriate. Test that application methods do not reacquire their own session lock, and that provider/store/session cleanup occurs after exceptions and interruption.

Read-only reader/evidence queries must not create a missing database or run schema migrations. Preserve missing-translation responses rather than silently initializing an empty project.

### 13.6 UI regression without UI redesign

Keep the existing browser assets unchanged wherever possible. Use HTTP contract tests and both JavaScript suites. Run the optional reviewer browser smoke when Playwright and Chromium are provisioned; record an explicit skip reason otherwise.

The browser smoke exercises real HTTP handlers and existing interactions, including scoped counts, draft flushes, bulk review, and stable actions. Do not replace it with screenshots of a newly designed page.

### 13.7 Live testing policy

Normal tests must not use real provider credentials, discover an unrequested remote service, or start a billable/subscription model turn. Make network use explicit in the test design: existing local mock HTTP integration is permitted; real inference is opt-in.

For live smoke, use the user's explicitly supplied local endpoint/profile/model configuration in a disposable project. No cloud fallback, automatic profile substitution, or silent use of built-in Codex credentials is allowed. The absence of a reachable local model is a reported unrun integration check, not permission to replace it with paid inference.

Do not require deterministic prose from a real model. Check pipeline completion, structural validation, retained evidence, output coverage, and useful diagnostics separately from human translation-quality assessment.

Never run a destructive test against the only copy of an ongoing translation. Copy/quiesce a whole workspace consistently, using SQLite backup for an active database where appropriate. Preserve original sources, prompts, checkpoints, review drafts, and credentials. Runtime/auth/evidence files excluded by `.gitignore` must not be committed.

## 14. Staged implementation and exit gates

Each stage should end with a small coherent commit and recorded verification. Continue until all stages and applicable acceptance criteria are complete; adding a facade is not the finish line.

### Stage 0: Reconcile the baseline and freeze behavior

Inspect current code, existing instructions, Git status, and all exposed commands/routes. Record differences from the audited SHA. Run existing tests before refactoring and capture existing failures honestly.

Add a preservation ledger mapping each command/route and invariant to tests. Establish deterministic fixture extraction and baseline snapshots. Do not invoke real models during this step.

**Exit gate:** current interfaces and persisted contracts are inventoried; baseline behavior has executable protection; unrelated changes and real workspaces are untouched.

### Stage 1: Introduce the boundary and resource scopes

Create application inputs/results, small ports, explicit composition, and operation/review/reader scopes. Add inert-construction tests, lock ownership tests, and dependency tests. Keep compatibility entry points.

**Exit gate:** a direct Python caller can enter correctly scoped operations without presentation objects; factory construction has no external side effects; there are no import cycles.

### Stage 2: Extract project and operational use cases

Move configuration resolution, import/continuation orchestration, status assembly, export-copy policy, diagnostics, catalog/discovery, and explicit live-smoke orchestration behind services. Convert one CLI command at a time.

Preserve resource lookups, existing command-specific validation ordering, and no-generation versus no-network distinctions. `doctor` may still run its local no-turn protocol probe; `status` must not contact a provider.

**Exit gate:** converted CLI handlers only decode, invoke, and render; defaults and persisted import/configuration results remain compatible.

### Stage 3: Extract pipeline execution and persistence policies

Move analyze/translate orchestration and Runner coordination behind application capabilities. Extract pure rules without changing outputs. Replace terminal dependencies and raw external Store connection access.

Test checkpoint reuse, partial P1 resume, P4 interruption, completed-attempt recovery, compact/canonical formats, context limits, and evidence failures after each cut.

**Exit gate:** direct Python calls and CLI produce the same semantic requests and accepted state; old work is reused; no new scheduler or state model exists.

### Stage 4: Extract review and committed approval

Move draft operations out of the HTTP module and approval orchestration out of Store. Wire the existing reviewer to a session-bound application service and preserve its full lifetime writer lock. Keep the static review catalog output.

**Exit gate:** HTTP and direct calls exercise the same rules; draft/committed state remains distinct; all review, approval, backup, and invalidation tests pass.

### Stage 5: Extract reader, markers, and read models

Move marker/application policy and read-only query orchestration behind services. Keep assets and HTTP contracts. Preserve separate locking, per-request connections, cache behavior, P5 verification, Unicode ranges, and knowledge cutoffs.

**Exit gate:** reader runs alongside translation as before; no database creation/migration from read paths; marker/context behavior is unchanged.

### Stage 6: Complete boundaries and verify end to end

Remove remaining adapter business logic, direct SQL/file mutations, duplicated validation, and temporary reverse dependencies. Keep only justified compatibility wrappers. Run the complete deterministic, HTTP, JavaScript, and old-to-new regression suite.

Run optional local smoke and real-workspace acceptance only when their prerequisites are explicitly available. Document exact results, skipped checks, and any remaining baseline defects separately from regressions.

**Exit gate:** all completion criteria below are satisfied, and the new API is documented with actual implemented signatures.

## 15. Commands and implementation evidence

The audited CI uses the following relevant checks. Preserve them and run both existing JavaScript suites explicitly:

```bash
uv lock --check
uv run python -m compileall -q bookpipe translate.py
uv run --group dev python -m pytest -q
node --test tests/test_review_filters.cjs tests/test_reader_ranges.cjs
```

New tests must be included in normal discovery unless explicitly marked as live/browser-dependent. Use `uv` for environment and dependency operations. Do not upgrade runtime dependencies or Python's minimum version solely to implement the service layer.

When the optional browser dependencies and browser binary are already provisioned, also run the existing `tests/browser_review_smoke.py` through the project's `uv` environment. Record the actual invocation and environment; do not report it as executed merely because its source was inspected.

During implementation, maintain `docs/implementation/application-layer-refactoring-report.md` containing:

- Actual starting and finishing commits and relevant local baseline differences.
- Final module/dependency map and public operation signatures.
- Command/route-to-service preservation ledger with test references.
- Test commands and actual results, including counts/failures/skips and live-test authorization/configuration without secrets.
- Old-project compatibility results and confirmation that accepted work was not regenerated unnecessarily.
- Any deviation from this design, with rationale and evidence of equivalent or stronger boundary enforcement without functional changes.

This report is an implementation deliverable, not something the current documentation-only commit claims to have completed.

## 16. Definition of done

The refactor is complete only when all of the following are demonstrated:

- [ ] All existing CLI commands/options and existing web operations are represented in the preservation ledger.
- [ ] Their current user-interface exposure remains unchanged; no new frontend, TUI, general HTTP API, or cross-interface feature parity was added.
- [ ] Every existing application operation has an explicit programmatic entry point independent of CLI parsing and HTTP serving.
- [ ] CLI and HTTP adapters invoke that entry point rather than duplicate its rules.
- [ ] Application/domain code has no forbidden presentation dependencies or raw infrastructure construction; dependency tests enforce this.
- [ ] SQL, atomic file writes, provider processes, and resource lifetimes are behind the intended capabilities.
- [ ] Project/review/reader locks have one owner, preserve baseline concurrency, and release on failure/interruption.
- [ ] Existing project formats, IDs, canonical request fingerprints, checkpoint paths, and accepted artifacts remain compatible.
- [ ] Baseline-generated partial projects resume correctly in fresh processes without redundant successful model turns.
- [ ] Draft review, browser confirmation, committed approval, and dependency-based staleness retain their distinct meanings.
- [ ] Reader/context/evidence/marker behavior, security-relevant transport checks, and Unicode offsets remain intact.
- [ ] Native transports, profile precedence, continuation inheritance, evidence durability, usage accounting, and retry behavior remain intact.
- [ ] Existing tests remain meaningful; new direct-application, contract, architectural, and compatibility tests pass.
- [ ] Optional browser/live/full-demo checks are either executed and reported accurately or explicitly marked unrun with reasons.
- [ ] No private book, working translation, authentication material, or runtime evidence was added to Git.
- [ ] The implementation report documents actual API signatures, results, and any justified deviations.

A green smoke test alone, a new directory called `application`, or a wrapper around CLI subprocesses does not satisfy these criteria.

## 17. Codex execution instructions

Implement this document as a behavior-preserving refactor, not as a product redesign.

Start with Stage 0. Inspect repository instructions and actual code before editing. Use the current checkout as the source of truth, reconcile newer functionality with this audited baseline, and preserve unrelated work. Work on `main` when carrying out the user's explicitly requested main-branch workflow; do not create a branch or PR without a new instruction.

Make incremental commits with the relevant regression checks between stages. Do not reset, reimport, approve, translate, or otherwise modify the user's real project directories without explicit authorization; use disposable fixture projects and consistent copies for tests.

Keep code, comments, and new documentation in English. Preserve existing product output and translation content rather than changing languages as a side effect of this instruction.

Do not add planned-but-unimplemented features merely because they appear in an older PRD or conversation. Do not remove a current feature because this document's static audit did not see a later change. When a baseline defect appears, report it and isolate it from refactoring work instead of silently redefining behavior.

Finish with the implementation report, actual verification results, and committed changes. Be explicit about checks that were not run. The objective is one maintainable application core with thin existing adapters, not a different application that happens to pass a new test suite.

## 18. Source and design references

Repository links below are pinned to the audited commit so later code movement does not erase the basis of this specification. Function/class names in the preceding sections identify the relevant parts.

- [CLI]: commands, defaults, orchestration, lock selection, approval/export, and exit handling.
- [Engine]: Runner, validation/recovery, analysis planning, sequential translation, and text export.
- [Store]: SQLite schema/checkpoints, review generation, approval, invalidation, and memory.
- [Review] and [Review evidence]: draft rules, routes, revisions, and read-only source/P5 alignment.
- [Reader] and [Reader context]: markers, HTTP contracts, read models, context visibility, and canonical text.
- [Configuration], [Profiles], [Series], and [Providers]: resolution, inheritance, handoff, and native provider construction.
- [Operations], [Utilities], [UI], [Importer], and [Fixtures]: operational behavior and infrastructure/presentation seams.
- [Pipeline tests], [Review tests], [Reader tests], and [Browser smoke]: existing regression examples used in the audit.
- [Project configuration] and [CI]: declared Python/dependency/test environment.

External references support the architectural mechanisms, not a claim that all of their patterns must be implemented:

- [Service Layer]: application operations and coordination shared by multiple interfaces.
- [Python typing]: structural protocols and the distinction between type annotations and runtime validation.
- [Python contextlib]: deterministic resource scopes and ExitStack.
- [Python sqlite3]: connection/thread handling, transaction behavior, and connection backup.

[CLI]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/cli.py
[Engine]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/engine.py
[Store]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/store.py
[Review]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/review.py
[Review evidence]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/review_context.py
[Reader]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/reader.py
[Reader context]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/reader_context.py
[Configuration]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/project_config.py
[Profiles]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/profiles.py
[Series]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/series.py
[Providers]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/provider_registry.py
[Operations]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/operations.py
[Utilities]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/util.py
[UI]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/ui.py
[Importer]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/bookpipe/importer.py
[Fixtures]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/examples/public-domain/README.md
[Pipeline tests]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/tests/test_pipeline.py
[Review tests]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/tests/test_review19.py
[Reader tests]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/tests/test_reader.py
[Browser smoke]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/tests/browser_review_smoke.py
[Project configuration]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/pyproject.toml
[CI]: https://github.com/hipotures/intelitex/blob/87a13ee2a952f773dbecaabd4e01b5e802e96aff/.github/workflows/ci.yml
[Service Layer]: https://martinfowler.com/eaaCatalog/serviceLayer.html
[Python typing]: https://docs.python.org/3.11/library/typing.html#typing.Protocol
[Python contextlib]: https://docs.python.org/3.11/library/contextlib.html#contextlib.ExitStack
[Python sqlite3]: https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup
