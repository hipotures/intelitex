"""Small local translated-book reader and prose-location marker service."""
from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .application.reader import (
    MarkerConflict as ApplicationMarkerConflict,
    MarkerRepository as ApplicationMarkerRepository,
)
from .util import PipelineError


# Compatibility exports use the application implementation so direct callers
# and HTTP requests share the exact marker policy.
MarkerConflict = ApplicationMarkerConflict
MarkerRepository = ApplicationMarkerRepository


class _RepositoryReaderSession:
    """Compatibility adapter for callers that still pass a marker repository."""

    def __init__(self, repository: MarkerRepository):
        self._repository = repository
        self.path = repository.path

    def load(self):
        return self._repository.load()

    def metadata(self):
        return self._repository.context.metadata()

    def progress(self):
        return self._repository.context.progress()

    def chapter(self, chapter_id):
        return self._repository.context.chapter(chapter_id)

    def context(self, chapter_id, block_id, position):
        return self._repository.context.context(chapter_id, block_id, position)

    def create_marker(self, payload, expected_revision):
        return self._repository.create(payload, expected_revision)

    def delete_marker(self, marker_id, expected_revision):
        return self._repository.delete(marker_id, expected_revision)


def _reader_session(value: Path | object):
    required = (
        "load", "metadata", "progress", "chapter", "context",
        "create_marker", "delete_marker", "path",
    )
    if all(hasattr(value, name) for name in required):
        return value
    repository = value if isinstance(value, MarkerRepository) else MarkerRepository(value)
    return _RepositoryReaderSession(repository)


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="light dark">
  <title>Intelitex Reader</title>
  <link rel="stylesheet" href="/assets/reader.css">
  <script src="/assets/reader.js" defer></script>
</head>
<body>
  <header class="reader-bar">
    <button id="tocButton" class="icon-button" type="button" aria-label="Open table of contents">☰</button>
    <button id="previousButton" class="icon-button" type="button" aria-label="Previous chapter">‹</button>
    <div class="book-heading"><strong id="bookTitle">Intelitex Reader</strong><span id="chapterTitle"></span></div>
    <button id="nextButton" class="icon-button" type="button" aria-label="Next chapter">›</button>
    <button id="settingsButton" class="icon-button" type="button" aria-label="Reader settings">Aa</button>
    <button id="fullscreenButton" class="icon-button" type="button" aria-label="Toggle fullscreen">⛶</button>
  </header>
  <button id="readingProgress" class="reading-progress" type="button" aria-label="Show reading progress" aria-controls="progressPopup" aria-expanded="false"><span id="readingProgressFill"></span></button>
  <div id="progressPopup" class="progress-popup" role="status" hidden>
    <strong id="progressPercent"></strong>
    <span id="progressCounts"></span>
    <span id="progressAvailable"></span>
  </div>
  <aside id="tocPanel" class="panel toc-panel" hidden aria-label="Table of contents">
    <h2>Contents</h2><nav id="toc"></nav>
  </aside>
  <aside id="settingsPanel" class="panel settings-panel" hidden aria-label="Reader settings">
    <label>Font size <input id="fontSize" type="range" min="15" max="30" step="1"></label>
    <label>Font family <select id="fontFamily"><option value="serif">Serif</option><option value="sans">Sans serif</option><option value="mono">Monospace</option></select></label>
    <label>Line height <input id="lineHeight" type="range" min="1.35" max="2.1" step="0.05"></label>
    <label>Content width <input id="contentWidth" type="range" min="32" max="54" step="1"></label>
    <label>Theme <select id="theme"><option value="light">Light</option><option value="sepia">Sepia</option><option value="dark">Dark</option></select></label>
    <label>Header auto-hide <select id="headerAutoHide"><option value="0">Off</option><option value="5">5 seconds</option><option value="10">10 seconds</option><option value="15">15 seconds</option></select></label>
    <label>Marker gesture <select id="markerGesture"><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag / swipe</option><option value="off">Off</option></select></label>
    <label>Context Helper <select id="contextGesture"><option value="tap">Tap / click</option><option value="long">Long press</option><option value="drag">Horizontal drag / swipe</option><option value="off">Off</option></select></label>
  </aside>
  <main id="reader" tabindex="-1"><article id="chapter" lang="pl" aria-live="polite"></article></main>
  <div id="markerControl" class="marker-control" hidden><button id="deleteMarker" type="button">Delete marker</button></div>
  <div id="contextOverlay" class="context-overlay" hidden>
    <section id="contextCard" class="context-card" role="dialog" aria-modal="true" aria-labelledby="contextTitle">
      <button id="contextClose" class="context-close" type="button" aria-label="Close context">×</button>
      <div class="context-kicker">Known so far</div>
      <h2 id="contextTitle"></h2>
      <div id="contextBody"></div>
    </section>
  </div>
  <div id="notice" class="notice" role="status" aria-live="polite"></div>
</body>
</html>
"""


class ReaderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, session):
        self.session = _reader_session(session)
        super().__init__(address, ReaderHandler)


class ReaderHandler(BaseHTTPRequestHandler):
    server: ReaderServer
    MAX_BODY = 64 * 1024

    def log_message(self, fmt, *args):
        return

    def _send(self, raw: bytes, content_type: str, status: int = HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload: dict, status: int = HTTPStatus.OK):
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _error(self, exc: Exception, status: int = HTTPStatus.BAD_REQUEST):
        if isinstance(exc, MarkerConflict):
            status = HTTPStatus.CONFLICT
        self._json({"error": str(exc)}, status)

    def _body(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise PipelineError("JSON requests require application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise PipelineError("Invalid Content-Length.") from exc
        if length < 0 or length > self.MAX_BODY:
            raise PipelineError("Request body is too large.")
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise PipelineError("Cross-origin mutation request rejected.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f"Invalid JSON request: {exc}") from exc
        if not isinstance(body, dict):
            raise PipelineError("JSON request body must be an object.")
        return body

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                raw = HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path in {"/assets/reader.js", "/assets/reader.css"}:
                name = path.rsplit("/", 1)[-1]
                raw = Path(__file__).with_name(name).read_bytes()
                kind = "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
                self._send(raw, kind)
            elif path == "/api/reader":
                self._json({
                    **self.server.session.metadata(),
                    "progress": self.server.session.progress(),
                    "marker_state": self.server.session.load(),
                })
            elif path.startswith("/api/chapters/"):
                chapter_id = unquote(path[len("/api/chapters/"):])
                if not chapter_id or "/" in chapter_id or chapter_id in {".", ".."}:
                    raise PipelineError("Invalid chapter path.")
                self._json(self.server.session.chapter(chapter_id))
            else:
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
        except (PipelineError, OSError, ValueError) as exc:
            self._error(exc)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/context":
                body = self._body()
                expected = {"chapter_id", "block_id", "position"}
                if set(body) != expected:
                    raise PipelineError("Context request must contain only chapter_id, block_id, and position.")
                self._json(self.server.session.context(
                    body["chapter_id"], body["block_id"], body["position"]
                ))
                return
            if path != "/api/markers":
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
                return
            body = self._body()
            revision = body.pop("revision", None)
            result = self.server.session.create_marker(body, revision)
            self._json(result, HTTPStatus.CREATED if result["created"] else HTTPStatus.OK)
        except (PipelineError, OSError, ValueError) as exc:
            self._error(exc)

    def do_DELETE(self):
        try:
            path = urlparse(self.path).path
            prefix = "/api/markers/"
            if not path.startswith(prefix) or "/" in path[len(prefix):]:
                self._error(PipelineError("Not found."), HTTPStatus.NOT_FOUND)
                return
            body = self._body()
            if set(body) != {"revision"}:
                raise PipelineError("Delete request must contain only revision.")
            self._json(self.server.session.delete_marker(unquote(path[len(prefix):]), body["revision"]))
        except (PipelineError, OSError, ValueError) as exc:
            self._error(exc)


def run_reader_server(root: Path | object, bind: str, port: int, open_browser: bool, ui) -> None:
    if not 0 <= port <= 65535:
        raise PipelineError("Reader port must be between 0 and 65535.")
    session = _reader_session(root)
    session.load()
    try:
        server = ReaderServer((bind, port), session)
    except OSError as exc:
        raise PipelineError(f"Cannot start Reader server on {bind}:{port}: {exc}") from exc
    actual_port = server.server_address[1]
    display_host = "127.0.0.1" if bind in {"0.0.0.0", "::"} else bind
    url = f"http://{display_host}:{actual_port}/"
    ui.message(
        f"Translation Reader: {url}\nMarker state: {session.path}\n"
        "Press Ctrl-C to stop. Marker changes are saved atomically."
    )
    if bind not in {"127.0.0.1", "localhost", "::1"}:
        ui.message(
            "WARNING: Reader is listening beyond loopback. It is an unauthenticated local editing service "
            "reachable by hosts allowed by this machine/network firewall."
        )
    if open_browser:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
