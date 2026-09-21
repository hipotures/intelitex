# Application Layer Refactoring Report

## Baseline and scope

- Audited production baseline: `87a13ee2a952f773dbecaabd4e01b5e802e96aff`.
- Starting checkout: `3707d3a` on `main` (the audited production tree plus only
  `docs/prd/application-layer-refactoring.md`).
- Finishing implementation commit: `f0dd980` on `main`; this report is the
  following documentation-only delivery commit.
- Starting worktree: clean. No unrelated local changes or translation workspaces
  were present or modified.
- Runtime baseline: Intelitex 1.11.0, Python >= 3.11.
- Product scope remains unchanged: the existing CLI owns import, analysis,
  approval, translation and export interactions; the existing local web adapters
  own terminology review and reading/markers.

Stage 0 established the following deterministic baseline without provider turns:

| Check | Result before production changes |
| --- | --- |
| `uv lock --check` | passed |
| `uv run python -m compileall -q bookpipe translate.py` | passed |
| `uv run --group dev python -m pytest -q` | 216 passed |
| `node --test tests/test_review_filters.cjs tests/test_reader_ranges.cjs` | 31 passed |

The existing integration tests already construct projects under pytest temporary
directories and exercise real SQLite and filesystem artifacts. The application
refactor tests continue that convention; no real project is used as a fixture.

## Preservation ledger

| Contract | Application target | Preservation coverage |
| --- | --- | --- |
| `import` | `ProjectsService.import_book` | `test_application_baseline.py`, `test_pipeline.py`, `test_series.py` |
| `analyze` | `PipelineService.analyze` | `test_pipeline.py`, `test_p1_compact.py` |
| `review` | `ReviewService.open_session` | `test_review.py`, `test_review19.py` |
| `reader` | `ReaderService.open_session` | `test_reader.py` |
| `approve` | `ReviewService.approve` | `test_pipeline.py`, `test_series.py` |
| `translate` | `PipelineService.translate` | `test_pipeline.py`, `test_p1_compact.py` |
| `status` | `ProjectsService.status` | `test_pipeline.py`, application tests |
| `export` | `ExportsService.export_text` | `test_pipeline.py`, application tests |
| `profiles` | `OperationsService.profiles` | `test_transports.py`, application tests |
| `doctor` | `OperationsService.doctor` | `test_transports.py`, application tests |
| `attempts` | `OperationsService.attempts` | `test_transports.py`, application tests |
| `usage` | `OperationsService.usage` | `test_transports.py`, application tests |
| `catalog-import` | `OperationsService.import_catalog` | `test_transports.py`, application tests |
| `discover` | `OperationsService.discover` | `test_transports.py`, application tests |
| `smoke` | `OperationsService.smoke` | `test_transports.py`, application tests |
| `GET /api/review` | `ReviewSession.load` | `test_review.py`, `test_review19.py` |
| `GET /api/terms/{id}/evidence` | `ReviewSession.evidence` | `test_review.py`, `test_pipeline.py` |
| `PATCH /api/terms/{id}` | `ReviewSession.patch_term` | `test_review.py`, `test_review19.py` |
| `POST /api/review/bulk` | `ReviewSession.review_terms` | `test_review19.py` |
| `POST /api/confirm` | `ReviewSession.set_confirmed` | `test_review.py`, `test_review19.py` |
| `GET /api/reader` | `ReaderSession.metadata`, `progress`, `load` | `test_reader.py` |
| `GET /api/chapters/{id}` | `ReaderSession.chapter` | `test_reader.py` |
| `POST /api/context` | `ReaderSession.context` | `test_reader.py` |
| `POST /api/markers` | `ReaderSession.create_marker` | `test_reader.py`, `test_reader_ranges.cjs` |
| `DELETE /api/markers/{id}` | `ReaderSession.delete_marker` | `test_reader.py`, `test_reader_ranges.cjs` |
| Reviewer `/` and `/assets/review_filters.js` | unchanged HTTP/UI adapter assets | `test_review19.py`, `test_review_filters.cjs` |
| Reader `/`, `/assets/reader.js`, `/assets/reader.css` | unchanged HTTP/UI adapter assets | `test_reader.py`, `test_reader_ranges.cjs` |
| Frozen manifests and task fingerprints | domain identity and existing engine contracts | `test_series.py`, `test_p1_compact.py` |
| Resume/recovery and accepted artifacts | pipeline execution | `test_pipeline.py`, `test_p1_compact.py` |
| Draft/approval/staleness distinctions | review and approval services | `test_review*.py`, `test_pipeline.py`, `test_series.py` |
| Verified P5 reads/context/markers | reader service and read model | `test_reader.py`, `test_reader_ranges.cjs` |
| Configuration/profile/continuation precedence | projects and operations services | `test_series.py`, `test_transports.py` |

## Implemented API and dependency map

`bookpipe.bootstrap.create_application(progress=None, *, provider_factory=None,
store_factory=None, plan_fingerprint_fn=None) -> Application` is inert. Factory
construction does not read a project, create SQLite, resolve credentials, contact
a provider, spawn a process, or bind a socket. The optional keyword factories are
composition/test seams; normal callers use no arguments or supply only a progress
sink.

The public service methods are:

```text
app.projects.import_book(ImportBookCommand) -> ImportResult
app.projects.status(StatusCommand) -> StatusResult
app.pipeline.analyze(AnalyzeCommand) -> PipelineResult
app.pipeline.translate(TranslateCommand) -> PipelineResult
app.review.open_session(ReviewSessionCommand | Path) -> ReviewSession
app.review.approve(ApproveCommand) -> ApprovalResult
app.reader.open_session(ReaderSessionCommand | Path) -> ReaderSession
app.exports.export_text(ExportCommand) -> ExportResult
app.operations.profiles(ProfilesCommand) -> ReportResult
app.operations.doctor(DoctorCommand) -> ReportResult
app.operations.attempts(AttemptsCommand) -> ReportResult
app.operations.usage(UsageCommand) -> ReportResult
app.operations.import_catalog(CatalogImportCommand) -> ReportResult
app.operations.discover(DiscoverCommand) -> ReportResult
app.operations.smoke(SmokeCommand) -> ReportResult
```

`ImportBookCommand(project, source, ..., *, host=None, port=None, model=None,
context_size=None, thinking=None, allow_model_change=False, profile=None,
pass_profiles=())`, `AnalyzeCommand(project, *, same model options)`, and
`TranslateCommand(project, chunk_limit=5, *, same model options)` preserve the
omitted/explicit configuration distinctions. The remaining commands are:

```text
ReviewSessionCommand(project)
ReaderSessionCommand(project)
ApproveCommand(project, accept_defaults=False)
StatusCommand(project)
ExportCommand(project, encoding="utf-8", output=None)
ProfilesCommand(project); DoctorCommand(project); UsageCommand(project)
AttemptsCommand(project, attempt=None)
CatalogImportCommand(project, source)
DiscoverCommand(project, profile=None, pass_no=1)
SmokeCommand(project, live=False, profile=None, pass_no=1)
```

Review sessions expose `load`, `summary`, `evidence`, `patch_term`,
`review_terms`, and `set_confirmed`. Reader sessions expose `load`, `metadata`,
`progress`, `chapter`, `context`, `create_marker`, and `delete_marker`. Returned
drafts, reports, reader values, and result dataclasses are detached from live
service state.

The final dependency direction is:

```text
translate.py -> bookpipe.cli -> bookpipe.application
review.py / reader.py HTTP adapters -> application session objects
bookpipe.application -> injected locks, Store/provider factories, ProjectFiles
bookpipe.application -> stable engine/import/config/read-model algorithms
bookpipe.bootstrap -> concrete Store, ProviderPool, locks, LocalProjectFiles
```

`OperationScope` owns the project writer lock and lazily closes Store/provider
resources in reverse order. `ReviewSession` retains that writer lock for its full
lifetime. `ReaderSession` owns only `.reader.lock`; its `ReaderContext` continues
to open request-scoped read-only SQLite connections, so translation and reading
remain concurrent. The application never exposes a Store, connection, cursor,
provider process, Display, or HTTP object as a result.

The CLI now contains parsing and rendering only. It injects its compatibility
factories, invokes one service method, and preserves the existing exit mapping.
The HTTP modules retain request/body/origin/status handling and unchanged UI
assets; their former draft and marker repositories are compatibility aliases to
the application implementations.

## Verification and compatibility results

Incremental commits and their focused gates were:

| Commit | Stage | Evidence |
| --- | --- | --- |
| `f8de121` | 0, contract freeze | 215 focused tests passed after the 216-test clean baseline |
| `52f56e5` | 1, API/scopes | 6 boundary/baseline tests and compile passed |
| `bd51320` | 2, projects/operations | full suite: 223 passed |
| `06bf197` | 3, pipeline | full suite: 223 passed; JS: 31 passed |
| `71604e2` | 4, review/approval | 126 focused review/pipeline/series tests passed |
| `201e168` | 5, reader | full suite: 223 passed; JS: 31 passed |
| `baeaa1d`, `d75475d`, `f0dd980` | 6, thin adapters/capabilities/final API | 226 tests passed; JS: 31 passed |

Final verification on `f0dd980`:

| Command | Actual result |
| --- | --- |
| `uv lock --check` | passed; 25 packages resolved, lock unchanged |
| `uv run python -m compileall -q bookpipe translate.py` | passed |
| `uv run --group dev python -m pytest -q` | 226 passed in 26.24s |
| `node --test tests/test_review_filters.cjs tests/test_reader_ranges.cjs` | 31 passed in 254.88ms |

The direct application acceptance test uses the existing local deterministic HTTP
mock and disposable source/project directories. It executes analysis, bulk review,
confirmation, committed approval, one P2-P5 unit, export, status, and reader
queries without CLI parsing, Rich construction, HTTP serving, or a browser.

Compatibility evidence includes:

- Exact canonical P1 and P2-P5 fingerprint fixtures remain unchanged in
  `test_p1_compact.py`.
- Existing canonical/compact checkpoints and compatible completed attempts are
  recovered before provider calls.
- The 12-of-34 persisted resume fixture reopens its SQLite project, preserves all
  accepted checkpoint rows, and calls only P2-P5 for unit 13.
- Partial P1 receipts, failed P4/retry evidence, corrupt/missing registered
  artifacts, observability holds, and strict encoding failures retain their
  fail-closed behavior.
- Review backups, confirmation-versus-approval, dependency-only stale marking,
  retained stale output, marker revisions/duplicates/Unicode offsets, verified P5
  hashes, and reader-alongside-writer behavior all use real temporary files and
  SQLite rather than repository mocks.
- Configuration inheritance, built-in profiles, legacy migration, prompt/catalog
  copying, path remapping, predecessor locking, and cumulative series memory pass
  the unchanged continuation suite.
- No schemas, SQL table definitions, JSON format versions, task keys, prompt
  bytes, response schemas, reader/reviewer assets, or persisted identity
  algorithms changed. Accepted work is therefore reused rather than regenerated.

## Deviations and unrun checks

The PRD's file tree was intentionally reduced:

- Pure fingerprint, validation, terminology-memory, and reader-matching algorithms
  remain at their established import paths instead of being moved mechanically
  into a new `domain/` directory. This avoids changing checkpoint serialization or
  compatibility imports. Dependency tests enforce the required direction rather
  than directory names.
- SQLite remains in `bookpipe.store`, providers remain in their native transport
  modules, and read models remain in `reader_context.py`/`review_context.py`.
  They are reached through injected factories/capabilities and compatibility
  wrappers. `infrastructure/project_files.py` is the new atomic filesystem port.
- `engine.analyze`, `engine.translate`, `Store.approve`, `bookpipe.review` repository
  names, and `bookpipe.reader` marker names remain as compatibility entry points;
  they delegate or alias the application implementation and contain no duplicate
  orchestration/policy.

Checks intentionally not run:

- `tests/browser_review_smoke.py`: Playwright is not installed in the project
  environment (`ModuleNotFoundError: No module named 'playwright'`), so no browser
  binary was assumed or downloaded.
- Live `smoke`, provider discovery, and a real full-book translation: the request
  did not supply or explicitly authorize a local live endpoint/profile/model.
  No cloud provider, built-in Codex authentication, automatic profile
  substitution, or billable turn was used.
- Acceptance against a user's real translation workspace: no such workspace was
  authorized for mutation or consistent copying. Compatibility was exercised only
  with disposable full-project fixtures and persisted-state regression fixtures.

No private book, credentials, runtime evidence, generated translation project, or
browser artifact was added to Git. The CLI/web feature division and all existing
UI assets remain unchanged; no new command, route, product feature, scheduler, or
data migration was introduced.
