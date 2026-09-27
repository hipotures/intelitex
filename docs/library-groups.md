# Library hierarchy

The Work **All** view follows the directory hierarchy under `--import-root`.
Ordinary directories are groups, including nested author, series and category
folders. Packed EPUBs and unpacked EPUB/HTML book roots are books. Discovery stops
at an unpacked book root; its technical folders are not Library groups.
Directory symlinks are not followed during discovery.

Each group may contain an optional `library.yaml`:

```yaml
version: 1
kind: author
name: Peter F. Hamilton
```

For `Peter F. Hamilton/Salvation/library.yaml`:

```yaml
version: 1
kind: series
name: Salvation
```

`kind` accepts `author`, `series`, or `category`. Both `kind` and `name` are
optional. Without a declaration, the directory name is displayed and no type
badge is invented. Invalid declarations show a warning and retain the untyped
directory group. YAML is read safely, limited to 16 KiB, and never used to rename
books or move files. `Refresh Library` rereads directory membership and YAML.

All uses the existing continuous card grid: each book occupies one cell, with no
reserved cells or forced row breaks at group boundaries. A shared subtle accent
and an author/series/category breadcrumb on cards identify the hierarchy.

The selected Author, Title or Language order applies to the entire catalog before
pagination, recursively at each hierarchy level. Groups remain contiguous rather
than being broken apart to interleave individual books. Title sorts group names
and book titles. Author sorts author groups by their declared name, other groups
by their first known descendant author, and books by their declared parent author
(or EPUB author when no parent author is declared). This keeps metadata spelling
variants of the same declared author together. Within an author, natural filename ordering
preserves numbered volumes. Language sorts groups by their first known descendant
language and books by declared language; missing/unknown values sort last.
Presentation and sorting use normalized base-language codes: for example `en`,
`eng`, `en-US`, and `en_GB` all become `en`; `pol` and `pl-PL` become `pl`.
Common three-letter aliases are mapped to their two-letter equivalents; other
three-letter codes are preserved. EPUB files and stored workspace state are not
rewritten. Source inspection retains the original declaration as evidence.
Numbers sort naturally (2 before 10). The browser remembers the selection for this
server. Source details show the relative location, allowing identical titles in
different files to be distinguished.

Library discovery reads no translation state. Metadata sorting reads small EPUB
package documents, with an in-memory cache invalidated by file identity, size and
nanosecond modification/change timestamps. It does not hash or tokenize every book.

## Existing workspace linkage — not yet relocation-safe

Grouping does **not** implement automatic workspace relinking. Current bindings:

* `book.json`: `source_root` and optional `source_archive`.
* `workspace.json`, for newer workspaces: relative `source_id` and a preflight
  `source_fingerprint` (the current algorithm includes the packed filename).
* `<workspace-root>/.intelitex-web.json`: source/draft catalog, partly indexed by
  hashes of absolute paths.

A moved source may lose its Library/workspace association. Imported packed EPUBs
have a `source-package/` snapshot; older unpacked imports can still depend on an
external source directory for publication. Do not rewrite immutable translation
fingerprints or substitute a new source merely because its title matches.

The next identity stage must introduce durable book IDs, exact source-version
hashes and separate locations, with an explicit relationship to workspace IDs.
One book may have multiple workspaces. The current grouping change intentionally
leaves existing links and translation files unchanged; the selected live
workspaces will be bound to their new locations explicitly afterwards.
