"""One production FastAPI adapter; detached worker lifetimes belong to supervisor."""
import json
import re
import socket
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse
import uvicorn

from ..application.imports import DestinationConflict, ImportDisabled, workspace_destination
from ..application.reader import MarkerConflict
from ..application.review import ReviewConflict
from ..application.web import LifecycleConflict, WorkspaceArchived
from ..processing import AnalysisMembershipLocked, ConfigConflict, ModelChangeRequired
from ..runtime.supervisor import JobConflict, RequestConflict
from ..util import LockConflict, PipelineError
from .http import BODY_LIMIT
from .routes import dispatch
from .serialization import safe_json

HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
           'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}


def json_response(status, value):
    return Response(json.dumps(safe_json(value), ensure_ascii=True, allow_nan=False), status,
                    headers=HEADERS, media_type='application/json')


def error(exc):
    for kind, status, code, message in (
        (ReviewConflict, 409, 'review_revision_conflict', 'Review changed elsewhere. Reload the latest review to continue.'),
        (MarkerConflict, 409, 'marker_revision_conflict', 'Marker state or translated text changed.'),
        (LifecycleConflict, 409, 'lifecycle_revision_conflict', 'Workspace membership changed.'),
        (ConfigConflict, 409, 'config_revision_conflict', 'Configuration changed. Reload before saving.'),
        (AnalysisMembershipLocked, 409, 'analysis_membership_locked', 'P1 membership is frozen after its first attempt.'),
        (ModelChangeRequired, 409, 'model_change_confirmation_required', 'Confirm model changes for future work.'),
        (WorkspaceArchived, 409, 'workspace_archived', 'Restore this workspace before running or editing.'),
        (RequestConflict, 409, 'request_key_conflict', 'Request key already belongs to another operation.'),
        (DestinationConflict, 409, 'destination_exists', 'Import destination already exists.'),
        (ImportDisabled, 403, 'import_disabled', 'Web import is disabled.'),
        ((JobConflict, LockConflict), 409, 'workspace_busy', 'Workspace busy or server stopping.'),
        (PermissionError, 403, 'origin_rejected', 'Same-origin access required.'),
        ((KeyError, FileNotFoundError), 404, 'not_found', 'Not found.'),
        (OverflowError, 413, 'body_too_large', 'JSON body too large.'),
        ((ValueError, TypeError), 400, 'invalid_request', 'Invalid request.'),
        (PipelineError, 422, 'invalid_project_state', 'Project operation unavailable; inspect locally.'),
    ):
        if isinstance(exc, kind):
            return json_response(status, {'error': {'code': code, 'message': message, 'details': {}}})
    return json_response(500, {'error': {'code': 'internal_error', 'message': 'Internal server error.', 'details': {}}})


async def body(request):
    if request.headers.get('transfer-encoding') or request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise ValueError('Invalid framing.')
    lengths = request.headers.getlist('content-length')
    if len(lengths) != 1:
        raise ValueError('One Content-Length required.')
    length = int(lengths[0])
    if not 0 <= length <= BODY_LIMIT:
        raise OverflowError()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > BODY_LIMIT:
            raise OverflowError()
    if len(raw) != length:
        raise ValueError('Incomplete request.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate field.')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite value.')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError('Object required.')
    return value


def create_app(service, allowed_hosts, *, frontend=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    assets = (frontend or Path(__file__).resolve().parents[2] / 'web' / 'dist').resolve()

    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS', 'PUT'])
    async def route(request: Request, path: str):
        try:
            host = request.headers.get('host', '')
            if len(request.headers.getlist('host')) != 1 or host not in allowed_hosts:
                raise PermissionError()
            if request.headers.get('origin') not in {None, f'http://{host}'}:
                raise PermissionError()
            if request.headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'}:
                raise PermissionError()
            if path == 'api/events' and request.method == 'GET':
                return await stream(request, service)
            if path.startswith('api/') or path == 'api':
                if request.url.query and not (path == 'api/library' and request.method == 'GET'):
                    raise ValueError('Unexpected query parameters.')
                parts = [unquote(part) for part in request.scope['raw_path'].decode('ascii').split('/')[1:]]
                if request.method == 'GET' and len(parts) == 5 and parts[:2] == ['api', 'workspaces'] and parts[3:] == ['publication', 'download']:
                    data = await run_in_threadpool(service.publication_resource, parts[2])
                    return Response(data, media_type='application/epub+zip', headers={**HEADERS,
                                    'Content-Disposition': 'attachment; filename="translation.epub"'})
                payload = await body(request) if request.method in {'POST', 'PATCH', 'DELETE'} else None
                if request.method not in {'GET', 'POST', 'PATCH', 'DELETE'}:
                    raise KeyError()
                library_query = None
                if request.url.query:
                    parsed_query = parse_qs(request.url.query, keep_blank_values=True)
                    if any(len(values) != 1 for values in parsed_query.values()):
                        raise ValueError('Duplicate query parameter.')
                    library_query = {key: values[0] for key, values in parsed_query.items()}
                status, value = await run_in_threadpool(dispatch, service, request.method, parts, payload, library_query)
                return json_response(status, value)
            if request.method not in {'GET', 'HEAD'}:
                raise KeyError()
            if re.fullmatch(r'assets/[A-Za-z0-9_.-]+\.(?:js|css|woff2)', path):
                target = (assets / path).resolve(strict=True)
                if not target.is_relative_to(assets) or target.is_symlink():
                    raise KeyError()
                mime = 'text/javascript' if target.suffix == '.js' else 'text/css' if target.suffix == '.css' else 'font/woff2'
            elif path == '' or re.fullmatch(r'(?:work(?:/workspaces/[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/(?:prepare|analyse|review|translate|publish))?)?|reader(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)?)', path):
                target, mime = assets / 'index.html', 'text/html'
            else:
                raise KeyError()
            data = await run_in_threadpool(target.read_bytes)
            return Response(data if request.method != 'HEAD' else b'', media_type=mime, headers=HEADERS)
        except Exception as exc:
            return error(exc)
    return app


async def stream(request, service):
    query = parse_qs(request.url.query, keep_blank_values=True)
    if set(query) - {'workspace_id', 'job_id', 'after'} or any(len(v) != 1 for v in query.values()):
        raise ValueError('Invalid event filters.')
    supervisor = service.supervisor
    filters = {'workspace_root': supervisor.workspace_root}
    for key in ('workspace_id', 'job_id'):
        if key in query:
            filters[key] = query[key][0]
    if 'workspace_id' in filters:
        try:
            service.workspaces.resolve(filters['workspace_id'])
        except KeyError:
            # Import identity exists before book.json makes it discoverable.
            workspace_destination(service.workspaces.root, filters['workspace_id'])
    if 'job_id' in filters:
        supervisor.get(filters['job_id'])
    snapshot = await run_in_threadpool(supervisor.snapshot, workspace_id=filters.get('workspace_id'), job_id=filters.get('job_id'))
    cursor = int(request.headers.get('last-event-id', query.get('after', [str(snapshot['cursor'])])[0]))
    if cursor < 0 or cursor > snapshot['cursor']:
        cursor = snapshot['cursor']
    async def generate():
        nonlocal cursor
        yield 'event: snapshot\ndata: ' + json.dumps(safe_json(snapshot)) + '\n\n'
        while not supervisor.broker.closed:
            events = await run_in_threadpool(supervisor.broker.read, cursor, timeout=1, **filters)
            for event in events:
                yield f"id: {event['id']}\nevent: progress\ndata: " + json.dumps(safe_json(event)) + '\n\n'
                cursor = event['id']
            if not events:
                yield ': keepalive\n\n'
    return StreamingResponse(generate(), media_type='text/event-stream', headers={**HEADERS, 'X-Accel-Buffering': 'no'})


class ASGIServer:
    """Serve-entrypoint lifetime adapter; one prebound socket, one supervisor."""
    def __init__(self, address, service):
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(address)
        self.socket.listen(2048)
        self.finished = threading.Event()
        self.server_address = self.socket.getsockname()
        self.server_port = self.server_address[1]
        host = address[0]
        hosts = {f'{host}:{self.server_port}'}
        if host == '127.0.0.1':
            hosts.add(f'localhost:{self.server_port}')
        self.app = create_app(service, hosts)
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_config=None, access_log=False,
                                     workers=1, proxy_headers=False, timeout_graceful_shutdown=1))

    def serve_forever(self, poll_interval=.2):
        try:
            self.server.run(sockets=[self.socket])
        finally:
            self.finished.set()

    def shutdown(self):
        self.server.should_exit = True
        self.finished.wait(timeout=5)

    def server_close(self):
        self.socket.close()
