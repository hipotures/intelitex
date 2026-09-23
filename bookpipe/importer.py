from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urldefrag, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, UnicodeDammit
from defusedxml import ElementTree as ET

from .progress import ProgressEvent
from .util import PipelineError, atomic_text, digest, inside, natural_key

HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
LEAF_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "blockquote", "figcaption", "dt", "dd", "tr"}
CONTAINERS = LEAF_BLOCKS | {"div", "section", "article", "main", "body", "ul", "ol", "table", "tbody", "thead", "figure", "header", "footer", "aside"}
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "sr.", "jr.", "vs.", "e.g.", "i.e.", "etc.", "no.", "capt.", "gen.", "adm."}


def estimate_source_tokens(text: str) -> int:
    """Pre-Prepare planning estimate: one token per four Unicode characters, rounded up."""
    return (len(text) + 3) // 4


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Conservative English sentence boundaries, preserving all original offsets."""
    starts = [0]
    for m in re.finditer(r'[.!?…]+["\'”’)]*\s+(?=\S)', text):
        prefix = text[:m.start() + 1]
        word = prefix.rsplit(None, 1)[-1] if prefix.strip() else ""
        if word.lower() in ABBREVIATIONS or re.search(r'(?:\b[A-Z]\.)+$', prefix):
            continue
        # Lowercase continuation often follows quoted speech; retain it together.
        next_char = text[m.end():m.end() + 1]
        if next_char.islower():
            continue
        starts.append(m.end())
    return [(a, b) for a, b in zip(starts, starts[1:] + [len(text)]) if text[a:b].strip()]


def split_long(text: str, count: Callable[[str], int], limit: int) -> list[tuple[int, int]]:
    """Split only an oversized paragraph, at sentence/whitespace boundaries first."""
    if count(text) <= limit:
        return [(0, len(text))]
    boundaries = sorted(set([b for _, b in sentence_spans(text)] + [len(text)]))
    results, start = [], 0
    while start < len(text):
        options = [b for b in boundaries if b > start]
        lo, hi, best = 0, len(options) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if count(text[start:options[mid]]) <= limit:
                best, lo = options[mid], mid + 1
            else:
                hi = mid - 1
        if best is None:
            # A single oversized sentence. Binary search Unicode character offsets,
            # then prefer the preceding whitespace. No character is discarded.
            lo, hi, best = start + 1, len(text), start
            while lo <= hi:
                mid = (lo + hi) // 2
                if count(text[start:mid]) <= limit:
                    best, lo = mid, mid + 1
                else:
                    hi = mid - 1
            if best == start:
                raise PipelineError("A character cannot fit in the configured chunk budget.")
            last_space = max(text.rfind(" ", start, best), text.rfind("\n", start, best))
            if last_space > start:
                best = last_space + 1
        results.append((start, best))
        start = best
    return results


def pack_blocks(blocks: list[dict], count: Callable[[str], int], target: int, maximum: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    for block in blocks:
        candidate = current + [block]
        size = count("\n\n".join(b["text"] for b in candidate))
        if current and size > maximum:
            groups.append(current)
            current = [block]
        else:
            current = candidate
        if count("\n\n".join(b["text"] for b in current)) >= target:
            groups.append(current)
            current = []
    if current:
        if groups and count("\n\n".join(b["text"] for b in groups[-1] + current)) <= maximum:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups


def local_ref(root: Path, base: Path, href: str) -> tuple[Path, str] | None:
    if urlsplit(href).scheme or href.startswith("//"):
        return None
    path, fragment = urldefrag(unquote(href))
    return inside(root, base / path), fragment


def reading_order(root: Path, opf_path: Path | None = None) -> tuple[list[Path], dict, list[str]]:
    """Use OPF spine when present. Never mistake filename order for guaranteed spine order."""
    warnings = []
    meta: dict[str, Any] = {"order_method": "natural_filename", "toc": []}
    if opf_path is None:
        container = root / "META-INF" / "container.xml"
        if container.exists():
            doc = ET.parse(container)
            names = doc.findall(".//{*}rootfile")
            if names:
                opf_path = inside(root, root / names[0].get("full-path", ""))
        if opf_path is None:
            candidates = list(root.rglob("*.opf"))
            if len(candidates) == 1:
                opf_path = candidates[0]
            elif len(candidates) > 1:
                raise PipelineError("More than one OPF file found. Select one with --opf.")
    paths: list[Path] = []
    if opf_path:
        opf_path = inside(root, opf_path)
        doc = ET.parse(opf_path)
        manifest = {e.get("id"): e for e in doc.findall(".//{*}manifest/{*}item")}
        spine = doc.find(".//{*}spine")
        if spine is None:
            raise PipelineError("OPF has no spine.")
        for ref in spine.findall("{*}itemref"):
            item = manifest.get(ref.get("idref"))
            if item is None:
                raise PipelineError(f"Unknown spine idref: {ref.get('idref')}")
            link = local_ref(root, opf_path.parent, item.get("href", ""))
            if not link:
                raise PipelineError("Remote spine documents are not supported.")
            path, _ = link
            if path.suffix.lower() not in HTML_SUFFIXES:
                warnings.append(f"Non-HTML spine item not imported: {path.name}")
                continue
            if ref.get("linear") == "no":
                warnings.append(f"Non-linear spine item included: {path.name}")
            if "nav" in item.get("properties", "").split():
                continue
            if not path.is_file():
                raise PipelineError(f"Missing spine file: {path}")
            if path in paths:
                raise PipelineError(f"Repeated spine file requires manual order review: {path.name}")
            paths.append(path)
        meta["order_method"] = "opf_spine"
        meta["opf"] = str(opf_path.relative_to(root))
        title = doc.find(".//{*}metadata/{*}title")
        meta["title"] = title.text if title is not None else root.name
        meta['creators'] = [(node.text or '').strip() for node in doc.findall('.//{*}metadata/{*}creator') if (node.text or '').strip()]
        language = doc.find('.//{*}metadata/{*}language')
        meta['language'] = (language.text or '').strip() or None if language is not None else None
        # NCX and EPUB3 navigation links retain fragment targets for section splits.
        for item in manifest.values():
            is_ncx = item.get("media-type") == "application/x-dtbncx+xml"
            is_nav = "nav" in item.get("properties", "").split()
            if not (is_ncx or is_nav):
                continue
            resolved = local_ref(root, opf_path.parent, item.get("href", ""))
            if not resolved or not resolved[0].exists():
                continue
            tocpath = resolved[0]
            if is_ncx:
                navigation = ET.parse(tocpath)
                for point in navigation.findall(".//{*}navPoint"):
                    src = point.find("{*}content")
                    label = point.find("{*}navLabel/{*}text")
                    if src is not None:
                        target = local_ref(root, tocpath.parent, src.get("src", ""))
                        if target:
                            meta["toc"].append({"file": str(target[0].relative_to(root)), "anchor": target[1],
                                                "label": label.text if label is not None else ""})
            elif not meta["toc"]:
                soup = BeautifulSoup(tocpath.read_bytes(), "html.parser")
                nav = soup.find("nav", attrs={"epub:type": "toc"}) or soup.find("nav")
                for anchor in nav.find_all("a", href=True) if nav else []:
                    target = local_ref(root, tocpath.parent, anchor["href"])
                    if target:
                        meta["toc"].append({"file": str(target[0].relative_to(root)), "anchor": target[1],
                                            "label": anchor.get_text(" ", strip=True)})
    else:
        paths = sorted((p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in HTML_SUFFIXES),
                       key=lambda p: natural_key(str(p.relative_to(root))))
        warnings.append("No OPF spine found. Using natural filename order; inspect book.json before analysis.")
    if not paths:
        raise PipelineError("No HTML/XHTML reading documents found.")
    return paths, meta, warnings


MAJOR_SECTION_CLASSES = {"calibre27"}
SCENE_START_CLASSES = {"calibre19", "calibre21", "calibre23"}
CHAPTER_UNITS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}
CHAPTER_TEENS = {"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"}
CHAPTER_TENS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}


def _structure_title(text: str) -> str:
    """Remove lightweight emphasis and source-formatting whitespace from labels."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", text)).strip()


def _numbered_chapter_label(text: str) -> bool:
    label = _structure_title(text).casefold()
    if label.startswith("chapter "):
        label = label[8:].split(":", 1)[0].strip()
    words = label.replace("-", " ").split()
    if len(words) == 1:
        word = words[0]
        return (bool(re.fullmatch(r"[1-9][0-9]{0,3}", word)) or
                bool(re.fullmatch(r"(?=[ivxlcdm]+$)m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})", word)) or
                word in CHAPTER_UNITS | CHAPTER_TEENS | CHAPTER_TENS)
    return len(words) == 2 and words[0] in CHAPTER_TENS and words[1] in CHAPTER_UNITS


def _semantic_lower_heading(block: dict) -> bool:
    """Recognize explicit chapters in lower-level headings without splitting every h3–h6."""
    if block.get("heading_level") not in (3, 4, 5, 6):
        return False
    label = _structure_title(block["text"]).casefold()
    return label in {"prologue", "epilogue", "about the author"} or _numbered_chapter_label(label)


def _opening_label(blocks: list[dict], metadata: dict) -> str | None:
    """Use an explicit opening label when a converter made the filename the only title."""
    first = _structure_title(blocks[0]["text"])
    if not first:
        return None
    if (first.casefold().startswith("this book is a work of fiction") or
            any("copyright ©" in _structure_title(block["text"]).casefold()
                for block in blocks[:2])):
        return "Copyright"
    contents = re.fullmatch(r"(.{2,80})\s+Contents", first, re.I)
    if contents:
        return f"{contents.group(1)} · Contents"
    if first.casefold() in {"contents", "cast of characters", "principal characters", "prologue", "epilogue", "timeline"}:
        return first
    if re.fullmatch(r"Part\s+\d+\s*:\s*[^.]{1,70}", first, re.I):
        if len(blocks) > 1:
            second = _structure_title(blocks[1]["text"])
            if re.fullmatch(r"\d{1,3}", second):
                return f"{first} · Chapter {second}"
        return first
    if re.fullmatch(r"\d{1,3}", first):
        return f"Chapter {first}"
    if _numbered_chapter_label(first):
        return first
    creator_names = {str(name).casefold() for name in metadata.get("creators", [])}
    if first.isupper() and len(first) <= 70 and creator_names:
        pieces = []
        for block in blocks[:5]:
            part = _structure_title(block["text"])
            if part.casefold() in creator_names or not part.isupper() or len(part) > 70:
                break
            pieces.append(part)
        if len(pieces) > 1 and 5 <= len(" ".join(pieces)) <= 80:
            return " ".join(pieces)
    return None


def _extract_document(raw: bytes, *, encoding: str | None = None,
                      chapter_selector: str | None = None):
    """Extract readable blocks plus conservative literary-structure hints.

    Inline spans are concatenated without synthetic spaces, so drop caps/small caps
    such as <span>T</span><span>HE</span> remain "THE". Images are never converted
    to alt text. Image-only paragraphs act as scene separators.
    """
    warnings: list[str] = []
    if encoding:
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeError as exc:
            raise PipelineError(f"Input decoding failed with {encoding}: {exc}") from exc
        detected = encoding
    else:
        decoded = UnicodeDammit(raw, is_html=True)
        if decoded.unicode_markup is None or decoded.contains_replacement_characters:
            raise PipelineError("Cannot decode HTML without data loss. Specify --input-encoding.")
        text, detected = decoded.unicode_markup, decoded.original_encoding or "unknown"

    soup = BeautifulSoup(text, "html.parser")
    for tag in list(soup.find_all(["script", "style", "noscript", "template", "head", "nav"])):
        tag.decompose()
    for tag in list(soup.select('[hidden], [aria-hidden="true"], [epub\\:type="pagebreak"]')):
        tag.decompose()
    for comment in soup.find_all(string=lambda x: isinstance(x, Comment)):
        comment.extract()

    selectors = {id(x) for x in soup.select(chapter_selector)} if chapter_selector else set()
    body = soup.body or soup
    result: list[dict] = []
    nodes: list[Tag | None] = []
    pending_anchors: list[str] = []
    pending_scene_break = False

    def inline(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        if node.name == "br":
            return "\uf000"
        if node.name == "img":
            # ALT often contains conversion-tool paths such as X:\\Data\\Books\\...
            # and must not leak into prose. Image-only blocks are handled as scene breaks.
            return ""
        value = "".join(inline(child) for child in node.children)
        if node.name in {"em", "i"} and value.strip():
            return "*" + value.strip() + "*"
        if node.name in {"strong", "b"} and value.strip():
            return "**" + value.strip() + "**"
        if node.name in {"td", "th"}:
            return value + "\t"
        return value

    def cleaned(value: str) -> str:
        # HTML source line-wrapping is not prose structure. Preserve only explicit <br>.
        value = value.replace("\xa0", " ")
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r" *\uf000 *", "\n", value)
        return value.strip()

    def emit(node, value: str, kind: str = "paragraph"):
        nonlocal pending_scene_break
        value = cleaned(value)
        if not value:
            return
        if re.fullmatch(r"(?:\*\s*){3,}|[-—_]{3,}", value):
            pending_scene_break = True
            return
        classes = sorted(set(node.get("class", []))) if isinstance(node, Tag) else []
        major_section = bool(set(classes) & MAJOR_SECTION_CLASSES)
        if major_section:
            kind = "heading"
        scene_start = pending_scene_break or bool(set(classes) & SCENE_START_CLASSES)
        result.append({
            "text": value,
            "kind": kind,
            "anchors": list(pending_anchors),
            "heading_level": int(node.name[1]) if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name) else None,
            "custom_heading": isinstance(node, Tag) and id(node) in selectors,
            "major_section": major_section,
            "scene_start": scene_start,
            "classes": classes,
        })
        nodes.append(node if isinstance(node, Tag) else None)
        pending_anchors.clear()
        pending_scene_break = False

    def walk(node):
        nonlocal pending_scene_break
        if isinstance(node, NavigableString):
            if str(node).strip():
                emit(None, str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.get("id"):
            pending_anchors.append(node["id"])
        if node.name == "a" and node.get("name"):
            pending_anchors.append(node["name"])
        if node.name == "hr":
            pending_scene_break = True
            return

        descendants = node.find(CONTAINERS)
        if node.name in LEAF_BLOCKS and not descendants:
            for child in node.find_all(attrs={"id": True}):
                pending_anchors.append(child["id"])
            value = inline(node)
            # Decorative scene-divider images carry no prose but do carry structure.
            if node.find("img") is not None and not cleaned(value):
                pending_scene_break = True
                return
            kind = "heading" if node.name.startswith("h") and node.name[1:].isdigit() else "paragraph"
            emit(node, value, kind)
            return

        if node.name not in CONTAINERS and node.name != "[document]" and not descendants:
            emit(node, inline(node))
            return

        buffer = []
        for child in node.children:
            is_container = isinstance(child, Tag) and (child.name in CONTAINERS or child.name == "hr" or child.find(CONTAINERS))
            if is_container:
                if buffer:
                    emit(node, "".join(inline(x) for x in buffer))
                    buffer.clear()
                walk(child)
            else:
                if isinstance(child, Tag) and child.get("id"):
                    pending_anchors.append(child["id"])
                buffer.append(child)
        if buffer:
            emit(node, "".join(inline(x) for x in buffer))

    walk(body)
    return result, detected, sorted(set(warnings)), soup, nodes


def extract_blocks(raw: bytes, *, encoding: str | None = None,
                   chapter_selector: str | None = None) -> tuple[list[dict], str, list[str]]:
    """Public extraction API; its values and ordering are frozen import inputs."""
    blocks, detected, warnings, _, _ = _extract_document(
        raw, encoding=encoding, chapter_selector=chapter_selector,
    )
    return blocks, detected, warnings


def extract_publication_document(raw: bytes, *, encoding: str | None = None):
    """Return the normal extracted blocks and their parsed source elements.

    This deliberately reuses the importer traversal so publication never guesses
    a second, subtly different block order. Callers must still reject elements
    whose inline structure cannot be rewritten safely.
    """
    blocks, detected, _, soup, nodes = _extract_document(raw, encoding=encoding)
    return blocks, detected, soup, nodes


def _section_role(section: dict, index: int, total: int, metadata: dict) -> str:
    title = _structure_title(section.get("title", ""))
    title_norm = re.sub(r"\s+", " ", title).strip().casefold()
    source_chars = len("\n\n".join(b["text"] for b in section["blocks"]))
    if re.search(r"\babout (?:the )?author(?:s)?\b", title_norm):
        return "back_matter"
    book_title = re.sub(r"\s+", " ", str(metadata.get("title") or "")).strip().casefold()
    source_norm = re.sub(r"\s+", " ", " ".join(b["text"] for b in section["blocks"])).strip().casefold()
    if index == 0 and source_chars <= 3000 and book_title and book_title in source_norm:
        return "front_matter"
    return "narrative"


def _scenes_for_section(section: dict) -> list[dict]:
    scenes: list[dict] = []
    current: list[dict] = []

    def has_prose(blocks: list[dict]) -> bool:
        return any(b["kind"] != "heading" for b in blocks)

    def flush():
        nonlocal current
        if not current:
            return
        number = len(scenes) + 1
        sid = f"{section['id']}_s{number:03d}"
        chars = len("\n\n".join(b["text"] for b in current))
        words = sum(len(re.findall(r"\S+", b["text"])) for b in current)
        for block in current:
            block["scene_id"] = sid
        scenes.append({
            "id": sid,
            "number": number,
            "block_ids": [b["id"] for b in current],
            "source_chars": chars,
            "source_words": words,
        })
        current = []

    for block in section["blocks"]:
        if block.get("scene_start") and current and has_prose(current):
            flush()
        current.append(block)
    flush()
    return scenes


def import_folder(root: Path, project: Path, count: Callable[[str], int], settings: dict, ui,
                  *, opf: Path | None = None, encoding: str | None = None,
                  chapter_mode: str = "auto", chapter_selector: str | None = None,
                  sidecars: bool = False, include_glob: str | None = None) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise PipelineError("Input must be an existing folder containing HTML/XHTML files.")
    paths, metadata, warnings = reading_order(root, opf)
    if include_glob:
        paths = [p for p in paths if p.match(include_glob)]
    if not paths:
        raise PipelineError("No HTML files match the requested filter.")

    manifest: dict[str, Any] = {
        "format_version": 2,
        "source_root": str(root),
        "metadata": metadata,
        "warnings": warnings,
        "files": [],
        "chapters": [],
        "non_narrative_sections": [],
        "chunks": [],
        "translation_unit_policy": {
            "whole_section_char_limit": int(settings["whole_section_char_limit"]),
            "rule": "section<=limit: whole section; section>limit: one unit per natural scene; never split a scene by size",
        },
    }

    block_serial = 0
    discovered: list[dict] = []
    for file_i, path in enumerate(paths, 1):
        ui.emit(ProgressEvent(kind="import_files_progress", current=file_i - 1, total=len(paths)))
        ui.emit(ProgressEvent(kind="import_file_started", values={"filename": path.name}))
        raw = path.read_bytes()
        blocks, detected, notes = extract_blocks(raw, encoding=encoding, chapter_selector=chapter_selector)
        relative = str(path.relative_to(root))
        plain = "\n\n".join(b["text"] for b in blocks) + ("\n" if blocks else "")
        atomic_text(project / "extracted" / Path(relative).with_suffix(".txt"), plain)
        if sidecars:
            target = path.with_suffix(".txt")
            if target.exists() and target.read_text(encoding="utf-8") != plain:
                raise PipelineError(f"Refusing to overwrite different sidecar: {target}")
            atomic_text(target, plain)
        manifest["files"].append({"file": relative, "sha256": digest(raw), "encoding": detected})
        manifest["warnings"].extend(f"{relative}: {n}" for n in notes)
        if not blocks:
            manifest["warnings"].append(f"No text in {relative}; no translation unit created.")
            ui.emit(ProgressEvent(kind="import_files_progress", current=file_i, total=len(paths)))
            continue

        toc = [e for e in metadata["toc"] if e["file"] == relative]
        file_label = next((e["label"] for e in toc if not e["anchor"]), None)
        opening_label = _opening_label(blocks, metadata) if not file_label else None
        current = None
        for block in blocks:
            matching = next((e for e in toc if e["anchor"] and e["anchor"] in block["anchors"]), None)
            heading_boundary = (block["custom_heading"] or block["heading_level"] in (1, 2)
                                or _semantic_lower_heading(block))
            major_boundary = bool(block.get("major_section"))

            if chapter_mode == "file":
                boundary = current is None
            elif chapter_mode == "headings":
                boundary = current is None or heading_boundary or major_boundary
            else:
                boundary = current is None or matching or heading_boundary or major_boundary

            # Adjacent structural headings form one heading cluster, not empty sections.
            if boundary and current and all(b["kind"] == "heading" for b in current["blocks"]):
                boundary = False

            if boundary:
                if matching:
                    label = matching["label"]
                    structure = "toc"
                elif heading_boundary or major_boundary:
                    label = _structure_title(block["text"])
                    structure = "major_section" if major_boundary and not heading_boundary else "heading"
                else:
                    label = file_label or opening_label or path.stem
                    structure = "file_section"
                current = {
                    "title": label,
                    "source_file": relative,
                    "structure": structure,
                    "thread_id": None,
                    "blocks": [],
                }
                discovered.append(current)

            block_serial += 1
            block.update(id=f"B{block_serial:07d}", order=block_serial, file=relative)
            current["blocks"].append(block)
        ui.emit(ProgressEvent(kind="import_files_progress", current=file_i, total=len(paths)))

    if not discovered:
        raise PipelineError("Import produced no text.")

    # A genuine Prologue starts the narrative. Title pages, author credits and
    # cast lists preceding it are front matter even when the EPUB TOC is sparse.
    prologue_index = next((i for i, section in enumerate(discovered)
                           if section["structure"] == "heading" and
                           _structure_title(section["title"]).casefold() == "prologue"), None)
    if prologue_index is not None and any(_numbered_chapter_label(section["title"])
                                          for section in discovered[:prologue_index]):
        prologue_index = None
    for i, section in enumerate(discovered):
        role = ("front_matter" if prologue_index is not None and i < prologue_index
                else _section_role(section, i, len(discovered), metadata))
        section["role"] = role
        if role == "narrative":
            number = len(manifest["chapters"]) + 1
            section["id"] = f"ch{number:04d}"
            section["number"] = number
            manifest["chapters"].append(section)
        else:
            number = len(manifest["non_narrative_sections"]) + 1
            section["id"] = f"matter{number:03d}"
            section["number"] = number
            manifest["non_narrative_sections"].append(section)

    if not manifest["chapters"]:
        raise PipelineError("No narrative sections found after excluding front/back matter.")

    # Persist excluded matter for inspection, but do not analyze/translate it.
    for section in manifest["non_narrative_sections"]:
        atomic_text(
            project / "matter" / section["id"] / "source.txt",
            "\n\n".join(b["text"] for b in section["blocks"]) + "\n",
        )

    limit = int(settings["whole_section_char_limit"])
    for i, chapter in enumerate(manifest["chapters"], 1):
        ui.emit(ProgressEvent(
            kind="import_plan_progress", current=i - 1, total=len(manifest["chapters"]),
            values={"section_number": i},
        ))
        source_text = "\n\n".join(b["text"] for b in chapter["blocks"])
        chapter["source_chars"] = len(source_text)
        chapter["source_words"] = sum(len(re.findall(r"\S+", b["text"])) for b in chapter["blocks"])
        chapter["source_tokens"] = count(source_text)
        chapter["scenes"] = _scenes_for_section(chapter)
        chapter["chunk_ids"] = []

        # Keep an unsplit piece representation for manifest/fingerprint compatibility.
        chapter["pieces"] = []
        for block in chapter["blocks"]:
            piece = copy.deepcopy(block)
            piece.update(parent_id=block["id"], start=0, end=len(block["text"]))
            chapter["pieces"].append(piece)

        by_id = {b["id"]: b for b in chapter["pieces"]}
        if chapter["source_chars"] <= limit:
            planned = [(chapter["scenes"], chapter["pieces"], "whole_section")]
        else:
            planned = []
            for scene in chapter["scenes"]:
                group = [by_id[bid] for bid in scene["block_ids"]]
                planned.append(([scene], group, "scene"))

        for local, (scenes, group, unit_kind) in enumerate(planned, 1):
            cid = f"{chapter['id']}_c{local:04d}"
            source = "\n\n".join(b["text"] for b in group)
            chunk = {
                "id": cid,
                "chapter_id": chapter["id"],
                "index_in_chapter": local,
                "number": len(manifest["chunks"]) + 1,
                "unit_kind": unit_kind,
                "scene_ids": [s["id"] for s in scenes],
                "blocks": group,
                "source_chars": len(source),
                "source_words": sum(len(re.findall(r"\S+", b["text"])) for b in group),
                "source_tokens": count(source),
                "sentences": [],
            }
            for piece in group:
                for k, (start, end) in enumerate(sentence_spans(piece["text"]), 1):
                    chunk["sentences"].append({
                        "id": f"{piece['id']}:S{k:03d}",
                        "block_id": piece["id"],
                        "text": piece["text"][start:end].strip(),
                    })
            chapter["chunk_ids"].append(cid)
            manifest["chunks"].append(chunk)

        atomic_text(project / "chapters" / chapter["id"] / "source.txt", source_text + "\n")
        ui.emit(ProgressEvent(
            kind="import_plan_progress", current=i, total=len(manifest["chapters"]),
            values={"section_number": i},
        ))

    manifest["source_fingerprint"] = digest(manifest["files"])
    manifest["warnings"].append(
        "Natural structure policy: sections <= 10,000 characters stay whole; larger sections split only at detected scene boundaries; individual scenes are never size-split."
        if limit == 10000 else
        f"Natural structure policy: sections <= {limit:,} characters stay whole; larger sections split only at detected scene boundaries; individual scenes are never size-split."
    )
    manifest["warnings"].append(
        "Structure hints recognize explicit numbered/Prologue/Epilogue headings in h3-h6, calibre27 named major sections, and calibre19/calibre21/calibre23 plus image-only scene separators. Inspect book.json before analysis."
    )
    return manifest
