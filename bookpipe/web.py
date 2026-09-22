"""FastAPI delivery-adapter foundation, independent of existing HTTP servers.

Future routes must call application services through the composition root.
Constructing this scaffold opens no projects and starts no runtime workers.
"""
from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create the technical ASGI app without initializing domain resources."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Process liveness only; not project/provider readiness."""
        return {"status": "ok"}

    return app
