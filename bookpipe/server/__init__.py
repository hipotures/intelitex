"""Composition and lifetime of the single Intelitex HTTP/supervisor process."""
from pathlib import Path
import signal

from ..application.workspaces import WorkspaceQueries
from ..bootstrap import create_application
from ..runtime.registry import JobRegistry, default_registry_path
from ..runtime.supervisor import JobSupervisor
from .http import IntelitexHTTPServer
from .service import ServerService


def serve(workspace_root: Path, bind: str = "127.0.0.1", port: int = 8780):
    application = create_application()
    workspaces = WorkspaceQueries(application.projects, workspace_root)
    registry = JobRegistry(default_registry_path())
    supervisor = JobSupervisor(registry, workspaces.root)
    server = None
    old_term = signal.getsignal(signal.SIGTERM)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        server = IntelitexHTTPServer((bind, port), ServerService(application, workspaces, supervisor))
        print(f"Intelitex serving http://{bind}:{server.server_port} (workspace root: {workspaces.root})", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        # Repeated terminal signals must not abort worker cleanup.
        old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            supervisor.shutdown()
            if server:
                server.server_close()
            registry.close()
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
