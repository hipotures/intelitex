"""Deterministic EPUB publication adapter.

The application supplies an immutable publication request.  This module owns
package parsing, conservative XHTML rewriting, ZIP serialization and internal
EPUB validation; it never reads Intelitex project state.
"""
from __future__ import annotations

import copy
import os
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as XmlTree
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urldefrag, urlsplit

from bs4 import NavigableString, Tag
from defusedxml import ElementTree as ET

from ..application.ports import (
    PublicationBuildResult,
    PublicationRequest,
    PublicationSourceFile,
    PublicationSourceInfo,
)
from ..importer import LEAF_BLOCKS, extract_publication_document
from ..util import PipelineError, digest


EPUB_MIMETYPE = b"application/epub+zip"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
SUPPORTED_INLINE = {"em", "i", "strong", "b", "br"}
PUBLICATION_FORMAT = "intelitex-epub-v1"
INTELITEX_METADATA_IRI = "https://github.com/hipotures/intelitex/metadata#"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _safe_member(value: str, label: str) -> str:
    path = PurePosixPath(unquote(value))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise PipelineError(f"Unsafe {label} path in EPUB package: {value!r}.")
    return path.as_posix()


def _package_path_from_container(source_root: Path) -> str:
    container = source_root / "META-INF" / "container.xml"
    if not container.is_file():
        raise PipelineError(
            "Publishing requires a real unpacked EPUB package with META-INF/container.xml. "
            "Generic HTML-directory imports cannot be published as EPUB."
        )
    try:
        root = ET.fromstring(container.read_bytes())
    except Exception as exc:
        raise PipelineError(f"EPUB container.xml is not valid XML: {exc}") from exc
    rootfiles = root.findall(".//{*}rootfile")
    if not rootfiles or not rootfiles[0].get("full-path"):
        raise PipelineError("EPUB container.xml has no package rootfile.")
    return _safe_member(rootfiles[0].get("full-path", ""), "package document")


def _metadata(package_raw: bytes) -> tuple[str, tuple[str, ...], str | None]:
    try:
        root = ET.fromstring(package_raw)
    except Exception as exc:
        raise PipelineError(f"EPUB package document is not valid XML: {exc}") from exc
    title_node = root.find(".//{*}metadata/{*}title")
    title = (title_node.text or "").strip() if title_node is not None else ""
    creators = tuple(
        value for node in root.findall(".//{*}metadata/{*}creator")
        if (value := (node.text or "").strip())
    )
    language_node = root.find(".//{*}metadata/{*}language")
    language = (language_node.text or "").strip() if language_node is not None else None
    return title, creators, language or None


def _source_members(source_root: Path) -> list[tuple[str, Path]]:
    members: list[tuple[str, Path]] = []
    for path in source_root.rglob("*"):
        if path.is_symlink():
            raise PipelineError(f"EPUB source contains a symbolic link, which is not published safely: {path}")
        if path.is_file():
            relative = path.relative_to(source_root).as_posix()
            _safe_member(relative, "source member")
            members.append((relative, path))
    members.sort(key=lambda item: item[0])
    return members


def _linked_member(document: str, href: str) -> str | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path:
        return document
    return _safe_member(posixpath.normpath(posixpath.join(posixpath.dirname(document),
                                                    unquote(parsed.path))), 'navigation link')


def _prune_navigation(raw: bytes, member: str, excluded_files: set[str]) -> bytes:
    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        raise PipelineError(f'EPUB navigation document is not valid XML: {exc}') from exc
    parent = {child: node for node in root.iter() for child in node}
    is_ncx = root.tag.rsplit('}', 1)[-1] == 'ncx'
    if is_ncx:
        for content in list(root.iter()):
            if content.tag.rsplit('}', 1)[-1] != 'content':
                continue
            if _linked_member(member, content.get('src', '')) not in excluded_files:
                continue
            point = parent.get(content)
            if point is None or point.tag.rsplit('}', 1)[-1] not in {'navPoint', 'pageTarget', 'navTarget'}:
                raise PipelineError('Cannot remove an unknown EPUB navigation target.')
            if point.tag.rsplit('}', 1)[-1] == 'navPoint' and any(
                    child.tag.rsplit('}', 1)[-1] == 'navPoint' for child in point):
                raise PipelineError('Cannot omit a navigation parent that contains retained chapters.')
            parent[point].remove(point)
    else:
        for link in list(root.iter()):
            if link.tag.rsplit('}', 1)[-1] != 'a' or _linked_member(member, link.get('href', '')) not in excluded_files:
                continue
            item = parent.get(link)
            while item is not None and item.tag.rsplit('}', 1)[-1] != 'li':
                item = parent.get(item)
            if item is None:
                raise PipelineError('Cannot remove an EPUB navigation link without a list item.')
            if any(child.tag.rsplit('}', 1)[-1] == 'a' and child is not link for child in item.iter()):
                parent[link].remove(link)
            else:
                parent[item].remove(item)
    return XmlTree.tostring(root, encoding='utf-8', xml_declaration=True)


class EpubPublicationBuilder:
    """Build and validate a translated EPUB without knowing project persistence."""

    def inspect(self, source_root: Path, package_document: str,
                source_files: tuple[PublicationSourceFile, ...]) -> PublicationSourceInfo:
        source_root = source_root.resolve()
        if not source_root.is_dir():
            raise PipelineError(f"EPUB source folder no longer exists: {source_root}")
        mimetype = source_root / "mimetype"
        if not mimetype.is_file() or mimetype.read_bytes() != EPUB_MIMETYPE:
            raise PipelineError("EPUB source must contain an exact application/epub+zip mimetype file.")
        container_package = _package_path_from_container(source_root)
        requested_package = _safe_member(package_document, "imported package document")
        if container_package != requested_package:
            raise PipelineError(
                f"EPUB container resolves to {container_package!r}, but the imported spine used "
                f"{requested_package!r}; publishing would be ambiguous."
            )
        package_path = source_root / PurePosixPath(container_package)
        if not package_path.is_file():
            raise PipelineError(f"EPUB package document is missing: {package_path}")

        expected = {item.path: item for item in source_files}
        for relative, item in expected.items():
            path = source_root / PurePosixPath(_safe_member(relative, "imported source"))
            if not path.is_file() or digest(path.read_bytes()) != item.sha256:
                raise PipelineError(
                    f"Imported source document is missing or changed: {path}. "
                    "Restore the source package used at import."
                )

        members = _source_members(source_root)
        package_fingerprint = digest([
            {"path": relative, "sha256": digest(path.read_bytes())}
            for relative, path in members
        ])
        title, creators, language = _metadata(package_path.read_bytes())
        return PublicationSourceInfo(
            package_fingerprint=package_fingerprint,
            title=title,
            creators=creators,
            source_language=language,
        )

    def build(self, request: PublicationRequest) -> PublicationBuildResult:
        source_info = self.inspect(
            request.source_root, request.package_document, request.source_files,
        )
        if source_info.package_fingerprint != request.package_fingerprint:
            raise PipelineError("EPUB source package changed after publication inputs were prepared; retry publish.")

        replacements: dict[str, list] = {}
        for block in request.blocks:
            replacements.setdefault(block.source_file, []).append(block)
        rendered: dict[str, bytes] = {}
        excluded_files = set(request.excluded_source_files)
        if excluded_files & set(replacements):
            raise PipelineError('Publication selection conflicts with translated source bindings.')
        for relative, bindings in replacements.items():
            source_path = request.source_root / PurePosixPath(_safe_member(relative, "XHTML source"))
            encoding = next((item.encoding for item in request.source_files if item.path == relative), None)
            if encoding is None:
                raise PipelineError(f"No frozen source record exists for translated file {relative!r}.")
            raw = source_path.read_bytes()
            blocks, _, soup, nodes = extract_publication_document(raw, encoding=encoding)
            seen_nodes: set[int] = set()
            for binding in bindings:
                if binding.source_ordinal < 0 or binding.source_ordinal >= len(blocks):
                    raise PipelineError(
                        f"Cannot map translated block {binding.id} to {relative}: source ordinal is missing."
                    )
                extracted = blocks[binding.source_ordinal]
                node = nodes[binding.source_ordinal]
                if extracted["text"] != binding.source_text:
                    raise PipelineError(
                        f"Cannot map translated block {binding.id} to {relative}: "
                        "the source text no longer matches the frozen manifest."
                    )
                if node is None or id(node) in seen_nodes:
                    raise PipelineError(
                        f"Cannot map translated block {binding.id} to one unique XHTML element in {relative}."
                    )
                seen_nodes.add(id(node))
                self._replace_block(node, binding.id, binding.source_text, binding.translated_text)
            html = soup.find("html")
            if isinstance(html, Tag):
                html["lang"] = request.target_language
                html["xml:lang"] = request.target_language
            rendered[relative] = soup.encode("utf-8", formatter="minimal")

        package_relative = _safe_member(request.package_document, "package document")
        package_path = request.source_root / PurePosixPath(package_relative)
        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        rendered[package_relative] = self._rewrite_package(
            package_path.read_bytes(), request.target_language,
            request.source_fingerprint, source_info.source_language, generated_at,
            excluded_files=excluded_files, package_relative=package_relative,
        )
        if excluded_files:
            package_root = ET.fromstring(package_path.read_bytes())
            for item in package_root.findall('.//{*}manifest/{*}item'):
                if 'nav' not in (item.get('properties') or '').split() and item.get('media-type') != 'application/x-dtbncx+xml':
                    continue
                member = _linked_member(package_relative, item.get('href', ''))
                if member and member not in excluded_files:
                    rendered[member] = _prune_navigation((request.source_root / member).read_bytes(),
                                                          member, excluded_files)

        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + request.output_path.name + ".", suffix=".tmp",
            dir=request.output_path.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self._write_zip(temporary, request.source_root, rendered, excluded_files)
            validation = validate_epub(
                temporary, request.target_language, required_xhtml=tuple(replacements),
                excluded_files=excluded_files,
            )
            os.replace(temporary, request.output_path)
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(request.output_path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return PublicationBuildResult(
            output_path=request.output_path,
            title=source_info.title or request.title,
            creators=source_info.creators,
            source_language=source_info.source_language,
            target_language=request.target_language,
            validation=validation,
            generated_at=generated_at,
        )

    @staticmethod
    def _replace_block(node: Tag, block_id: str, source_text: str, translated_text: str) -> None:
        if node.name not in LEAF_BLOCKS:
            raise PipelineError(
                f"Block {block_id} maps to <{node.name}>, not a safe leaf prose element."
            )
        if node.name == 'tr':
            # Import can bind the first table cell to its row while a second
            # cell is bound separately to a nested <p>. Replacing the whole
            # row would silently destroy that second translation.
            matches = [part for part in node.descendants
                       if isinstance(part, NavigableString) and str(part).strip() == source_text]
            if len(matches) != 1:
                raise PipelineError(f"Block {block_id} cannot be mapped to one table-cell text node.")
            parts, _, _ = _parse_inline(translated_text.strip(), block_id)
            original = matches[0]
            parent = original.parent
            position = parent.contents.index(original)
            original.extract()
            holder = parent_soup_new_tag('span', parent)
            _append_inline(holder, parts)
            for offset, child in enumerate(list(holder.contents)):
                parent.insert(position + offset, child.extract())
            return
        prefix_span = next((child for child in node.contents if isinstance(child, Tag)), None)
        if (prefix_span is not None and node.contents[0] is prefix_span
                and prefix_span.name == 'span' and set(prefix_span.attrs) == {'class'}
                and not prefix_span.find(True) and prefix_span.get_text()
                and source_text.startswith(prefix_span.get_text())
                and translated_text.startswith(prefix_span.get_text())):
            # Timeline dates are styled as an initial span. Keep the source
            # styling when the same date starts the saved translation.
            prefix_text = prefix_span.get_text()
            retained_prefix = copy.deepcopy(prefix_span)
            source_text = source_text[len(prefix_text):]
            translated_text = translated_text[len(prefix_text):]
        else:
            retained_prefix = None
        anchors: list[Tag] = []
        semantics: list[str] = []
        emphasized: list[Tag] = []
        breaks = 0
        for child in node.find_all(True):
            if child is prefix_span and retained_prefix is not None:
                continue
            name = child.name.lower()
            if name == "a" and not child.get("href") and not child.get_text(strip=True) and (
                child.get("id") or child.get("name")
            ):
                anchors.append(copy.deepcopy(child))
                continue
            if name == 'span' and child.attrs == {'class': ['char-dcrit']} and not child.find(True):
                # The imported plain text already contains this diacritic. The
                # source wrapper only selects a font for that one character.
                continue
            if name not in SUPPORTED_INLINE:
                raise PipelineError(
                    f"Block {block_id} contains unsupported inline <{name}> markup; "
                    "publishing stopped rather than discarding it."
                )
            if child.attrs and not (name in {'em', 'i', 'strong', 'b'}
                                    and set(child.attrs) == {'class'}):
                raise PipelineError(
                    f"Block {block_id} contains attributed inline <{name}> markup that cannot be "
                    "round-tripped safely."
                )
            if name == "br":
                breaks += 1
            else:
                semantics.append("em" if name in {"em", "i"} else "strong")
                emphasized.append(child)

        source_parts, source_semantics, source_breaks = _parse_inline(source_text, block_id)
        translated_parts, translated_semantics, translated_breaks = _parse_inline(
            translated_text.strip(), block_id,
        )
        if semantics != source_semantics or breaks != source_breaks:
            raise PipelineError(
                f"Block {block_id} source inline markup cannot be reconstructed deterministically."
            )
        for child in list(node.contents):
            child.extract()
        if retained_prefix is not None:
            node.append(retained_prefix)
        for anchor in anchors:
            node.append(anchor)
        _append_inline(node, translated_parts)
        if translated_semantics == source_semantics:
            translated_emphasis = node.find_all(['em', 'strong'])
            for source, translated in zip(emphasized, translated_emphasis, strict=True):
                if source.get('class'):
                    translated['class'] = list(source['class'])

    @staticmethod
    def _rewrite_package(raw: bytes, target_language: str, source_fingerprint: str,
                         source_language: str | None, generated_at: str, *,
                         excluded_files: set[str] | None = None, package_relative: str = '') -> bytes:
        try:
            root = ET.fromstring(raw)
        except Exception as exc:
            raise PipelineError(f"EPUB package document is not valid XML: {exc}") from exc
        namespace = root.tag.partition("}")[0].removeprefix("{") if root.tag.startswith("{") else ""
        if namespace:
            XmlTree.register_namespace("", namespace)
        excluded_files = excluded_files or set()
        if excluded_files:
            manifest = root.find('.//{*}manifest')
            spine = root.find('.//{*}spine')
            if manifest is None or spine is None:
                raise PipelineError('EPUB package has no manifest or spine.')
            removed_ids = set()
            matched_files = set()
            for item in list(manifest):
                member = _linked_member(package_relative, item.get('href', ''))
                if member in excluded_files:
                    if 'nav' in (item.get('properties') or '').split():
                        raise PipelineError('EPUB navigation document cannot be omitted.')
                    matched_files.add(member)
                    removed_ids.add(item.get('id'))
                    manifest.remove(item)
            if matched_files != excluded_files:
                raise PipelineError('Selected publication section does not map to one EPUB manifest document.')
            for itemref in list(spine):
                if itemref.get('idref') in removed_ids:
                    spine.remove(itemref)
            if not list(spine):
                raise PipelineError('Publication must retain at least one EPUB spine document.')
        XmlTree.register_namespace("dc", DC_NAMESPACE)
        root.set(f"{{{XML_NAMESPACE}}}lang", target_language)
        metadata = root.find(".//{*}metadata")
        if metadata is None:
            raise PipelineError("EPUB package document has no metadata element.")
        language = metadata.find("{*}language")
        if language is None:
            language = XmlTree.SubElement(metadata, f"{{{DC_NAMESPACE}}}language")
        language.text = target_language

        for child in list(metadata):
            if child.tag.endswith("}meta") or child.tag == "meta":
                if (
                    child.get("name", "").startswith("intelitex:")
                    or child.get("name") == "generator"
                    or child.get("property", "").startswith("intelitex:")
                ):
                    metadata.remove(child)
            elif child.tag.endswith("}source") or child.tag == "source":
                if (child.text or "").startswith("urn:intelitex:"):
                    metadata.remove(child)
        meta_tag = f"{{{namespace}}}meta" if namespace else "meta"
        values = (
            ("generated-by", "Intelitex"),
            ("source-language", source_language or "unknown"),
            ("target-language", target_language),
            ("source-fingerprint", source_fingerprint),
            ("publication-format", PUBLICATION_FORMAT),
        )
        if str(root.get("version", "")).startswith("3"):
            prefixes = root.get("prefix", "").strip()
            if not re.search(r"(?:^|\s)intelitex:\s", prefixes):
                root.set("prefix", (prefixes + f" intelitex: {INTELITEX_METADATA_IRI}").strip())
            modified = next((
                child for child in metadata
                if (child.tag.endswith("}meta") or child.tag == "meta")
                and child.get("property") == "dcterms:modified"
            ), None)
            if modified is None:
                modified = XmlTree.SubElement(metadata, meta_tag, {"property": "dcterms:modified"})
            modified.text = generated_at
            for name, content in values:
                node = XmlTree.SubElement(metadata, meta_tag, {"property": f"intelitex:{name}"})
                node.text = content
        else:
            for name, content in values:
                legacy_name = "generator" if name == "generated-by" else f"intelitex:{name}"
                XmlTree.SubElement(metadata, meta_tag, {"name": legacy_name, "content": content})
        source = XmlTree.SubElement(metadata, f"{{{DC_NAMESPACE}}}source")
        source.text = f"urn:intelitex:source-fingerprint:{source_fingerprint}"
        return XmlTree.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _write_zip(target: Path, source_root: Path, rendered: dict[str, bytes],
                   excluded_files: set[str] | None = None) -> None:
        members = _source_members(source_root)
        excluded_files = excluded_files or set()
        names = {relative for relative, _ in members}
        if "mimetype" not in names:
            raise PipelineError("EPUB source has no mimetype member.")
        with zipfile.ZipFile(target, "w") as archive:
            _write_member(archive, "mimetype", EPUB_MIMETYPE, zipfile.ZIP_STORED)
            for relative, path in members:
                if relative == "mimetype" or relative in excluded_files:
                    continue
                _write_member(
                    archive, relative, rendered.get(relative, path.read_bytes()),
                    zipfile.ZIP_DEFLATED,
                )


def _parse_inline(text: str, block_id: str):
    root: list = []
    children = root
    stack: list[tuple[str, list]] = []
    semantics: list[str] = []
    break_count = 0
    index = 0
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            children.append("".join(buffer))
            buffer.clear()

    while index < len(text):
        if text[index] == "\n":
            flush()
            children.append(("br", []))
            break_count += 1
            index += 1
            continue
        if text.startswith("**", index) and (not stack or stack[-1][0] != "em"):
            marker, size = "strong", 2
        elif text[index] == "*":
            marker, size = "em", 1
        else:
            buffer.append(text[index])
            index += 1
            continue
        flush()
        if stack and stack[-1][0] == marker:
            _, parent = stack.pop()
            children = parent
        else:
            nested: list = []
            children.append((marker, nested))
            semantics.append(marker)
            stack.append((marker, children))
            children = nested
        index += size
    flush()
    if stack:
        raise PipelineError(f"Block {block_id} contains unbalanced Intelitex emphasis markers.")
    return root, semantics, break_count


def _append_inline(parent: Tag, parts: list) -> None:
    for part in parts:
        if isinstance(part, str):
            parent.append(NavigableString(part))
            continue
        name, children = part
        tag = parent_soup_new_tag(name, parent)
        parent.append(tag)
        _append_inline(tag, children)


def parent_soup_new_tag(name: str, context: Tag) -> Tag:
    document = context
    while getattr(document, "parent", None) is not None:
        document = document.parent
    return document.new_tag(name)


def _write_member(archive: zipfile.ZipFile, name: str, raw: bytes, compression: int) -> None:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, raw, compress_type=compression, compresslevel=9 if compression else None)


def validate_epub(path: Path, target_language: str,
                  *, required_xhtml: tuple[str, ...] = (),
                  excluded_files: set[str] | None = None) -> tuple[str, ...]:
    checks: list[str] = []
    if not zipfile.is_zipfile(path):
        raise PipelineError("Generated publication is not a ZIP archive.")
    with zipfile.ZipFile(path) as archive:
        damaged = archive.testzip()
        if damaged is not None:
            raise PipelineError(f"Generated EPUB has a corrupt ZIP member: {damaged}")
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise PipelineError("Generated EPUB does not store mimetype as its first member.")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise PipelineError("Generated EPUB mimetype member is compressed.")
        if archive.read("mimetype") != EPUB_MIMETYPE:
            raise PipelineError("Generated EPUB has an invalid mimetype value.")
        checks.extend(("zip", "mimetype-first", "mimetype-stored"))
        names = set(archive.namelist())
        if "META-INF/container.xml" not in names:
            raise PipelineError("Generated EPUB is missing META-INF/container.xml.")
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
        except Exception as exc:
            raise PipelineError(f"Generated EPUB container.xml is invalid: {exc}") from exc
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise PipelineError("Generated EPUB container has no package rootfile.")
        package_name = _safe_member(rootfile.get("full-path", ""), "generated package")
        if package_name not in names:
            raise PipelineError("Generated EPUB container resolves to a missing package document.")
        try:
            package = ET.fromstring(archive.read(package_name))
        except Exception as exc:
            raise PipelineError(f"Generated EPUB package document is invalid: {exc}") from exc
        checks.extend(("container", "package"))
        language = package.find(".//{*}metadata/{*}language")
        if language is None or (language.text or "").strip() != target_language:
            raise PipelineError("Generated EPUB package does not contain the target language.")
        checks.append("target-language")
        metadata_xml = XmlTree.tostring(package, encoding="unicode")
        if "Intelitex" not in metadata_xml or "source-fingerprint" not in metadata_xml:
            raise PipelineError("Generated EPUB is missing Intelitex publication identity metadata.")
        checks.append("intelitex-metadata")
        manifest = {
            item.get("id"): item for item in package.findall(".//{*}manifest/{*}item")
            if item.get("id")
        }
        package_dir = posixpath.dirname(package_name)
        for item in manifest.values():
            href = item.get("href", "")
            if urlsplit(href).scheme or href.startswith("//"):
                continue
            local, _ = urldefrag(unquote(href))
            member = _safe_member(posixpath.normpath(posixpath.join(package_dir, local)), "manifest")
            if member not in names:
                raise PipelineError(f"Generated EPUB manifest references missing member: {member}")
        for itemref in package.findall(".//{*}spine/{*}itemref"):
            if itemref.get("idref") not in manifest:
                raise PipelineError(f"Generated EPUB spine has unknown idref: {itemref.get('idref')}")
        checks.append("manifest-spine")
        for relative in required_xhtml:
            if relative not in names:
                raise PipelineError(f"Generated EPUB is missing modified XHTML: {relative}")
            try:
                ET.fromstring(archive.read(relative))
            except Exception as exc:
                raise PipelineError(f"Generated XHTML {relative} is not parseable: {exc}") from exc
        checks.append("modified-xhtml")
        if excluded_files:
            for relative in names:
                if not relative.endswith(('.xhtml', '.html', '.ncx')):
                    continue
                try:
                    document = ET.fromstring(archive.read(relative))
                except Exception as exc:
                    raise PipelineError(f'Generated EPUB document {relative} is not parseable: {exc}') from exc
                for element in document.iter():
                    for attribute in ('href', 'src'):
                        href = element.get(attribute)
                        if href and _linked_member(relative, href) in excluded_files:
                            raise PipelineError('Generated EPUB still links to an omitted source document.')
            checks.append('omitted-links')
    return tuple(checks)
