#!/usr/bin/env python3
"""Bounded read-only API smoke; optional guarded browser smoke against a test fixture.

Does not start a server, create jobs, run models, prepare review, or change a project.
Browser mode requires explicit fixture acknowledgement and a visible readiness selector.
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

MAX_BODY = 16 * 1024 * 1024
API_ROUTES = ("/api/health", "/api/capabilities", "/api/workspaces", "/api/jobs", "/api/profiles")


def validate_base_url(value: str, *, allow_non_loopback: bool = False) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Use an HTTP(S) origin with no credentials, path, query, or fragment.")
    port = parsed.port  # Validate malformed ports early.
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid port.")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.casefold() == "localhost"
    if not loopback and not allow_non_loopback:
        raise ValueError("Non-loopback origins require explicit --allow-non-loopback.")
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_ui_path(path: str) -> str:
    parsed = urlsplit(path)
    if not path.startswith("/") or path.startswith("//") or parsed.netloc or parsed.scheme or "\\" in path:
        raise ValueError("UI path must be a same-origin absolute URL path, not a new origin.")
    return path


def origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    return (parsed.scheme, (parsed.hostname or "").casefold(), parsed.port or (443 if parsed.scheme == "https" else 80))


def allowed_browser_request(method: str, value: str, base: str) -> bool:
    # Block external requests and all writes, even if a broken page tries a mutation on load.
    try:
        return method in {"GET", "HEAD"} and origin(value) == origin(base)
    except ValueError:
        return False


def read_json(base: str, path: str, timeout: float) -> dict:
    parsed = urlsplit(base)
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = cls(parsed.hostname, port=parsed.port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        if response.status != 200:
            raise ValueError(f"{path}: expected HTTP 200, got {response.status}; redirects are not followed.")
        if response.getheader("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError(f"{path}: expected application/json.")
        if len(raw) > MAX_BODY:
            raise ValueError(f"{path}: JSON response exceeds smoke-test body limit.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected a JSON object.")
        return value
    finally:
        connection.close()


def validate_payload(path: str, data: dict) -> None:
    if path == "/api/health":
        if data.get("status") != "ok":
            raise ValueError("Health response is not ok.")
    elif path == "/api/capabilities":
        for key in ("import_enabled", "review", "reader", "sse", "multi_workspace"):
            if type(data.get(key)) is not bool:
                raise ValueError(f"Capabilities is missing boolean {key}.")
    elif path == "/api/workspaces":
        if not isinstance(data.get("workspaces"), list):
            raise ValueError("Workspace listing is not an array.")
    elif path == "/api/jobs":
        if not isinstance(data.get("jobs"), list) or type(data.get("cursor")) is not int or data["cursor"] < 0:
            raise ValueError("Invalid job snapshot/cursor.")
    elif path == "/api/profiles":
        if not isinstance(data.get("profiles"), list) or not isinstance(data.get("resolved_passes"), dict):
            raise ValueError("Invalid profile listing.")


def api_smoke(base: str, timeout: float) -> list[dict]:
    checks = []
    for path in API_ROUTES:
        try:
            validate_payload(path, read_json(base, path, timeout))
            checks.append({"path": path, "status": "passed"})
        except (OSError, ValueError, http.client.HTTPException) as exc:
            # Do not include the response body: it may contain project data.
            checks.append({"path": path, "status": "failed", "error": str(exc)})
    return checks


def browser_smoke(base: str, path: str, selector: str, timeout: float,
                  executable: str | None, output_dir: Path | None) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Browser check requires Playwright; it was NOT skipped as success.") from exc
    findings: dict[str, list] = {"console_errors": [], "page_errors": [], "http_errors": [],
                                 "request_failures": [], "blocked_requests": []}
    with sync_playwright() as playwright:
        launch = {"headless": True}
        if executable:
            launch["executable_path"] = executable
        browser = playwright.chromium.launch(**launch)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                          accept_downloads=False, service_workers="block")
            def guard(route):
                request = route.request
                if not allowed_browser_request(request.method, request.url, base):
                    findings["blocked_requests"].append({"method": request.method, "url": request.url})
                    route.abort()
                else:
                    route.continue_()
            context.route("**/*", guard)
            page = context.new_page()
            page.set_default_timeout(timeout * 1000)
            page.on("console", lambda message: findings["console_errors"].append(message.text)
                    if message.type == "error" else None)
            page.on("pageerror", lambda error: findings["page_errors"].append(str(error)))
            page.on("response", lambda response: findings["http_errors"].append({"status": response.status, "url": response.url})
                    if response.status >= 400 else None)
            page.on("requestfailed", lambda request: findings["request_failures"].append({"url": request.url, "failure": request.failure}))
            navigation_error = None
            try:
                response = page.goto(base + path, wait_until="domcontentloaded", timeout=timeout * 1000)
                if response is None or response.status != 200:
                    raise RuntimeError("UI navigation did not return HTTP 200.")
                if origin(page.url) != origin(base):
                    raise RuntimeError("UI navigation escaped the requested origin.")
                page.locator(selector).first.wait_for(state="visible")
                page.wait_for_timeout(500)
                if output_dir:
                    page.screenshot(path=str(output_dir / "browser.png"), full_page=True)
            except Exception as exc:
                navigation_error = str(exc)
            result = {"status": "passed" if not navigation_error and not any(findings.values()) else "failed",
                      "ui_path": path, "ready_selector": selector, **findings}
            if navigation_error:
                result["navigation_error"] = navigation_error
            context.close()
            return result
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allow-non-loopback", action="store_true")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--ui-path")
    parser.add_argument("--ready-selector")
    parser.add_argument("--fixture", action="store_true", help="Confirm the browser target is an isolated test fixture.")
    parser.add_argument("--browser-executable", help="Optional installed Chromium path; otherwise Playwright default.")
    parser.add_argument("--output-dir", type=Path, help="New local directory for JSON report and optional screenshot.")
    args = parser.parse_args(argv)
    output_owned = False
    try:
        base = validate_base_url(args.base_url, allow_non_loopback=args.allow_non_loopback)
        if not 0 < args.timeout <= 120:
            raise ValueError("Timeout must be > 0 and <= 120 seconds.")
        if args.ui_path:
            validate_ui_path(args.ui_path)
            if not args.ready_selector or not args.fixture:
                raise ValueError("Browser mode requires --ready-selector and explicit --fixture acknowledgement.")
        elif args.ready_selector or args.browser_executable:
            raise ValueError("Browser options require --ui-path.")
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            output_owned = True
        checks = api_smoke(base, args.timeout)
        browser = {"status": "not_requested"}
        if args.ui_path:
            browser = browser_smoke(base, args.ui_path, args.ready_selector, args.timeout,
                                    args.browser_executable, args.output_dir)
        passed = all(check["status"] == "passed" for check in checks) and browser["status"] in {"passed", "not_requested"}
        report = {"scope": "api_and_guarded_browser_smoke" if args.ui_path else "api_only",
                  "status": "passed" if passed else "failed", "api_checks": checks, "browser": browser,
                  "limitations": "Read-only smoke only. Does not validate mutations, SSE replay, visual parity, or a complete workflow."}
        code = 0 if passed else 1
    except Exception as exc:
        report = {"scope": "smoke_setup_or_execution", "status": "error", "error": str(exc)}
        code = 2
    encoded = json.dumps(report, indent=2)
    if output_owned:
        (args.output_dir / "report.json").write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return code


if __name__ == "__main__":
    sys.exit(main())
