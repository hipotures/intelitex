"""Application facade composed from explicit service groups."""
from __future__ import annotations

from .ports import ApplicationDependencies, NullProgress, ProgressSink
from .exports import ExportsService
from .operations import OperationsService
from .pipeline import PipelineService
from .projects import ProjectsService
from .review import ReviewService
from .reader import ReaderService


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
