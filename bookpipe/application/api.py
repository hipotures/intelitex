"""Application facade composed from explicit service groups."""
from __future__ import annotations

from .ports import ApplicationDependencies, NullProgress, ProgressSink
from .exports import ExportsService
from .operations import OperationsService
from .pipeline import PipelineService
from .projects import ProjectsService
from .review import ReviewService
from .reader import ReaderService
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
        self.projects = ProjectsService(dependencies, sink)
        self.pipeline = PipelineService(dependencies, sink)
        self.review = ReviewService(dependencies, sink)
        self.reader = ReaderService(dependencies, sink)
        self.exports = ExportsService(dependencies, sink)
        self.operations = OperationsService(dependencies, sink)
