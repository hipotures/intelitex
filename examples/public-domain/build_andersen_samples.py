#!/usr/bin/env python3
"""Build the small Andersen EPUB fixtures from the pinned Gutenberg EPUB."""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag


SOURCE_SHA256 = "75ff4e02d6173eae7e4afb85270d34ba4b6d3b27fa4a20059d5e8ebda0c73a20"
SOURCE_IDENTIFIER = "http://www.gutenberg.org/27200"
MODIFIED = "2026-09-21T00:00:00Z"


@dataclass(frozen=True)
class Story:
    title: str
    slug: str


@dataclass(frozen=True)
class Sample:
    filename: str
    title: str
    identifier: str
    stories: tuple[Story, ...]


SAMPLES = (
    Sample(
        filename="andersen-smoke.epub",
        title="The Princess and the Pea — Intelitex Smoke Sample",
        identifier="urn:intelitex:sample:andersen-smoke:v1",
        stories=(Story("The Princess and the Pea", "princess-and-the-pea"),),
    ),
    Sample(
        filename="andersen-mini.epub",
        title="Three Fairy Tales by Hans Christian Andersen — Intelitex Mini Sample",
        identifier="urn:intelitex:sample:andersen-mini:v1",
        stories=(
            Story("The Buckwheat", "the-buckwheat"),
            Story("The Butterfly", "the-butterfly"),
            Story("The Little Match-Seller", "the-little-match-seller"),
        ),
    ),
)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def inline_text(node: Tag) -> str:
    def visit(item: object) -> str:
        if isinstance(item, NavigableString):
            return str(item)
        if not isinstance(item, Tag):
            return ""
        if item.name == "br":
            return "\n"
        return "".join(visit(child) for child in item.children)

    return re.sub(r"\s+", " ", visit(node).replace("\xa0", " ")).strip()


def source_documents(epub: zipfile.ZipFile) -> list[tuple[str, bytes]]:
    return [
        (name, epub.read(name))
        for name in epub.namelist()
        if name.lower().endswith((".xhtml", ".html", ".htm"))
        and not name.lower().endswith(("toc.xhtml", "nav.xhtml"))
    ]


def extract_story(documents: list[tuple[str, bytes]], story: Story) -> list[str]:
    target = normalized(story.title)
    for name, raw in documents:
        soup = BeautifulSoup(raw, "html.parser")
        for heading in soup.find_all(re.compile(r"^h[1-6]$")):
            if normalized(heading.get_text(" ", strip=True)) != target:
                continue
            if heading.name != "h3":
                raise RuntimeError(f"Unexpected heading level for {story.title!r} in {name}: {heading.name}")
            paragraphs: list[str] = []
            for sibling in heading.next_siblings:
                if isinstance(sibling, Tag) and sibling.name == "h3":
                    break
                if not isinstance(sibling, Tag):
                    continue
                candidates = [sibling] if sibling.name == "p" else sibling.find_all("p")
                for paragraph in candidates:
                    text = inline_text(paragraph)
                    if text:
                        paragraphs.append(text)
            if not paragraphs:
                raise RuntimeError(f"No paragraphs found for {story.title!r} in {name}")
            return paragraphs
    raise RuntimeError(f"Story not found: {story.title}")


def xhtml(story: Story, paragraphs: list[str]) -> str:
    body = "\n".join(f"      <p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>{escape(story.title)}</title>
    <link rel="stylesheet" type="text/css" href="../styles/book.css"/>
  </head>
  <body>
    <article epub:type="chapter">
      <h1 id="{story.slug}">{escape(story.title)}</h1>
{body}
    </article>
  </body>
</html>
"""


def navigation(sample: Sample) -> str:
    items = "\n".join(
        f'        <li><a href="text/{story.slug}.xhtml#{story.slug}">{escape(story.title)}</a></li>'
        for story in sample.stories
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>Contents</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>Contents</h1>
      <ol>
{items}
      </ol>
    </nav>
  </body>
</html>
"""


def package(sample: Sample) -> str:
    manifest = "\n".join(
        f'    <item id="story-{index}" href="text/{story.slug}.xhtml" media-type="application/xhtml+xml"/>'
        for index, story in enumerate(sample.stories, 1)
    )
    spine = "\n".join(
        f'    <itemref idref="story-{index}"/>' for index, _story in enumerate(sample.stories, 1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{escape(sample.identifier)}</dc:identifier>
    <dc:title>{escape(sample.title)}</dc:title>
    <dc:creator id="author">Hans Christian Andersen</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <dc:contributor id="translator">Susanna Mary Paull</dc:contributor>
    <meta refines="#translator" property="role" scheme="marc:relators">trl</meta>
    <dc:language>en</dc:language>
    <dc:rights>The source text is in the public domain. The new EPUB packaging is dedicated to the public domain under CC0 1.0 Universal.</dc:rights>
    <meta property="dcterms:modified">{MODIFIED}</meta>
    <link rel="license" href="https://creativecommons.org/publicdomain/zero/1.0/"/>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="styles/book.css" media-type="text/css"/>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""


CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


CSS = """body {
  font-family: serif;
  line-height: 1.45;
  margin: 5%;
}

h1 {
  text-align: center;
}
"""


def entry(name: str, content: str, compression: int = zipfile.ZIP_DEFLATED) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, content.encode("utf-8")


def build(sample: Sample, texts: dict[Story, list[str]], output: Path) -> None:
    members = [
        entry("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
        entry("META-INF/container.xml", CONTAINER),
        entry("EPUB/package.opf", package(sample)),
        entry("EPUB/nav.xhtml", navigation(sample)),
        entry("EPUB/styles/book.css", CSS),
    ]
    members.extend(
        entry(f"EPUB/text/{story.slug}.xhtml", xhtml(story, texts[story])) for story in sample.stories
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as epub:
        for info, content in members:
            epub.writestr(info, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Pinned Project Gutenberg EPUB #27200")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    raw = args.source.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != SOURCE_SHA256:
        raise SystemExit(f"Source SHA-256 mismatch: expected {SOURCE_SHA256}, got {actual_hash}")

    with zipfile.ZipFile(args.source) as source:
        opf = source.read("OEBPS/content.opf").decode("utf-8")
        if SOURCE_IDENTIFIER not in opf:
            raise SystemExit(f"Source EPUB does not identify itself as {SOURCE_IDENTIFIER}")
        documents = source_documents(source)
        stories = {story for sample in SAMPLES for story in sample.stories}
        texts = {story: extract_story(documents, story) for story in stories}

    for sample in SAMPLES:
        output = args.output_dir / sample.filename
        build(sample, texts, output)
        words = sum(len(" ".join(texts[story]).split()) for story in sample.stories)
        print(f"{output}: {len(sample.stories)} story/stories, {words} words, {output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
