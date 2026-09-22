"""Disposable browser-test server. All generation targets the in-process offline fixture."""
import json
import shutil
import signal
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bookpipe.bootstrap as bootstrap

if len(sys.argv) > 1 and sys.argv[1] == '--worker':
    bootstrap.BUNDLE = Path(sys.argv[2])
    from bookpipe.runtime.worker import main
    raise SystemExit(main())

from bookpipe.application.commands import ImportBookCommand
from bookpipe.application.workspaces import WorkspaceQueries
from bookpipe.runtime.registry import JobRegistry
from bookpipe.runtime.supervisor import JobSupervisor
from bookpipe.server.asgi import ASGIServer
from bookpipe.server.service import ServerService
from test_pipeline import server as offline_server, make_epub_source

with tempfile.TemporaryDirectory(prefix='intelitex-browser-') as directory:
    base = Path(directory)
    provider = offline_server.__wrapped__()
    state, port = next(provider)
    bundle = base / 'bundle'
    bundle.mkdir()
    shutil.copytree(bootstrap.BUNDLE / 'prompts', bundle / 'prompts')
    settings = json.loads((bootstrap.BUNDLE / 'settings.default.json').read_text())
    settings['port'] = port
    settings['profiles']['local']['options']['port'] = port
    settings['profiles'] = {'local': settings['profiles']['local']}
    (bundle / 'settings.default.json').write_text(json.dumps(settings))
    bootstrap.BUNDLE = bundle
    sources, workspaces = base / 'sources', base / 'workspaces'
    sources.mkdir(); workspaces.mkdir()
    source = make_epub_source(sources)
    shutil.copytree(source, sources / 'second-book')
    app = bootstrap.create_application()
    app.projects.import_book(ImportBookCommand(workspaces / 'prepared', source))
    registry = JobRegistry(base / 'registry' / 'jobs.sqlite3')
    supervisor = JobSupervisor(registry, workspaces, command_factory=lambda spec: [sys.executable, __file__, '--worker', str(bundle)], interrupt_grace=.5, terminate_grace=.3)
    service = ServerService(app, WorkspaceQueries(app.projects, workspaces), supervisor, import_root=sources)
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 0
    server = ASGIServer(('127.0.0.1', port), service)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    info = {'url': f'http://127.0.0.1:{server.server_port}', 'root': str(base)}
    if '--info' in sys.argv:
        Path(sys.argv[sys.argv.index('--info') + 1]).write_text(json.dumps(info))
    print(json.dumps(info), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == 'quit':
                break
    finally:
        supervisor.shutdown(); server.shutdown(); thread.join(); server.server_close(); registry.close()
        provider.close()
