# Intelitex Architecture

## Purpose

This document describes the maintained architecture of Intelitex after the application-layer refactoring. It is a description of the current system, not an implementation plan or historical PRD.

User-facing commands, provider configuration, translation workflow, Reader behavior, series continuation, and compact Codex Pass-1 behavior are documented in the repository `README.md`. This file focuses on code boundaries and extension rules.

Last architecture cleanup baseline: `fc78055d5be409e3a6719052fb5985cd96a77b7f`.

## System shape

Intelitex is a modular Python application with multiple interface adapters around one shared application layer.

```text
                         +----------------------+
                         |  Application Layer   |
                         |                      |
CLI -------------------->| Projects             |
Terminology Review HTTP ->| Review              |
Reader HTTP ------------>| Reader               |
Future adapters -------->| Pipeline             |
                         | Operations           |
                         | Exports              |
                         +----------+-----------+
                                    |
                +-------------------+-------------------+
                |                   |                   |
             SQLite              Project files      LLM providers
             Store               and artifacts      / evidence
```

The core rule is:

> Business behavior belongs behind the application API. CLI and HTTP code decode interface-specific input, call application services, and render interface-specific output.

Interface parity is not required. A feature may remain web-only or CLI-only while still being callable programmatically through the application layer.

## Entry points and adapters

### CLI

- Entry point: `translate.py`
- Adapter: `bookpipe/cli.py`
- Presentation: `bookpipe/ui.py`

The CLI owns:

- argument parsing;
- CLI-only syntax such as repeatable `--pass-profile P=PROFILE`;
- construction of semantic application commands;
- terminal rendering and exit-code mapping.

The CLI must not own project locks, manipulate SQLite directly, implement pipeline policy, or parse progress text back into state.

### Terminology Review

- HTTP/UI adapter: `bookpipe/review.py`
- Application service: `bookpipe/application/review.py`

The HTTP adapter keeps transport concerns such as routes, request decoding, response status codes, browser launch, and embedded UI assets. Review draft rules, revision conflicts, confirmation, evidence access, and committed approval live behind the application boundary.

Draft review, browser confirmation, and committed approval are separate states and must remain separate.

### Translation Reader

- HTTP/UI adapter: `bookpipe/reader.py`
- Application service: `bookpipe/application/reader.py`
- Read model: `bookpipe/reader_context.py`

Production Reader HTTP routes call only the public `ReaderSession` methods:

- `load()`
- `metadata()`
- `progress()`
- `chapter(...)`
- `context(...)`
- `create_marker(...)`
- `delete_marker(...)`

Compatibility adapters may preserve old direct imports, but new interface code must target the public session API rather than `ReaderContext` or marker storage directly.

## Application layer

The public application package is `bookpipe/application/`.

```text
application/
  api.py          service facade
  commands.py     explicit operation inputs
  results.py      detached operation results
  ports.py        outbound capabilities
  sessions.py     lock/resource ownership
  projects.py     import/configuration/status
  pipeline.py     analyze/translate orchestration
  review.py       draft review and approval
  reader.py       reader/marker operations
  operations.py   diagnostics/discovery/smoke/reports
  exports.py      text export orchestration
```

`bookpipe.bootstrap.create_application(...)` is the composition root. Constructing the application is intentionally inert: it does not open a project, create a database, contact a provider, resolve credentials, bind a socket, or start a process.

The service facade exposes the maintained programmatic operations:

```text
app.projects.import_book(...)
app.projects.status(...)

app.pipeline.analyze(...)
app.pipeline.translate(...)

app.review.open_session(...)
app.review.approve(...)

app.reader.open_session(...)

app.exports.export_text(...)

app.operations.profiles(...)
app.operations.doctor(...)
app.operations.attempts(...)
app.operations.usage(...)
app.operations.import_catalog(...)
app.operations.discover(...)
app.operations.smoke(...)
```

New interfaces should build on these services instead of invoking the CLI as a subprocess.

## Commands and results

Application commands are typed dataclasses in `bookpipe/application/commands.py`.

Interface syntax must be decoded before constructing them. For example:

```text
CLI: --pass-profile P3=local
                  |
                  v
Application command: pass_profiles = {3: "local"}
```

The application receives a semantic, immutable `Mapping[int, str]`; it does not know the CLI spelling.

Results returned from services are detached snapshots or result dataclasses. A presentation adapter must not receive live SQLite connections, cursors, provider processes, or mutable Store internals.

## Progress model

Runtime progress is presentation-neutral.

`bookpipe/progress.py` defines `ProgressEvent`, and application/runtime code publishes events only through:

```python
progress.emit(ProgressEvent(...))
```

Events carry structured fields such as:

- event kind;
- pass number;
- task key;
- chapter/unit/chunk identifiers;
- completed/total counts;
- provider input measurement;
- received answer/reasoning character counts.

Human terminal labels are created only in `Display.emit()`.

Do not make a future web interface parse strings such as `P3/5 | pass3/...` to recover state. Use structured event values and persisted application state.

The current application API remains synchronous. A future web processing UI may add an execution/job host above the application layer, but should not move business logic into that host.

## Resource ownership and locks

Resource ownership is explicit in `bookpipe/application/sessions.py`.

### Normal project operations

`OperationScope` owns:

- the project writer lock `.lock`;
- a lazily created Store;
- a lazily created provider pool;
- deterministic reverse-order cleanup.

Import, analysis, translation, approval, export, status, and operational commands use this scope as appropriate.

### Review

`ReviewSession` keeps the project writer lock for the lifetime of the interactive review session. Review mutations additionally use the session's in-process mutex around revision checking and atomic replacement.

### Reader

`ReaderSession` uses the separate `.reader.lock`, not the project writer lock. The Reader is therefore allowed to run while translation owns `.lock`.

Reader checkpoint queries remain read-only and use the existing read-model behavior. Do not change Reader to open the mutable Store merely for convenience.

## Persistence and durable contracts

The current filesystem/SQLite representation is part of application compatibility.

Important artifacts include:

| Artifact | Purpose |
| --- | --- |
| `book.json` | frozen imported source/chunk manifest and completed-import marker |
| `settings.json`, project prompts, optional catalog | saved project configuration |
| `state.sqlite3` | application facts, accepted jobs/checkpoints, terminology, dependencies, history |
| `analysis_plan.json`, `analysis_inputs/` | resumable Pass-1 planning/input snapshots |
| `book_memory.json` | current structured terminology/observation memory |
| `terms.review.json` | editable terminology-review draft |
| `lexicon.approved.json` | committed approved terminology export |
| `translation.review.json` | Reader marker state |
| `artifacts/` | model attempt evidence and checkpoint results |
| `translation.txt`, `translated_chapters/`, `translation.status.json` | current readable text products |
| `series.json`, `series.seed.json` | continuation identity and inherited memory |

Accepted task identity and checkpoint reuse must not depend on interface names, UI layout, progress labels, or new wrapper types.

Completed or stale results are not silently recomputed because an interface changes. Missing or modified registered artifacts are errors, not cache misses.

## Pipeline invariants

The maintained workflow is:

```text
import
  -> Pass 1 over the analysis plan
  -> terminology review draft
  -> human review / confirmation
  -> explicit approval
  -> P2 -> P3 -> P4 -> P5 per selected unfinished translation unit
  -> incremental text products / Reader
```

Key invariants:

- import plans and freezes source structure without prose generation;
- Pass 1 completes before translation;
- review confirmation does not itself commit approved terminology;
- approval is explicit;
- P2-P5 are sequential per translation unit;
- accepted checkpoints are reused;
- validation fails closed;
- stale translated output remains readable until regenerated;
- provider/evidence failure must not be mistaken for bad model JSON and trigger blind duplicate inference.

## Providers and evidence

Provider construction is injected through the composition root. Native transports remain in their provider modules.

Current provider families include:

- llama.cpp;
- OpenAI Responses;
- Codex app-server.

Model/profile selection is configuration data. Application commands can override semantic profile selection without changing accepted historical work.

Physical attempts retain durable communication evidence. Generation completion, output validation, evidence durability, and checkpoint acceptance are distinct states.

Never infer missing usage as zero, and never expose credentials through reports or evidence.

## Compatibility wrappers

Some historical public imports remain as thin compatibility entry points, for example engine orchestration helpers and Reader/Review repository names.

Compatibility wrappers may delegate to the application implementation. They must not become a second source of business rules.

When adding new code, prefer the application API rather than compatibility entry points.

## Extension rules

### Adding a new use case

1. Define an application command/result when useful.
2. Put orchestration and workflow validation in an application service.
3. Add or reuse a narrow outbound capability for I/O when necessary.
4. Keep persistence implementation in infrastructure/Store/filesystem modules.
5. Expose the use case from an adapter only after the programmatic operation exists.
6. Add direct application tests before adding interface-specific tests.

### Adding a new interface

A new web UI, TUI, desktop client, or automation adapter should:

- translate its own input into semantic application commands;
- call application services;
- consume structured `ProgressEvent` data where progress is needed;
- maintain its own presentation/navigation state;
- avoid direct SQLite and project-file mutations;
- avoid invoking `translate.py` as a subprocess to reach business behavior.

### Adding provider behavior

Provider-specific protocol, streaming, counting, and process/network behavior belongs in provider transport modules. The pipeline should continue to operate through the existing semantic request/evidence boundary.

### Changing persistence

Treat persistence formats, task fingerprints, and accepted checkpoints as compatibility contracts. Any intentional migration requires its own design, backups, old-project tests, and explicit migration path.

## Testing and CI

Architecture is protected by executable tests, including guards that verify:

- application/domain modules do not import presentation modules;
- application code does not construct concrete SQLite/provider/CLI processes;
- CLI does not own project locks or Store internals;
- CLI pass-profile strings are decoded into semantic mappings;
- pipeline code does not call terminal-shaped progress methods;
- P1-P5 starts expose structured identifiers;
- Reader HTTP delegates through the public Reader session.

At the architecture cleanup baseline, the full Python suite contains 228 passing tests and the two JavaScript suites contain 31 tests total.

CI also verifies the lockfile and Python compilation.

## Documentation policy

Keep this document focused on the maintained architecture. Do not accumulate completed PRDs, implementation prompts, or one-off migration reports under `docs/`.

Historical design and implementation material remains available through Git history.

Empirical model/profile benchmark data that remains useful for future decisions belongs under `docs/research/` and should be consolidated rather than stored as one report per run.
