"""Resource scopes shared by all application services."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .ports import ApplicationDependencies, NullProgress, ProgressSink


class OperationScope:
    """Own one project lock and lazily created Store/provider resources."""

    def __init__(self, dependencies: ApplicationDependencies, project: Path,
                 progress: ProgressSink | None = None, *, create: bool = False):
        self.dependencies = dependencies
        self.project = project.resolve()
        self.progress = progress or NullProgress()
        self.create = create
        self._stack: ExitStack | None = None
        self._store: Any = None
        self._providers: Any = None

    def __enter__(self) -> OperationScope:
        if not self.create and not self.dependencies.files.is_file(self.project / "book.json"):
            from ..util import PipelineError
            raise PipelineError("Project not imported. Run import first.")
        stack = ExitStack()
        try:
            stack.enter_context(self.dependencies.project_lock(self.project))
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return self

    @property
    def store(self) -> Any:
        if self._stack is None:
            raise RuntimeError("OperationScope must be entered before resources are used.")
        if self._store is None:
            self._store = self.dependencies.store_factory(self.project)
            self._stack.callback(self._store.close)
        return self._store

    def providers(self, settings: dict, *, profile: str | None = None,
                  pass_profiles: dict[int, str] | None = None) -> Any:
        if self._stack is None:
            raise RuntimeError("OperationScope must be entered before resources are used.")
        if self._providers is None:
            self._providers = self.dependencies.provider_factory(
                settings, self.progress, self.project,
                command_profile=profile, command_pass_profiles=pass_profiles or {},
            )
            self._stack.callback(self._providers.close)
        return self._providers

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._stack is not None
        self._stack.close()
        self._stack = None


class ReaderScope(OperationScope):
    """Reader lifetime scope: separate lock and no mutable Store by default."""

    def __enter__(self) -> ReaderScope:
        stack = ExitStack()
        try:
            stack.enter_context(self.dependencies.reader_lock(self.project))
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return self

    @property
    def store(self) -> Any:
        raise RuntimeError("ReaderScope does not expose a mutable Store.")
