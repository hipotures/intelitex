from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from bookpipe.application.api import Application
from bookpipe.application import (
    AttemptsCommand, ExportCommand, ImportBookCommand, StatusCommand, UsageCommand,
)
from bookpipe.application.ports import ApplicationDependencies
from bookpipe.application.sessions import OperationScope, ReaderScope
from bookpipe.bootstrap import create_application
from bookpipe.store import Store
from bookpipe.util import project_lock, reader_lock
from bookpipe.util import plan_fingerprint


class Resource:
    def __init__(self, events, name):
        events.append(f"open:{name}")
        self.events, self.name = events, name

    def close(self):
        self.events.append(f"close:{self.name}")


def dependencies(events):
    @contextmanager
    def lock(path):
        events.append(f"lock:{path.name}")
        try:
            yield
        finally:
            events.append(f"unlock:{path.name}")

    return ApplicationDependencies(
        project_lock=lock,
        reader_lock=lambda path: lock(path.with_name(path.name + "-reader")),
        store_factory=lambda path: Resource(events, "store"),
        provider_factory=lambda *args, **kwargs: Resource(events, "providers"),
        bundle=Path("/unused"),
        plan_fingerprint=plan_fingerprint,
    )


def test_factory_construction_is_inert(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_application()
    assert isinstance(app, Application)
    assert list(tmp_path.iterdir()) == []


def test_operation_scope_owns_one_lock_and_closes_lazy_resources(tmp_path):
    events = []
    app = Application(dependencies(events))
    with pytest.raises(RuntimeError, match="boom"):
        with OperationScope(app.projects.dependencies, tmp_path / "project") as scope:
            assert scope.store is scope.store
            assert scope.providers({}) is scope.providers({})
            raise RuntimeError("boom")
    assert events == [
        "lock:project", "open:store", "open:providers",
        "close:providers", "close:store", "unlock:project",
    ]


def test_reader_scope_has_separate_lock_and_no_mutable_store(tmp_path):
    events = []
    app = Application(dependencies(events))
    with ReaderScope(app.projects.dependencies, tmp_path / "project") as scope:
        with pytest.raises(RuntimeError, match="does not expose"):
            _ = scope.store
    assert events == ["lock:project-reader", "unlock:project-reader"]


def test_application_and_domain_modules_have_no_presentation_imports():
    root = Path(__file__).parents[1] / "bookpipe"
    forbidden = {"bookpipe.cli", "bookpipe.ui", "bookpipe.review", "bookpipe.reader",
                 "rich", "argparse", "http.server", "webbrowser"}
    for directory in (root / "application", root / "domain"):
        if not directory.exists():
            continue
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            assert not any(name == blocked or name.startswith(blocked + ".")
                           for name in imports for blocked in forbidden), path


class LocalImportProvider:
    identity = {"id": "fixture-model"}
    tokenizer_identity = {"kind": "fixture", "id": "fixture-tokenizer"}

    def discover(self):
        return self.identity

    def count(self, text):
        return max(1, len(text) // 4)


class LocalImportPool:
    def __init__(self, *args, **kwargs):
        self.client = LocalImportProvider()
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
        return


def test_direct_project_operations_need_no_cli_or_http(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "chapter.html").write_text(
        "<html><body><h1>One</h1><p>A deterministic fixture paragraph.</p></body></html>",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    bundle = Path(__file__).parents[1]
    app = Application(ApplicationDependencies(
        project_lock=project_lock, reader_lock=reader_lock, store_factory=Store,
        provider_factory=LocalImportPool, bundle=bundle,
        plan_fingerprint=plan_fingerprint,
    ))

    imported = app.projects.import_book(ImportBookCommand(project=project, source=source))
    assert imported.narrative_sections == 1 and imported.translation_units == 1
    status = app.projects.status(StatusCommand(project))
    assert status.analysis_complete is False and status.approved is False
    assert [chunk.status for chunk in status.chunks] == ["pending"]

    exported = app.exports.export_text(ExportCommand(project))
    assert exported.internal_output.read_bytes() == b""
    assert app.operations.attempts(AttemptsCommand(project)).value == {"attempts": []}
    assert app.operations.usage(UsageCommand(project)).value["groups"] == []
