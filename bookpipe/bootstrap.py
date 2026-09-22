"""Composition root for the Intelitex application services."""
from __future__ import annotations

from pathlib import Path

from .application import Application
from .application.ports import ApplicationDependencies, ProgressSink


BUNDLE = Path(__file__).resolve().parent.parent


def create_application(progress: ProgressSink | None = None, *, provider_factory=None,
                       store_factory=None, plan_fingerprint_fn=None,
                       publication_builder=None) -> Application:
    """Build the dependency graph without opening files or contacting providers."""
    # Imports stay inside the composition root so importing the public API is
    # inert and application modules never depend on concrete infrastructure.
    if provider_factory is None:
        from .provider_registry import ProviderPool
        provider_factory = ProviderPool
    if store_factory is None:
        from .store import Store
        store_factory = Store
    from .util import plan_fingerprint, project_lock, reader_lock
    from .infrastructure.epub_publisher import EpubPublicationBuilder
    from .infrastructure.project_files import LocalProjectFiles
    from .infrastructure.read_store import ReadStore
    if plan_fingerprint_fn is None:
        plan_fingerprint_fn = plan_fingerprint
    if publication_builder is None:
        publication_builder = EpubPublicationBuilder()

    return Application(
        ApplicationDependencies(
            project_lock=project_lock,
            reader_lock=reader_lock,
            store_factory=store_factory,
            read_store_factory=ReadStore,
            provider_factory=provider_factory,
            bundle=BUNDLE,
            plan_fingerprint=plan_fingerprint_fn,
            files=LocalProjectFiles(),
            publication_builder=publication_builder,
        ),
        progress,
    )
