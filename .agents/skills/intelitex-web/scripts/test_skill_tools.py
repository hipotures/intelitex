"""Offline regression tests for the skill helpers, not the Intelitex application."""
from __future__ import annotations

from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load('verify-mockup')
check = load('check-api-contract')
smoke = load('web-smoke-test')


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(SKILL / 'assets', self.root / 'assets')
        (self.root / 'references').mkdir()
        shutil.copyfile(SKILL / 'references/implementation-contract.md', self.root / 'references/implementation-contract.md')

    def tearDown(self):
        self.temp.cleanup()

    def test_original_reference_and_contract_match(self):
        self.assertEqual(verify.verify(self.root)['status'], 'passed')

    def test_mockup_content_change_fails(self):
        path = self.root / 'assets/intelitex_workspace_mockup_v33.html'
        path.write_bytes(path.read_bytes() + b'\n')
        self.assertEqual(verify.verify(self.root)['status'], 'failed')

    def test_contract_change_fails(self):
        (self.root / 'references/implementation-contract.md').write_text('Changed')
        self.assertEqual(verify.verify(self.root)['status'], 'failed')

    def test_manifest_cannot_silently_repin(self):
        path = self.root / 'assets/reference-manifest.json'
        manifest = json.loads(path.read_text())
        manifest['reference']['sha256'] = '0' * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            verify.verify(self.root)

    def test_missing_asset_is_error(self):
        (self.root / 'assets/intelitex_workspace_mockup_v33.html').unlink()
        with self.assertRaises(OSError):
            verify.verify(self.root)

    def test_asset_traversal_rejected(self):
        with self.assertRaises(ValueError):
            verify.contained_file(self.root, '../secret')

    def test_absolute_asset_rejected(self):
        with self.assertRaises(ValueError):
            verify.contained_file(self.root, '/tmp/secret')

    def test_escaping_symlink_rejected(self):
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            verify.contained_file(self.root, 'escape/secret')


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'api.py').write_bytes(b'print("fixture")\n')
        self.baseline = self.root / 'baseline.json'
        self.data = {'format_version': 1, 'inspected_commit': 'fixture-only',
                     'source_blobs': {'api.py': check.git_blob_sha((self.root / 'api.py').read_bytes())}}
        self.baseline.write_text(json.dumps(self.data))

    def tearDown(self):
        self.temp.cleanup()

    def test_matched_source(self):
        self.assertEqual(check.inspect(self.root, self.baseline)['status'], 'baseline_matched')

    def test_changed_source_needs_reaudit(self):
        (self.root / 'api.py').write_bytes(b'changed')
        self.assertEqual(check.inspect(self.root, self.baseline)['status'], 'needs_reaudit')

    def test_missing_source_needs_reaudit(self):
        (self.root / 'api.py').unlink()
        self.assertEqual(check.inspect(self.root, self.baseline)['files'][0]['status'], 'missing')

    def test_baseline_traversal_rejected(self):
        self.data['source_blobs'] = {'../outside': '0' * 40}
        self.baseline.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            check.inspect(self.root, self.baseline)

    def test_invalid_baseline_rejected(self):
        self.baseline.write_text('{}')
        with self.assertRaises(ValueError):
            check.inspect(self.root, self.baseline)

    def test_blob_hash_matches_git(self):
        if not shutil.which('git'):
            self.skipTest('Git binary unavailable for independent blob calculation')
        raw = b'Verifiable\n'
        expected = subprocess.run(['git', 'hash-object', '--stdin'], input=raw,
                                  capture_output=True, check=True, timeout=5).stdout.decode().strip()
        self.assertEqual(check.git_blob_sha(raw), expected)


class InputAndGuardTests(unittest.TestCase):
    def test_valid_loopback_origins(self):
        for value in ('http://127.0.0.1:8780', 'http://localhost:9000/', 'http://[::1]:8780'):
            with self.subTest(value=value):
                self.assertTrue(smoke.validate_base_url(value).startswith('http'))

    def test_unsafe_origins_rejected(self):
        for value in ('file:///tmp/source', 'http://user:pass@localhost', 'http://example.com',
                      'http://localhost/path', 'http://localhost?x=1', 'http://localhost#x',
                      'http://localhost:bad', 'http://localhost:0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.validate_base_url(value)

    def test_explicit_non_loopback(self):
        self.assertEqual(smoke.validate_base_url('https://example.test', allow_non_loopback=True), 'https://example.test')

    def test_ui_path_is_same_origin(self):
        self.assertEqual(smoke.validate_ui_path('/work?view=all'), '/work?view=all')
        for value in ('//example.test', 'https://example.test/work', 'work', '/\\example.test'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.validate_ui_path(value)

    def test_request_guard_blocks_writes_and_external_requests(self):
        base = 'http://127.0.0.1:8780'
        self.assertTrue(smoke.allowed_browser_request('GET', base + '/work', base))
        self.assertTrue(smoke.allowed_browser_request('HEAD', base + '/asset.js', base))
        for method in ('POST', 'PATCH', 'DELETE', 'PUT'):
            self.assertFalse(smoke.allowed_browser_request(method, base + '/api/imports', base))
        self.assertFalse(smoke.allowed_browser_request('GET', 'http://example.test', base))
        self.assertFalse(smoke.allowed_browser_request('GET', 'http://localhost:bad', base))

    def test_browser_requires_fixture_and_selector(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(['--base-url', 'http://localhost:8780', '--ui-path', '/work']), 2)

    def test_existing_output_directory_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory)
            (path / 'sentinel').write_text('keep')
            self.assertEqual(smoke.main(['--base-url', 'http://localhost:8780', '--output-dir', directory]), 2)
            self.assertEqual(sorted(p.name for p in path.iterdir()), ['sentinel'])

    def test_boolean_cursor_is_not_integer(self):
        with self.assertRaises(ValueError):
            smoke.validate_payload('/api/jobs', {'jobs': [], 'cursor': True})

    def test_capability_type_and_profile_shape_checked(self):
        with self.assertRaises(ValueError):
            smoke.validate_payload('/api/capabilities', {'review': 'true'})
        with self.assertRaises(ValueError):
            smoke.validate_payload('/api/profiles', {'profiles': {}, 'resolved_passes': {}})


class HTTPFixtureTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.override = {}
        tests = self
        payloads = {
            '/api/health': {'status': 'ok'},
            '/api/capabilities': {key: False if key == 'import_enabled' else True for key in
                                  ('import_enabled', 'review', 'reader', 'sse', 'multi_workspace')},
            '/api/workspaces': {'workspaces': []}, '/api/jobs': {'jobs': [], 'cursor': 0},
            '/api/profiles': {'profiles': [], 'resolved_passes': {}},
        }
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                tests.requests.append((self.command, self.path))
                status, content_type, raw = tests.override.get(self.path, (
                    200, 'application/json', json.dumps(payloads.get(self.path, {})).encode()))
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_smoke_only_reads_verified_query_families(self):
        result = smoke.api_smoke(self.base, 3)
        self.assertTrue(all(item['status'] == 'passed' for item in result))
        self.assertEqual(self.requests, [('GET', route) for route in smoke.API_ROUTES])

    def test_http_failure_is_failure_without_body_leak(self):
        self.override['/api/jobs'] = (500, 'application/json', b'{"secret":"private fixture text"}')
        result = smoke.api_smoke(self.base, 3)
        self.assertEqual(result[3]['status'], 'failed')
        self.assertNotIn('private fixture text', json.dumps(result))

    def test_html_instead_of_json_fails(self):
        self.override['/api/health'] = (200, 'text/html', b'<html>Not the API</html>')
        self.assertEqual(smoke.api_smoke(self.base, 3)[0]['status'], 'failed')

    def test_redirect_not_followed(self):
        self.override['/api/health'] = (302, 'application/json', b'{}')
        self.assertEqual(smoke.api_smoke(self.base, 3)[0]['status'], 'failed')

    def test_malformed_json_fails(self):
        self.override['/api/jobs'] = (200, 'application/json', b'{invalid')
        self.assertEqual(smoke.api_smoke(self.base, 3)[3]['status'], 'failed')

    def test_api_only_report_is_not_browser_success(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = smoke.main(['--base-url', self.base])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report['scope'], 'api_only')
        self.assertEqual(report['browser']['status'], 'not_requested')


class StructureTests(unittest.TestCase):
    def test_skill_frontmatter_and_readable_references(self):
        text = (SKILL / 'SKILL.md').read_text()
        self.assertTrue(text.startswith('---\nname: intelitex-web\ndescription: '))
        self.assertLess(len(text.splitlines()), 200)
        for label, relative in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', text):
            if not relative.startswith(('http://', 'https://', '#')):
                with self.subTest(label=label):
                    self.assertTrue((SKILL / relative).is_file(), relative)

    def test_no_generated_cache_in_required_manifest(self):
        data = json.loads((SKILL / 'assets/reference-manifest.json').read_text())
        self.assertEqual(data['reference']['path'], 'assets/intelitex_workspace_mockup_v33.html')
        self.assertNotIn('node_modules', json.dumps(data))


if __name__ == '__main__':
    unittest.main(verbosity=2)
