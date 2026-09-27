# Book identity and bibliographic corrections

Prepare → Source metadata exposes Title, Authors, Source language and Book ID.
The editor shows original values beside editable current values. Each field can
be restored independently. Only the current corrections and last change time
are retained; this is not a versioned edit log.

`book_id` is a SHA-256 identity derived from the frozen `source_fingerprint`.
Titles, authors, language, workspace names and filesystem paths are values,
not identity keys. Workspaces with the same frozen source share corrections.
Different source editions/import manifests can have different identities even
if their titles match; they are never merged by title.

`<workspace-root>/.book-metadata.json` is authoritative JSON, containing one
original metadata record and current correction map per book. It must be backed
up with the workspaces, including when moving a workspace to another root.
It is not a disposable cache. Source bindings map explicit paths to Book IDs;
packed sources additionally record their byte SHA-256 to reject a replaced file.
There is no SQL dependency. A future SQLite cache must index this JSON too.

New imports register originals and source bindings. Existing workspaces without
a record initially display their frozen manifest metadata and persist originals
when first edited or explicitly linked. Registration never replaces an existing
original. For older manifests that omitted author/language fields, initial
explicit registration can obtain those missing fields from the verified source
EPUB. The saved original title is retained exactly.

GET `/api/workspaces/{id}/metadata` returns `book_id`, `source_fingerprint`,
`original`, `corrections`, `effective`, `revision` and `updated_at`.
PATCH requires `revision` and `corrections`; allowed fields are `title` (string),
`creators` (array of names) and `language` (language code). Null restores the
original field. An empty authors array intentionally clears authors. Atomic
writes and a root-level lock serialize edits; stale revisions return HTTP 409.

Workspace listings, Prepare, Library, source inspection and Reader use the
shared values. Source language here describes the book; changing it does not
rewrite translation prompts or the workspace's translation configuration.
Published EPUB language remains the target language.

Publication fingerprints include explicit metadata corrections. Editing them
makes an existing publication stale without invalidating P1–P5, review or
translation artifacts. Publish snapshots the corrections and applies them to
EPUB package title, authors and source-language provenance. Obsolete title/author
refinements are removed when replacing those fields. Each rebuilt edition gets
a new file; prior editions remain unchanged. Merely registering metadata without
corrections does not invalidate existing publications.

`application.source_relink.relink_epub(project, source)` explicitly binds a moved
EPUB only after checking every imported source document SHA-256. If the old
unpacked folder is missing, it safely extracts a durable `source-package` copy.
Only source locations in the manifest change; the previous manifest is saved in
`history/source_relinks/`. It does not reimport, translate, rename titles, or
modify the library EPUB. Restoring another machine requires preserving both the
workspace data and the root metadata catalog.

Regression coverage: `tests/test_book_metadata.py` covers shared identity,
conflicts, restores, warm listing caches, both HTTP adapters, source relocation,
and metadata-only publication with unchanged checkpoints and old editions.
`web/tests/book-metadata.test.mjs` exercises the real API/editor, Library update,
persistence, per-field restore and desktop/mobile dialog layout.
