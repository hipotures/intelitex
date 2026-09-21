# Public-domain example books

This directory is reserved for small, reproducible end-to-end examples whose **source text and specific edition may legally be redistributed**.

Repository-wide `*.epub` files are ignored by default so personal/copyrighted books cannot be committed accidentally. EPUB files placed under this directory are an explicit exception.

Before adding a book:

- verify that the underlying work is in the public domain in the relevant jurisdiction;
- verify that the particular EPUB edition, annotations, illustrations, and other added material are also redistributable;
- do not add copyrighted translations merely because the original-language work is public domain;
- record the source and license/public-domain basis alongside the fixture.

Synthetic HTML/EPUB fixtures remain preferable for unit tests. Full public-domain books are intended for end-to-end importer and translation demonstrations.

## Included fixtures

### Hans Christian Andersen

| File | Contents | Imported structure | Intended use |
| --- | --- | --- | --- |
| `andersen-smoke.epub` | *The Princess and the Pea* | 1 section/unit, 384 words | End-to-end smoke test (5 model calls). |
| `andersen-mini.epub` | *The Buckwheat*, *The Butterfly*, and *The Little Match-Seller* | 3 sections/units, 2,601 words | Short AI pipeline exercise (15 model calls). |

The English source text was extracted from *Fairy Tales of Hans Christian Andersen*,
[Project Gutenberg ebook #27200](https://www.gutenberg.org/ebooks/27200). The pinned
source EPUB has SHA-256
`75ff4e02d6173eae7e4afb85270d34ba4b6d3b27fa4a20059d5e8ebda0c73a20` and identifies
the text as public domain in the USA. The text corresponds to Susanna Mary Paull's
1872 English translation; Andersen died in 1875 and Paull died in 1888.

The fixtures contain only the selected story text. Project Gutenberg headers,
footers, license text, cover, and branding were not copied. Project Gutenberg permits
reuse of unrestricted text after all Project Gutenberg references are removed from
the work; the link above is a source acknowledgement outside the EPUB publications.
See the [Project Gutenberg license explanation](https://www.gutenberg.org/policy/license.html).

The EPUB structure, navigation, stylesheet, and metadata created for these fixtures
are dedicated under the repository's CC0 1.0 license. Regenerate the byte-stable files
from the pinned source with:

```bash
uv run python examples/public-domain/build_andersen_samples.py \
  "/path/to/Fairy Tales of Hans Christian Andersen.epub"
```

### Full translation demo: *The Time Machine*

`time-machine-demo.epub` contains H. G. Wells's complete novel: sixteen chapters
and an epilogue, plus a reader-visible contents page intentionally kept as a WebUI
opt-out test case. That page is marked non-linear in the EPUB spine, but the current
importer deliberately includes it with a warning. Intelitex imports 18 currently
translation-eligible sections and units with 32,445 words: the disposable 68-word
`Contents` unit followed by 17 narrative units with 32,377 words. A complete
five-pass run uses 90 model calls per target language, or 85 after the contents unit
is disabled. This fixture is intended for qualitative translation comparisons, not
short smoke tests or CI.

The source is [Project Gutenberg ebook #35](https://www.gutenberg.org/ebooks/35).
The pinned source EPUB has SHA-256
`6fe07408989c3c74d498336408b811e27fef4103a44bc7b97c544141ff56f376` and identifies
the text as public domain in the USA. The novel was published in 1895, and Wells
died in 1946.

The demo retains the complete narrative text and inline emphasis. Its generator
removes the Project Gutenberg header, generated cover, original generated contents
page, footer, license text, branding, generator-specific markup, and one stray `>`
conversion artifact before chapter IV. It then creates fresh EPUB 3 metadata,
navigation, styling, and the small `Contents` test unit described above. The source
link above is an acknowledgement outside the EPUB publication. The new packaging is
dedicated under the repository's CC0 1.0 license.

Regenerate the byte-stable demo from the pinned source with:

```bash
uv run python examples/public-domain/build_time_machine_demo.py \
  "/path/to/The Time Machine by H. G. Wells.epub"
```
