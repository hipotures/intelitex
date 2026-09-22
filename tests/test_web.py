"""HTTP scaffold smoke test and framework dependency boundary guard."""
import ast
from pathlib import Path

from fastapi.testclient import TestClient

from bookpipe.web import create_app


def test_web_factory_is_inert_and_serves_only_liveness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "access-control-allow-origin" not in response.headers
        assert client.get("/api/workspaces").status_code == 404
        assert client.get("/api/events").status_code == 404
        assert client.post("/api/health").status_code == 405
    assert list(tmp_path.iterdir()) == []


def test_web_frameworks_stay_at_delivery_edge():
    root = Path(__file__).parents[1] / "bookpipe"
    paths = [root / "engine.py"]
    for directory in ("application", "domain", "infrastructure", "runtime"):
        paths.extend((root / directory).rglob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name.split(".")[0] in {"fastapi", "starlette", "uvicorn"}
                           or name == "bookpipe.web" for name in names), path
