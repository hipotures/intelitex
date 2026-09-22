"""Application facade composed from explicit service groups."""
from __future__ import annotations

from .ports import ApplicationDependencies, NullProgress, ProgressSink
from .exports import ExportsService
from .operations import OperationsService
from .pipeline import PipelineService
from .publishing import PublishingService
from .projects import ProjectsService
from .review import ReviewService
from .reader import ReaderService
from .workflow import WorkflowQueries
from .web import WebWorkspaceService


class Application:
    """Lightweight service container; construction performs no I/O."""

    def __init__(self, dependencies: ApplicationDependencies, progress: ProgressSink | None = None):
        sink = progress or NullProgress()
        self.publishing = PublishingService(dependencies, sink)
        self.projects = ProjectsService(dependencies, sink, self.publishing)
        self.pipeline = PipelineService(dependencies, sink, self.publishing)
        self.review = ReviewService(dependencies, sink)
        self.reader = ReaderService(dependencies, sink)
        self.exports = ExportsService(dependencies, sink)
        self.operations = OperationsService(dependencies, sink)
        self.workflow = WorkflowQueries(dependencies, self.publishing)
        self.web = WebWorkspaceService(dependencies)
