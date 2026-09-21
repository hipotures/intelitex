from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from bookpipe.application.api import Application
from bookpipe.application.ports import ApplicationDependencies
from bookpipe.bootstrap import create_application


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
        with app.pipeline.operation_scope(tmp_path / "project") as scope:
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
    with app.reader.reader_scope(tmp_path / "project") as scope:
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
