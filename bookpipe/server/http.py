"""Small JSON/SSE HTTP adapter. Handlers only invoke control/query services."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from ..runtime.supervisor import JobConflict, RequestConflict
from ..application.web import LifecycleConflict, WorkspaceArchived
from ..processing import AnalysisMembershipLocked, ConfigConflict, ModelChangeRequired
from ..util import PipelineError, LockConflict
from ..application.review import ReviewConflict
from ..application.reader import MarkerConflict
from ..application.imports import ImportDisabled, DestinationConflict, workspace_destination
from .serialization import safe_json

BODY_LIMIT = 16 * 1024


class IntelitexHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service):
        self.service = service
        super().__init__(address, Handler)
        host, port = self.server_address[:2]
        names = {host, address[0]}
        if host == "127.0.0.1":
            names.add("localhost")
        self.allowed_hosts = {f"{name}:{port}" for name in names}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        return  # No request bodies, credentials, query strings or provider errors in logs.

    def _json(self, status, value):
        raw = json.dumps(safe_json(value), ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _guard(self):
        host = self.headers.get("Host", "")
        if host not in self.server.allowed_hosts:
            raise PermissionError("Invalid Host.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            raise PermissionError("Same-origin requests required.")
        if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
            raise PermissionError("Same-origin requests required.")

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise ValueError("One Content-Length is required.")
        length = int(lengths[0])
        if length < 0 or length > BODY_LIMIT:
            raise OverflowError("JSON body exceeds limit.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete JSON body.")
        def object_pairs(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON field.')
                result[key] = item
            return result
        def constant(value):
            raise ValueError('Non-finite JSON number.')
        value = json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError("JSON object required.")
        return value

    def do_GET(self):
        self._handle(False)

    def do_POST(self):
        self._handle(True)

    def do_PATCH(self):
        self._handle(True)

    def do_DELETE(self):
        self._handle(True)

    def _handle(self, mutation):
        try:
            self._guard()
            parsed = urlsplit(self.path)
            parts = [unquote(part) for part in parsed.path.split('/')[1:]]
            service = self.server.service
            if parsed.query and parts != ['api', 'events']:
                raise ValueError('Unexpected query parameters.')
            if not mutation and parts == ['api', 'events']:
                return self._events(parse_qs(parsed.query))
            from .routes import dispatch
            status, value = dispatch(service, self.command, parts, self._body() if mutation else None)
            return self._json(status, value)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as exc:
            self.close_connection = True
            known = (
                (RequestConflict, 409, 'request_key_conflict', 'Request key already belongs to another operation.'),
                (LifecycleConflict, 409, 'lifecycle_revision_conflict', 'Workspace membership changed.'),
                (WorkspaceArchived, 409, 'workspace_archived', 'Restore this workspace first.'),
                (AnalysisMembershipLocked, 409, 'analysis_membership_locked', 'P1 membership is frozen.'),
                (ConfigConflict, 409, 'config_revision_conflict', 'Configuration changed.'),
                (ModelChangeRequired, 409, 'model_change_confirmation_required', 'Confirm model changes.'),
                (ReviewConflict, 409, 'review_revision_conflict', 'Review state changed.'),
                (MarkerConflict, 409, 'marker_revision_conflict', 'Marker state or translated text changed.'),
                (DestinationConflict, 409, 'destination_exists', 'Import destination already exists.'),
                (ImportDisabled, 403, 'import_disabled', 'Web import is disabled.'),
                ((JobConflict, LockConflict), 409, 'workspace_busy', 'Workspace busy or server stopping.'),
                (PermissionError, 403, 'origin_rejected', 'Same-origin access required.'),
                ((KeyError, FileNotFoundError), 404, 'not_found', 'Not found.'),
                (OverflowError, 413, 'body_too_large', 'JSON body too large.'),
                ((ValueError, TypeError), 400, 'invalid_request', 'Invalid request.'),
                (PipelineError, 422, 'invalid_project_state', 'Project operation unavailable; inspect locally.'),
            )
            for cls, status, code, message in known:
                if isinstance(exc, cls):
                    return self._error(status, code, message)
            self._error(500, 'internal_error', 'Internal server error.')

    def _error(self, status, code, message):
        self._json(status, {'error': {'code': code, 'message': message, 'details': {}}})

    def _events(self, query):
        supervisor = self.server.service.supervisor
        if set(query) - {"workspace_id", "job_id", "after"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("Invalid event filters.")
        filters = {"workspace_root": supervisor.workspace_root}
        for key in ("workspace_id", "job_id"):
            if key in query:
                filters[key] = query[key][0]
        if "workspace_id" in filters:
            try:
                self.server.service.workspaces.resolve(filters['workspace_id'])
            except KeyError:
                # Imports have an identity before book.json marks discovery.
                workspace_destination(self.server.service.workspaces.root, filters['workspace_id'])
        if "job_id" in filters:
            supervisor.get(filters["job_id"])
        snapshot = supervisor.snapshot(workspace_id=filters.get("workspace_id"), job_id=filters.get("job_id"))
        cursor = int(self.headers.get("Last-Event-ID", query.get("after", [str(snapshot["cursor"])])[0]))
        if cursor < 0 or cursor > snapshot["cursor"]:
            cursor = snapshot["cursor"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        # Always send current state, including when old history has been pruned.
        # No id on snapshot: reconnect replay must retain the requested cursor.
        self.wfile.write(("event: snapshot\ndata: " + json.dumps(safe_json(snapshot)) + "\n\n").encode())
        self.wfile.flush()
        while not supervisor.broker.closed:
            events = supervisor.broker.read(cursor, **filters)
            for event in events:
                self.wfile.write((f"id: {event['id']}\nevent: progress\ndata: " + json.dumps(safe_json(event)) + "\n\n").encode())
                cursor = event['id']
            if not events:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
