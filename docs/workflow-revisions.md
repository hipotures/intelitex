# Workflow revisions and publication

The JSON checkpoint state owns accepted results and their dependencies. Artifact
files are immutable evidence; JSON review files hold draft terminology decisions.
The HTTP job registry describes running processes, not translation completion.
A completed job alone never proves that an EPUB has been delivered to Books.

## Editing and invalidation

- Review remains editable after approval and publication. Its impact preview lists
  segments whose recorded lexical dependencies contain a changed term. Approval
  invalidates P2–P5 for those segments, retaining their previous readable finals.
- Full runs and targeted runs record the same selected-pass receipts. Replacing
  an upstream pass invalidates later passes in that segment. Unrelated segments
  retain their accepted results.
- A changed P5 invalidates consumers only when their saved continuity excerpt no
  longer matches. A full run includes newly affected later segments within its
  requested limit. Propagation continues when a regenerated consumer changes.
- Translate and Publish remain inspectable while generation prerequisites are
  unmet. Model execution and publication still enforce approval and freshness.
- Source text, block IDs and segmentation remain frozen after work starts.
  Structural source changes require a new workspace; this update does not migrate
  existing checkpoints onto a different source plan.

## Publication

Every newly built EPUB receives a unique filename. Promotion refuses to replace
an existing file, and publication records retain previous edition receipts.
Retrying an unchanged, intact edition reuses it. Books delivery is a separate
operation and status: a workspace EPUB can be current while its Books copy is
missing. Automatic and explicit publication both support delivery; the interface
offers Add to Books when a current workspace edition has not been delivered.

Library filenames identify both the publication inputs and the actual archive
bytes, so a repaired or rebuilt archive cannot overwrite an earlier library copy.

## Read performance and consistency

The server reuses only bounded, validated immutable-source manifests, invalidated
by file identity, size and nanosecond modification/change timestamps. Mutable
callers receive copies. A short read-only state snapshot batches checkpoint
rows and verifies each result once per request. Completed chunks use their durable
acceptance receipts rather than reparsing every historical model input on each
navigation. Explicit invalidations and artifact checksum errors remain visible.

Workspace detail pages request metadata only for their selected workspace, so
opening one book does not validate every other project's source manifest.
Large JSON responses are encoded outside the HTTP event loop. SSE refreshes are
scoped to affected workspaces, and accounting/evidence refreshes do not delay the
completion of a UI write. Hashed frontend assets can be cached independently of
the uncached API. No additional SQLite cache or schema migration is introduced.
