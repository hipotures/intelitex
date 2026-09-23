"""Composition and lifetime of the single Intelitex HTTP/supervisor process."""
from pathlib import Path
import os
import signal
import sys
import threading

from ..application.workspaces import WorkspaceQueries
from ..bootstrap import create_application
from ..runtime.registry import JobRegistry, default_registry_path
from ..runtime.supervisor import JobSupervisor
from .asgi import ASGIServer as IntelitexHTTPServer
from .service import ServerService
from .reload import HANDOFF_ENV, read_handoff, register_server, unregister_server, write_handoff


def serve(workspace_root: Path, bind: str = "127.0.0.1", port: int = 8780, *, import_root: Path | None = None):
    stopping = threading.Event()
    reloading = threading.Event()
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def interrupt(signum, frame):
        stopping.set()

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, lambda signum, frame: reloading.set())
    registry = supervisor = server = server_thread = None
    server_record = None
    server_errors = []
    resume_specs = []
    should_exec = False
    try:
        application = create_application()
        workspaces = WorkspaceQueries(application.projects, workspace_root)
        registry = JobRegistry(default_registry_path())
        supervisor = JobSupervisor(registry, workspaces.root)
        server_record = register_server(workspaces.root)
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
        for spec in read_handoff(workspaces.root):
            supervisor.start(spec)
        print(f"Intelitex serving http://{bind}:{server.server_port} (workspace root: {workspaces.root})", flush=True)
        reload_requested = False
        while not stopping.wait(0.1) and not server.finished.is_set():
            if reloading.is_set() and not reload_requested:
                reload_requested = True
                supervisor.request_reload()
                print("Intelitex reload requested; waiting for current translation passes to checkpoint.", flush=True)
            if reload_requested and supervisor.reload_drained():
                resume_specs = list(supervisor.reload_specs)
                should_exec = True
                break
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
                        if server_record is not None:
                            unregister_server(server_record)
                        for signum, handler in old_handlers.items():
                            signal.signal(signum, handler)
    if server_errors:
        raise server_errors[0]
    if stopping.is_set():
        print("Intelitex stopped.", flush=True)
    elif should_exec:
        if resume_specs:
            os.environ[HANDOFF_ENV] = str(write_handoff(workspaces.root, resume_specs))
        args = [sys.executable, "-c", "from bookpipe.cli import main; raise SystemExit(main())",
                "serve", "--workspace-root", str(workspaces.root), "--bind", bind,
                "--port", str(server.server_port)]
        if import_root is not None:
            args.extend(["--import-root", str(import_root)])
        print("Intelitex reloading code and resuming checkpointed work.", flush=True)
        os.execv(sys.executable, args)
