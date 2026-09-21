"""Application facade composed from explicit service groups."""
from __future__ import annotations

from .ports import ApplicationDependencies, NullProgress, ProgressSink
from .sessions import OperationScope, ReaderScope


class _ScopedService:
    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink):
        self.dependencies = dependencies
        self.progress = progress

    def operation_scope(self, project):
        return OperationScope(self.dependencies, project, self.progress)


class _ReaderScopedService(_ScopedService):
    def reader_scope(self, project):
        return ReaderScope(self.dependencies, project, self.progress)


class Application:
    """Lightweight service container; construction performs no I/O."""

    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink | None = None):
        sink = progress or NullProgress()
        self.projects = _ScopedService(dependencies, sink)
        self.pipeline = _ScopedService(dependencies, sink)
        self.review = _ScopedService(dependencies, sink)
        self.reader = _ReaderScopedService(dependencies, sink)
        self.exports = _ScopedService(dependencies, sink)
        self.operations = _ScopedService(dependencies, sink)
