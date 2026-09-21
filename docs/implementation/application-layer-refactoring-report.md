# Application Layer Refactoring Report

## Baseline and scope

- Audited production baseline: `87a13ee2a952f773dbecaabd4e01b5e802e96aff`.
- Starting checkout: `3707d3a` on `main` (the audited production tree plus only
  `docs/prd/application-layer-refactoring.md`).
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

This ledger is updated as each adapter is moved to the application boundary.

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
| Reviewer HTTP routes | `ReviewSession` methods | `test_review.py`, `test_review19.py` |
| Reader HTTP routes | `ReaderSession` methods | `test_reader.py`, `test_reader_ranges.cjs` |
| Frozen manifests and task fingerprints | domain identity and existing engine contracts | `test_series.py`, `test_p1_compact.py` |
| Resume/recovery and accepted artifacts | pipeline execution | `test_pipeline.py`, `test_p1_compact.py` |
| Draft/approval/staleness distinctions | review and approval services | `test_review*.py`, `test_pipeline.py`, `test_series.py` |
| Verified P5 reads/context/markers | reader service and read model | `test_reader.py`, `test_reader_ranges.cjs` |
| Configuration/profile/continuation precedence | projects and operations services | `test_series.py`, `test_transports.py` |

## Implemented API and dependency map

To be completed from the final implementation.

## Verification and compatibility results

To be completed after each implementation stage and the final regression run.

## Deviations and unrun checks

To be completed at final verification. Real inference is not authorized by this
request and will not be attempted without an explicit local configuration.
