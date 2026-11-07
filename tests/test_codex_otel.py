import os
import shutil
import subprocess
import tomllib

import pytest

from bookpipe.codex_otel import install_otel
from bookpipe.util import PipelineError


def test_only_telemetry_is_inherited_and_refreshed(tmp_path):
    source = tmp_path / 'user.toml'
    source.write_text('model="unwanted"\n[mcp_servers.secret]\ncommand="unwanted"\n'
                      '[otel]\nlog_user_prompt=true\nlog_agent_responses=true\n'
                      'exporter={otlp-http={endpoint="http://localhost:4318/v1/logs", protocol="json", headers={token="private"}}}\n')
    home = tmp_path / 'private'
    install_otel(home, {'otel_config': str(source)})
    config = home / 'config.toml'
    actual = tomllib.loads(config.read_text())
    assert set(actual) == {'otel'}
    assert actual['otel']['log_user_prompt'] is False
    assert actual['otel']['log_agent_responses'] is False
    assert actual['otel']['exporter']['otlp-http']['headers'] == {'token': 'private'}
    assert config.stat().st_mode & 0o777 == 0o600
    source.write_text('[otel]\nexporter="none"\n')
    install_otel(home, {'otel_config': str(source)})
    assert tomllib.loads(config.read_text())['otel']['exporter'] == 'none'
    install_otel(home, {'otel_config': None})
    assert not config.exists()


def test_missing_config_and_symlink(tmp_path):
    home = tmp_path / 'home'
    install_otel(home, {'otel_config': str(tmp_path / 'absent')})
    assert 'exporter' not in tomllib.loads((home / 'config.toml').read_text())['otel']
    (home / 'config.toml').unlink()
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    (home / 'config.toml').symlink_to(outside)
    with pytest.raises(PipelineError, match='symbolic link'):
        install_otel(home, {'otel_config': str(tmp_path / 'absent')})
    assert outside.read_text() == 'unchanged'


def test_installed_codex_accepts_telemetry_config(tmp_path):
    executable = shutil.which('codex')
    if not executable:
        pytest.skip('Codex not installed')
    source = tmp_path / 'source.toml'
    source.write_text('[otel]\nexporter={otlp-http={endpoint="http://127.0.0.1:1/v1/logs", protocol="json"}}\n'
                      'metrics_exporter={otlp-http={endpoint="http://127.0.0.1:1/v1/metrics", protocol="json", headers={"X-Homestack-Upstream"="http://collector:4318/v1/metrics"}}}\n')
    home = tmp_path / 'home'
    install_otel(home, {'otel_config': str(source)})
    result = subprocess.run([executable, 'features', 'list'], env={**os.environ, 'CODEX_HOME': str(home)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_fresh_runtime_installs_telemetry(tmp_path):
    from bookpipe.codex_transport import CodexAppServerClient
    source = tmp_path / 'user.toml'
    source.write_text('[otel]\nexporter="none"\n')
    client = object.__new__(CodexAppServerClient)
    client.settings = {'runtime_root': str(tmp_path / 'runtime'), 'options': {'otel_config': str(source)}}
    home, _, _ = client._runtime(tmp_path / 'attempt')
    assert tomllib.loads((home / 'config.toml').read_text())['otel']['exporter'] == 'none'


def test_telemetry_changes_do_not_invalidate_saved_work():
    from bookpipe.engine import _semantic_execution_signature
    original = {'resolved_profile': {'provider': 'codex', 'options': {'auth_source': '/auth'}}}
    updated = {'resolved_profile': {'provider': 'codex', 'options': {'auth_source': '/auth', 'otel_config': '/telemetry'}}}
    assert _semantic_execution_signature(original) == _semantic_execution_signature(updated)


def test_book_title_header_is_local_ascii_and_bounded(tmp_path):
    import json
    import base64
    from bookpipe.codex_otel import book_title
    title = 'Zażółć gęślą jaźń ' * 30
    (tmp_path / 'book.json').write_text(json.dumps({'metadata': {'title': title}}))
    source = tmp_path / 'source.toml'
    source.write_text('[otel]\nexporter={otlp-http={endpoint="http://localhost/v1/logs"}}\n'
                      'metrics_exporter={otlp-http={endpoint="http://localhost/v1/metrics",headers={existing="keep"}}}\n')
    home = tmp_path / 'private'
    before = source.read_bytes()
    install_otel(home, {'otel_config': str(source)}, title=book_title(tmp_path))
    otel = tomllib.loads((home / 'config.toml').read_text())['otel']
    for name in ('exporter', 'metrics_exporter'):
        header = otel[name]['otlp-http']['headers']['X-Intelitex-Project']
        assert header.isascii()
        assert base64.urlsafe_b64decode(header.removeprefix('utf8-base64:')).decode('utf-8') == ' '.join(title.split())[:250]
    assert otel['metrics_exporter']['otlp-http']['headers']['existing'] == 'keep'
    assert source.read_bytes() == before
    install_otel(home, {'otel_config': str(source)})
    assert 'X-Intelitex-Project' not in tomllib.loads((home / 'config.toml').read_text())['otel']['exporter']['otlp-http']['headers']


def test_relay_patch_keeps_unmarked_projects_and_strips_header(tmp_path):
    import json
    import runpy
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import base64
    from urllib.request import Request, urlopen

    original = Path('/etc/rc.local')
    if not original.exists():
        pytest.skip('Host relay script is not installed')
    patched = tmp_path / 'relay.py'
    shutil.copyfile(original, patched)
    patch = Path(__file__).resolve().parents[1] / 'tools/telemetry/intelitex-project.patch'
    if 'X-Intelitex-Project' not in patched.read_text():
        subprocess.run(['patch', str(patched), str(patch)], check=True, capture_output=True)
    relay_module = runpy.run_path(str(patched))
    received = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((json.loads(self.rfile.read(int(self.headers['Content-Length']))), dict(self.headers)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

    collector = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
    upstream = f'http://127.0.0.1:{collector.server_port}/'
    relay = relay_module['telemetry_relay'](tmp_path, 0, {'/codex/v1/logs': upstream, '/codex/v1/metrics': upstream})
    workers = [threading.Thread(target=s.serve_forever, daemon=True) for s in (collector, relay)]
    for worker in workers:
        worker.start()
    try:
        for signal in ('logs', 'metrics'):
            group_key, scope_key = ('resourceLogs', 'scopeLogs') if signal == 'logs' else ('resourceMetrics', 'scopeMetrics')
            scope = {'logRecords': [{}]} if signal == 'logs' else {'metrics': [{'name': 'test', 'sum': {'dataPoints': [{}]}}]}
            payload = {group_key: [{scope_key: [scope]}]}
            for title in ('Książka', None):
                headers = {'Content-Type': 'application/json'}
                if title:
                    headers['X-Intelitex-Project'] = 'utf8-base64:' + base64.urlsafe_b64encode(title.encode()).decode()
                with urlopen(Request(f'http://127.0.0.1:{relay.server_port}/codex/v1/{signal}',
                                     data=json.dumps(payload).encode(), headers=headers), timeout=5) as response:
                    assert response.status == 200
                body, forwarded = received[-1]
                attrs = body[group_key][0]['resource']['attributes']
                project = next(a['value']['stringValue'] for a in attrs if a['key'] == 'project')
                assert project == ('intelitex/' + title if title else __import__('socket').gethostname())
                assert not any(k.lower() == 'x-intelitex-project' for k in forwarded)
    finally:
        for server in (relay, collector):
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(timeout=2)
