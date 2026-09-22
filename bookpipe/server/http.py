"""Small JSON/SSE HTTP adapter. Handlers only invoke control/query services."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from ..runtime.supervisor import JobConflict
from ..util import PipelineError

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
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
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
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("JSON object required.")
        return value

    def do_GET(self):
        self._handle(False)

    def do_POST(self):
        self._handle(True)

    def _handle(self, mutation):
        try:
            self._guard()
            parsed = urlsplit(self.path)
            parts = [unquote(part) for part in parsed.path.split('/')[1:]]
            service = self.server.service
            supervisor = service.supervisor
            if mutation:
                body = self._body()
                if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "jobs":
                    return self._json(202, service.start(parts[2], body))
                if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stop":
                    if body:
                        raise ValueError("Stop expects an empty object.")
                    return self._json(202, supervisor.stop(parts[2]).public())
            else:
                if parts == ["api", "health"]:
                    return self._json(200, {"status": "ok"})
                if parts == ["api", "workspaces"]:
                    return self._json(200, {"workspaces": service.list_workspaces()})
                if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
                    return self._json(200, service.workspace(parts[2]))
                if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "usage":
                    return self._json(200, service.usage(parts[2]))
                if parts == ["api", "jobs"]:
                    return self._json(200, supervisor.snapshot())
                if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                    return self._json(200, supervisor.get(parts[2]).public())
                if parts == ["api", "events"]:
                    return self._events(parse_qs(parsed.query))
            self._json(404, {"error": "Route not found."})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as exc:
            self.close_connection = True
            status = (403 if isinstance(exc, PermissionError) else
                      409 if isinstance(exc, JobConflict) else
                      404 if isinstance(exc, KeyError) else
                      413 if isinstance(exc, OverflowError) else
                      400 if isinstance(exc, (ValueError, TypeError)) else
                      422 if isinstance(exc, PipelineError) else 500)
            # Do not expose paths, provider errors or user configuration values.
            self._json(status, {"error": {400: "Invalid request.", 403: "Same-origin access required.",
                       404: "Not found.", 409: "Workspace busy or server stopping.",
                       413: "JSON body too large.", 422: "Project state unavailable; inspect locally.",
                       500: "Internal server error."}[status]})

    def _events(self, query):
        supervisor = self.server.service.supervisor
        if set(query) - {"workspace_id", "job_id", "after"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("Invalid event filters.")
        filters = {"workspace_root": supervisor.workspace_root}
        for key in ("workspace_id", "job_id"):
            if key in query:
                filters[key] = query[key][0]
        if "workspace_id" in filters:
            self.server.service.workspaces.resolve(filters["workspace_id"])
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
        self.wfile.write(("event: snapshot\ndata: " + json.dumps(snapshot) + "\n\n").encode())
        self.wfile.flush()
        while not supervisor.broker.closed:
            events = supervisor.broker.read(cursor, **filters)
            for event in events:
                self.wfile.write((f"id: {event['id']}\nevent: progress\ndata: " + json.dumps(event) + "\n\n").encode())
                cursor = event['id']
            if not events:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
