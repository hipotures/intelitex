"""Composition root for the Intelitex application services."""
from __future__ import annotations

from pathlib import Path

from .application import Application
from .application.ports import ApplicationDependencies, ProgressSink


BUNDLE = Path(__file__).resolve().parent.parent


def create_application(progress: ProgressSink | None = None) -> Application:
    """Build the dependency graph without opening files or contacting providers."""
    # Imports stay inside the composition root so importing the public API is
    # inert and application modules never depend on concrete infrastructure.
    from .provider_registry import ProviderPool
    from .store import Store
    from .util import project_lock, reader_lock

    return Application(
        ApplicationDependencies(
            project_lock=project_lock,
            reader_lock=reader_lock,
            store_factory=Store,
            provider_factory=ProviderPool,
            bundle=BUNDLE,
        ),
        progress,
    )
