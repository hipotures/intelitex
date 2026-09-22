"""Composition and lifetime of the single Intelitex HTTP/supervisor process."""
from pathlib import Path
import signal
import threading

from ..application.workspaces import WorkspaceQueries
from ..bootstrap import create_application
from ..runtime.registry import JobRegistry, default_registry_path
from ..runtime.supervisor import JobSupervisor
from .asgi import ASGIServer as IntelitexHTTPServer
from .service import ServerService


def serve(workspace_root: Path, bind: str = "127.0.0.1", port: int = 8780, *, import_root: Path | None = None):
    stopping = threading.Event()
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def interrupt(signum, frame):
        stopping.set()

    for signum in old_handlers:
        signal.signal(signum, interrupt)
    registry = supervisor = server = server_thread = None
    server_errors = []
    try:
        application = create_application()
        workspaces = WorkspaceQueries(application.projects, workspace_root)
        registry = JobRegistry(default_registry_path())
        supervisor = JobSupervisor(registry, workspaces.root)
        if stopping.is_set():
            return
        server = IntelitexHTTPServer((bind, port), ServerService(application, workspaces, supervisor, import_root=import_root))

        def run_server():
            try:
                server.serve_forever(poll_interval=0.2)
            except BaseException as exc:
                server_errors.append(exc)

        server_thread = threading.Thread(target=run_server, name="intelitex-http", daemon=True)
        server_thread.start()
        print(f"Intelitex serving http://{bind}:{server.server_port} (workspace root: {workspaces.root})", flush=True)
        while not stopping.wait(0.1) and not server.finished.is_set():
            pass
    finally:
        try:
            if supervisor is not None:
                supervisor.begin_shutdown()
            if server is not None:
                server.shutdown()
                if server_thread is not None:
                    server_thread.join(timeout=5)
                    if server_thread.is_alive():
                        raise RuntimeError("HTTP server did not stop during shutdown.")
        finally:
            try:
                if supervisor is not None:
                    supervisor.shutdown()
            finally:
                try:
                    if server is not None:
                        server.server_close()
                finally:
                    try:
                        if registry is not None:
                            registry.close()
                    finally:
                        for signum, handler in old_handlers.items():
                            signal.signal(signum, handler)
    if server_errors:
        raise server_errors[0]
    if stopping.is_set():
        print("Intelitex stopped.", flush=True)
