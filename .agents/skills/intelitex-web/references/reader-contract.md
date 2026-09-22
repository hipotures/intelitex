# Reader integration

B: ReaderService/ReaderContext/MarkerRepository and real main-server Reader routes.
R: v33 Reader shell is a deliberately incomplete visual placeholder, not a spec to
replace existing Reader functionality with three fake paragraphs.

Separate endpoints exist for metadata, progress, chapter and markers. Do not expect
the /reader metadata response to include progress/marker state (older standalone API
had a combined shape). Fetch appropriate resources with independently scoped keys.

Metadata: book_fingerprint,title,chapters. Progress: available-text word offsets,
last_chapter and per-chapter/block counts. It is not a persisted browser bookmark.
Chapter: id,title,blocks with canonical IDs/kind/text/formatting,complete,stale,
optional unavailable(after_blocks,reason),warning. Show only verified P5 content;
backend-authorized stale output can remain readable with a clear warning. Source
text and a partial unverified model stream are not substitutes.

Preserve location by book fingerprint/chapter/block/offset, not a fragile DOM index.
Adding output changes availability but must not jump the reader or overwrite its
currently viewed prose while selecting text. Name progress denominator; do not equate
reading progress with translation completion or source-book percentage.

Context POST: chapter_id,block_id,position. Use existing recognition/known-so-far
logic; recognized:false is a legitimate result. Do not call a model, infer entities,
or use whole-book Review evidence to answer context beyond the reading position.

Markers use id,chapter_id,block_id,start,end,text. POST includes revision; DELETE
body includes revision. Existing format/fingerprint/duplicate/exact-text checks apply.
Offsets are Unicode code points, not JavaScript UTF-16. Reuse tested DOM/range mapping
with emoji, combining marks, split inline nodes and formatting spans. Never submit
raw DOM offsets without conversion to canonical text.

Load marker `_revision`, use new returned revision after mutation. Conflicts preserve
intended selection and require explicit reapply if text changed. API marker writes
hold .reader.lock for the request. A standalone Reader holds that same lock for its
session, so main API marker writes can return workspace_busy while reads work. Do not
bypass or replace this lock with a component mutex.

Preserve existing minimal reading/quick-marker behavior, gesture preferences, deletion
and Context Helper as applicable to authorized integration. Do not invent marker types,
comments, an annotation-review workflow, model calls or advanced Reader redesign.
Retain standalone Reader compatibility. v33's simple shell doesn't authorize removing
working features or imply all Reader settings have a finished new design.
