#!/usr/bin/env python3
"""Build a clean translation-demo EPUB from the pinned Gutenberg edition."""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup, NavigableString, Tag


SOURCE_SHA256 = "6fe07408989c3c74d498336408b811e27fef4103a44bc7b97c544141ff56f376"
SOURCE_IDENTIFIER = "http://www.gutenberg.org/35"
MODIFIED = "2026-09-21T00:00:00Z"
OUTPUT_NAME = "time-machine-demo.epub"


@dataclass(frozen=True)
class Chapter:
    title: str
    slug: str


CHAPTERS = (
    Chapter("I. Introduction", "chapter-01"),
    Chapter("II. The Machine", "chapter-02"),
    Chapter("III. The Time Traveller Returns", "chapter-03"),
    Chapter("IV. Time Travelling", "chapter-04"),
    Chapter("V. In the Golden Age", "chapter-05"),
    Chapter("VI. The Sunset of Mankind", "chapter-06"),
    Chapter("VII. A Sudden Shock", "chapter-07"),
    Chapter("VIII. Explanation", "chapter-08"),
    Chapter("IX. The Morlocks", "chapter-09"),
    Chapter("X. When Night Came", "chapter-10"),
    Chapter("XI. The Palace of Green Porcelain", "chapter-11"),
    Chapter("XII. In the Darkness", "chapter-12"),
    Chapter("XIII. The Trap of the White Sphinx", "chapter-13"),
    Chapter("XIV. The Further Vision", "chapter-14"),
    Chapter("XV. The Time Traveller’s Return", "chapter-15"),
    Chapter("XVI. After the Story", "chapter-16"),
    Chapter("Epilogue", "epilogue"),
)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def inline_xhtml(node: Tag) -> str:
    def visit(item: object) -> str:
        if isinstance(item, NavigableString):
            return escape(re.sub(r"\s+", " ", str(item)))
        if not isinstance(item, Tag):
            return ""
        children = "".join(visit(child) for child in item.children)
        if item.name in {"i", "em"}:
            return f"<em>{children}</em>"
        if item.name == "br":
            return "<br/>"
        if item.name == "a" and not children.strip():
            return ""
        raise RuntimeError(f"Unexpected inline element in source prose: {item.name}")

    return "".join(visit(child) for child in node.children).strip()


def navigation_targets(source: zipfile.ZipFile) -> dict[str, str]:
    nav_name = "OEBPS/toc.xhtml"
    soup = BeautifulSoup(source.read(nav_name), "html.parser")
    nav = soup.find("nav", attrs={"epub:type": "toc"})
    if nav is None:
        raise RuntimeError("Source EPUB navigation document has no table of contents")
    targets = {
        normalized(anchor.get_text(" ", strip=True)): urldefrag(unquote(anchor.get("href", "")))[0]
        for anchor in nav.find_all("a", href=True)
    }
    expected = {normalized(chapter.title) for chapter in CHAPTERS}
    missing = expected - targets.keys()
    if missing:
        raise RuntimeError(f"Source navigation is missing chapters: {sorted(missing)}")
    return targets


def extract_chapter(source: zipfile.ZipFile, targets: dict[str, str], chapter: Chapter) -> list[str]:
    relative = targets[normalized(chapter.title)]
    name = posixpath.normpath(posixpath.join("OEBPS", relative))
    if not name.startswith("OEBPS/") or name not in source.namelist():
        raise RuntimeError(f"Unsafe or missing chapter target: {relative}")
    soup = BeautifulSoup(source.read(name), "html.parser")
    container = soup.select_one("div.chapter")
    if container is None:
        raise RuntimeError(f"No chapter container in {name}")
    heading = container.find(re.compile(r"^h[1-6]$"), recursive=False)
    if heading is None or normalized(heading.get_text(" ", strip=True)) != normalized(chapter.title):
        raise RuntimeError(f"Unexpected chapter heading in {name}")
    stray_text = normalized(" ".join(str(child) for child in container.children if isinstance(child, NavigableString)))
    expected_stray = ">" if chapter.slug == "chapter-04" else ""
    if stray_text != expected_stray:
        raise RuntimeError(f"Unexpected loose text in {name}: {stray_text!r}")
    direct_tags = [child for child in container.children if isinstance(child, Tag)]
    unexpected = [child.name for child in direct_tags if child is not heading and child.name != "p"]
    if unexpected:
        raise RuntimeError(f"Unexpected block elements in {name}: {unexpected}")
    paragraphs = [inline_xhtml(paragraph) for paragraph in container.find_all("p", recursive=False)]
    if not paragraphs or any(not paragraph for paragraph in paragraphs):
        raise RuntimeError(f"Empty prose paragraph in {name}")
    return paragraphs


def chapter_xhtml(chapter: Chapter, paragraphs: list[str]) -> str:
    body = "\n".join(f"      <p>{paragraph}</p>" for paragraph in paragraphs)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head>
    <meta charset="utf-8"/>
    <title>{escape(chapter.title)}</title>
    <link rel="stylesheet" type="text/css" href="../styles/book.css"/>
  </head>
  <body>
    <article epub:type="chapter">
      <h1 id="{chapter.slug}">{escape(chapter.title)}</h1>
{body}
    </article>
  </body>
</html>
"""


def navigation() -> str:
    items = "\n".join(
        f'        <li><a href="text/{chapter.slug}.xhtml#{chapter.slug}">{escape(chapter.title)}</a></li>'
        for chapter in CHAPTERS
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


def package() -> str:
    manifest = "\n".join(
        f'    <item id="chapter-{index}" href="text/{chapter.slug}.xhtml" media-type="application/xhtml+xml"/>'
        for index, chapter in enumerate(CHAPTERS, 1)
    )
    spine = "\n".join(
        f'    <itemref idref="chapter-{index}"/>' for index, _chapter in enumerate(CHAPTERS, 1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">urn:intelitex:demo:time-machine:v1</dc:identifier>
    <dc:title>The Time Machine — Intelitex Translation Demo</dc:title>
    <dc:creator id="author">H. G. Wells</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <dc:language>en</dc:language>
    <dc:date>1895</dc:date>
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


def build(texts: dict[Chapter, list[str]], output: Path) -> None:
    members = [
        entry("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
        entry("META-INF/container.xml", CONTAINER),
        entry("EPUB/package.opf", package()),
        entry("EPUB/nav.xhtml", navigation()),
        entry("EPUB/styles/book.css", CSS),
    ]
    members.extend(
        entry(f"EPUB/text/{chapter.slug}.xhtml", chapter_xhtml(chapter, texts[chapter]))
        for chapter in CHAPTERS
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as epub:
        for info, content in members:
            epub.writestr(info, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Pinned Project Gutenberg EPUB #35")
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
        targets = navigation_targets(source)
        texts = {chapter: extract_chapter(source, targets, chapter) for chapter in CHAPTERS}

    output = args.output_dir / OUTPUT_NAME
    build(texts, output)
    words = sum(len(BeautifulSoup(" ".join(texts[chapter]), "html.parser").get_text(" ").split()) for chapter in CHAPTERS)
    print(f"{output}: {len(CHAPTERS)} chapters, {words} prose words, {output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
