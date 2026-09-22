from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import pytest
from defusedxml import ElementTree as ET

from bookpipe.application import (
    ImportBookCommand,
    PublicationStatusCommand,
    PublishCommand,
    StatusCommand,
)
from bookpipe.bootstrap import create_application
from bookpipe.infrastructure.epub_publisher import EpubPublicationBuilder
from bookpipe.store import Store
from bookpipe.util import PipelineError, atomic_json, digest, read_json


class OfflineImportProvider:
    identity = {"id": "publication-fixture"}
    tokenizer_identity = {"kind": "fixture", "id": "publication-fixture"}

    def discover(self):
        return self.identity

    def count(self, text):
        return max(1, len(text) // 4)


class CountingOfflinePool:
    constructions = 0

    def __init__(self, *args, **kwargs):
        type(self).constructions += 1
        self.client = OfflineImportProvider()
        self.identity = {}

    def discover(self, pass_no=1):
        self.identity = self.client.discover()
        return self.identity

    def count(self, text):
        return self.client.count(text)

    @property
    def tokenizer_identity(self):
        return self.client.tokenizer_identity

    def close(self):
        return None


class FailingBuilder:
    def __init__(self, delegate):
        self.delegate = delegate
        self.build_calls = 0

    def inspect(self, *args, **kwargs):
        return self.delegate.inspect(*args, **kwargs)

    def build(self, request):
        self.build_calls += 1
        raise PipelineError("synthetic package serialization failure")


def _unpack_public_domain_epub(tmp_path: Path) -> Path:
    archive = Path(__file__).parents[1] / "examples" / "public-domain" / "andersen-mini.epub"
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(archive) as epub:
        epub.extractall(source)
    return source


def _import_project(tmp_path: Path, *, builder=None):
    CountingOfflinePool.constructions = 0
    source = _unpack_public_domain_epub(tmp_path)
    project = tmp_path / "project"
    app = create_application(
        provider_factory=CountingOfflinePool,
        publication_builder=builder or EpubPublicationBuilder(),
    )
    app.projects.import_book(ImportBookCommand(project=project, source=source))
    assert CountingOfflinePool.constructions == 1
    CountingOfflinePool.constructions = 0
    return app, project, source


def _set_pipeline_flags(project: Path, *, analysis=True, approved=True) -> None:
    store = Store(project)
    try:
        with store.db:
            store.set("analysis_done", analysis)
            store.set("approved", approved)
    finally:
        store.close()


def _complete_translations(project: Path, prefix: str = "Przekład: ") -> None:
    book = read_json(project / "book.json")
    store = Store(project)
    try:
        with store.db:
            store.set("analysis_done", True)
            store.set("approved", True)
        for chunk in book["chunks"]:
            final = {
                "translations": [
                    {"id": block["id"], "text": prefix + block["text"]}
                    for block in chunk["blocks"]
                ]
            }
            path = project / "artifacts" / "publication-fixture" / f"{chunk['id']}.json"
            atomic_json(path, final)
            store.save_job("pass5/" + chunk["id"], "fixture-" + chunk["id"], path, {})
            store.finish_chunk(chunk["id"], str(path.relative_to(project)), [], digest([]))
    finally:
        store.close()


def _change_first_final(project: Path) -> None:
    book = read_json(project / "book.json")
    store = Store(project)
    try:
        state = store.chunk(book["chunks"][0]["id"])
        path = project / state["final_path"]
        value = read_json(path)
        value["translations"][0]["text"] += " Nowa rewizja."
        atomic_json(path, value)
        with store.db:
            store.db.execute(
                "UPDATE jobs SET result_hash=? WHERE result_path=?",
                (digest(path.read_bytes()), state["final_path"]),
            )
    finally:
        store.close()


def test_publish_preconditions_distinguish_p1_approval_pending_and_stale(tmp_path):
    app, project, _ = _import_project(tmp_path)
    with pytest.raises(PipelineError, match="completed P1"):
        app.publishing.publish(PublishCommand(project))

    _set_pipeline_flags(project, analysis=True, approved=False)
    with pytest.raises(PipelineError, match="approved terminology"):
        app.publishing.publish(PublishCommand(project))

    _set_pipeline_flags(project)
    with pytest.raises(PipelineError, match="pending"):
        app.publishing.publish(PublishCommand(project))

    _complete_translations(project)
    book = read_json(project / "book.json")
    store = Store(project)
    try:
        with store.db:
            store.db.execute("UPDATE chunks SET status='stale' WHERE id=?", (book["chunks"][0]["id"],))
    finally:
        store.close()
    with pytest.raises(PipelineError, match="stale"):
        app.publishing.publish(PublishCommand(project))


def test_explicit_publish_builds_valid_epub_without_provider_and_preserves_source(tmp_path):
    app, project, source = _import_project(tmp_path)
    _complete_translations(project)
    source_snapshot = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }

    result = app.publishing.publish(PublishCommand(project))
    assert result.built is True
    assert result.status.state == "published" and result.status.current is True
    assert result.status.output_path == project / "published" / "Three Fairy Tales by Hans Christian Andersen — Intelitex Mini Sample [PL].epub"
    assert CountingOfflinePool.constructions == 0

    with zipfile.ZipFile(result.status.output_path) as epub:
        infos = epub.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert epub.read("mimetype") == b"application/epub+zip"
        container = ET.fromstring(epub.read("META-INF/container.xml"))
        opf_name = container.find(".//{*}rootfile").get("full-path")
        package = ET.fromstring(epub.read(opf_name))
        assert package.find(".//{*}metadata/{*}language").text == "pl"
        assert package.find(".//{*}metadata/{*}title").text == "Three Fairy Tales by Hans Christian Andersen — Intelitex Mini Sample"
        assert package.find(".//{*}metadata/{*}creator").text == "Hans Christian Andersen"
        metadata_xml = ET.tostring(package, encoding="unicode")
        assert "generated-by" in metadata_xml and "Intelitex" in metadata_xml
        assert "intelitex:source-language" in metadata_xml and "en" in metadata_xml
        assert read_json(project / "book.json")["source_fingerprint"] in metadata_xml
        assert epub.read("EPUB/styles/book.css") == source_snapshot["EPUB/styles/book.css"]
        assert epub.read("EPUB/nav.xhtml") == source_snapshot["EPUB/nav.xhtml"]
        translated = epub.read("EPUB/text/the-buckwheat.xhtml").decode("utf-8")
        assert "Przekład:" in translated
        ET.fromstring(translated.encode("utf-8"))

    assert {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    } == source_snapshot
    status = app.projects.status(StatusCommand(project))
    assert status.translation_complete is True
    assert status.publication.state == "published" and status.publication.current is True


def test_publish_is_idempotent_and_failure_preserves_prior_epub_and_checkpoints(tmp_path):
    app, project, _ = _import_project(tmp_path)
    _complete_translations(project)
    first = app.publishing.publish(PublishCommand(project))
    epub_bytes = first.status.output_path.read_bytes()
    record = copy.deepcopy(read_json(project / "publication.json"))

    again = app.publishing.publish(PublishCommand(project))
    assert again.built is False
    assert again.status.output_path.read_bytes() == epub_bytes

    _change_first_final(project)
    database_before = (project / "state.sqlite3").read_bytes()
    failing = FailingBuilder(EpubPublicationBuilder())
    broken_app = create_application(
        provider_factory=CountingOfflinePool, publication_builder=failing,
    )
    with pytest.raises(PipelineError, match="synthetic package serialization failure"):
        broken_app.publishing.publish(PublishCommand(project))
    assert failing.build_calls == 1
    assert first.status.output_path.read_bytes() == epub_bytes
    assert (project / "state.sqlite3").read_bytes() == database_before
    failed_status = broken_app.publishing.status(PublicationStatusCommand(project))
    assert failed_status.state == "failed" and failed_status.current is False
    assert failed_status.output_path == first.status.output_path
    failed_record = read_json(project / "publication.json")
    assert failed_record["last_success"] == record["last_success"]
    assert failed_record["last_attempt"]["status"] == "failed"

    retry_app = create_application(
        provider_factory=CountingOfflinePool, publication_builder=EpubPublicationBuilder(),
    )
    retried = retry_app.publishing.publish(PublishCommand(project))
    assert retried.status.state == "published" and retried.status.current is True
    assert retried.status.output_path.read_bytes() != epub_bytes
    assert CountingOfflinePool.constructions == 0


def test_corrupt_final_artifact_is_rejected_before_build(tmp_path):
    builder = FailingBuilder(EpubPublicationBuilder())
    app, project, _ = _import_project(tmp_path, builder=builder)
    _complete_translations(project)
    book = read_json(project / "book.json")
    store = Store(project)
    try:
        path = project / store.chunk(book["chunks"][0]["id"])["final_path"]
    finally:
        store.close()
    path.write_text(json.dumps({"translations": []}), encoding="utf-8")
    with pytest.raises(PipelineError, match="Final artifact is missing or changed"):
        app.publishing.publish(PublishCommand(project))
    assert builder.build_calls == 0


def test_generic_html_project_reports_clear_publication_error(tmp_path):
    source = tmp_path / "html"
    source.mkdir()
    (source / "chapter.html").write_text("<html><body><p>Text.</p></body></html>", encoding="utf-8")
    project = tmp_path / "project"
    app = create_application(provider_factory=CountingOfflinePool)
    app.projects.import_book(ImportBookCommand(project=project, source=source))
    _complete_translations(project)
    with pytest.raises(PipelineError, match="generic HTML directory"):
        app.publishing.publish(PublishCommand(project))


def test_supported_emphasis_is_reconstructed_as_xhtml_not_literal_markers(tmp_path):
    source = _unpack_public_domain_epub(tmp_path)
    chapter = source / "EPUB" / "text" / "the-buckwheat.xhtml"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace("Very often", "<em>Very</em> often", 1),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    app = create_application(provider_factory=CountingOfflinePool)
    app.projects.import_book(ImportBookCommand(project=project, source=source))
    _complete_translations(project)
    result = app.publishing.publish(PublishCommand(project))
    with zipfile.ZipFile(result.status.output_path) as epub:
        xhtml = epub.read("EPUB/text/the-buckwheat.xhtml").decode("utf-8")
    assert "<em>Very</em>" in xhtml
    assert "*Very*" not in xhtml


def test_meaningful_inline_link_fails_instead_of_being_silently_discarded(tmp_path):
    source = _unpack_public_domain_epub(tmp_path)
    chapter = source / "EPUB" / "text" / "the-buckwheat.xhtml"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace(
            "Very often", '<a href="https://example.test/reference">Very</a> often', 1,
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    app = create_application(provider_factory=CountingOfflinePool)
    app.projects.import_book(ImportBookCommand(project=project, source=source))
    _complete_translations(project)
    with pytest.raises(PipelineError, match="unsupported inline <a>"):
        app.publishing.publish(PublishCommand(project))
    assert not list((project / "published").glob("*.epub"))
