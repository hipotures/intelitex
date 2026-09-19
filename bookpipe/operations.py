from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .catalog import load_catalog
from .codex_transport import _RpcSession, app_server_argv
from .evidence import AttemptRecorder, redact
from .profiles import credential_status, resolve_profile
from .util import PipelineError, digest, read_json


def profile_report(settings: dict[str, Any], project: Path) -> dict[str, Any]:
    resolved = {}
    for pass_no in range(1, 6):
        name, profile, provenance = resolve_profile(settings, pass_no, project=project)
        safe = dict(profile)
        safe["credentials"] = credential_status(profile)
        safe.pop("credential_value", None)
        resolved[str(pass_no)] = {"name": name, "provenance": provenance, "settings": safe}
    return {
        "default_profile": settings["default_profile"],
        "pass_profiles": settings.get("pass_profiles", {}),
        "configured": redact(settings["profiles"]),
        "resolved_passes": resolved,
    }


def find_attempts(project: Path) -> list[Path]:
    return sorted(project.glob("artifacts/**/attempt_*/attempt.json"))


def attempt_report(project: Path, selected: str | None = None) -> dict[str, Any]:
    attempts = find_attempts(project)
    if selected:
        wanted = (project / selected).resolve() if not Path(selected).is_absolute() else Path(selected).resolve()
        manifest = wanted if wanted.name == "attempt.json" else wanted / "attempt.json"
        if not manifest.is_relative_to(project.resolve()) or not manifest.is_file():
            raise PipelineError("Selected attempt is outside this project or has no attempt.json.")
        root = manifest.parent
        result = read_json(manifest)
        result["location"] = str(root.relative_to(project))
        for name in ("request.semantic.json", "request.transport.json", "response_meta.json", "usage.json", "pricing.json", "error.json"):
            if (root / name).is_file():
                result[name] = read_json(root / name)
        result["transport_jsonl"] = str((root / "transport.jsonl").relative_to(project)) if (root / "transport.jsonl").is_file() else None
        return result
    return {"attempts": [{"location": str(path.parent.relative_to(project)), **read_json(path)} for path in attempts]}


def usage_report(project: Path) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(lambda: {
        "attempts": 0, "known_attempts": 0, "missing_attempts": 0,
        "input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
        "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0,
    })
    for manifest_path in find_attempts(project):
        manifest = read_json(manifest_path)
        identity = manifest.get("identity", {})
        key = (str(identity.get("pass", "unknown")), identity.get("provider") or "unknown", identity.get("requested_model") or "unknown")
        group = groups[key]
        group["attempts"] += 1
        usage_path = manifest_path.parent / "usage.json"
        if not usage_path.is_file():
            group["missing_attempts"] += 1
            continue
        usage = read_json(usage_path)
        if usage.get("status") != "reported":
            group["missing_attempts"] += 1
            continue
        group["known_attempts"] += 1
        for field in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"):
            if isinstance(usage.get(field), int):
                group[field] += usage[field]
    rows = [{"pass": key[0], "provider": key[1], "model": key[2], **value} for key, value in sorted(groups.items())]
    return {"scope": "historical physical attempts; accepted checkpoint reuse adds no new row", "groups": rows,
            "warning": "Totals across different models/tokenizers are accounting totals, not comparable text quantities."}


def codex_protocol_doctor(profile: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which(profile.get("executable") or "codex")
    if not executable:
        return {"status": "failed", "error": "Codex executable not found"}
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="intelitex-codex-schema-") as temp:
            subprocess.run([executable, "app-server", "generate-json-schema", "--experimental", "--out", temp],
                           capture_output=True, text=True, timeout=30, check=True)
            required = ["v1/InitializeParams.json", "v2/ThreadStartParams.json", "v2/TurnStartParams.json",
                        "v2/ThreadTokenUsageUpdatedNotification.json", "v2/TurnCompletedNotification.json"]
            hashes = {}
            for name in required:
                path = Path(temp) / name
                if not path.is_file():
                    raise PipelineError(f"Installed Codex schema is missing {name}")
                hashes[name] = digest(path.read_bytes())
        with tempfile.TemporaryDirectory(prefix="intelitex-codex-doctor-") as temp:
            runtime = Path(temp)
            home, sqlite_home, work = runtime / "home", runtime / "sqlite", runtime / "work"
            for directory in (home, sqlite_home, work):
                directory.mkdir(mode=0o700)
            env = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
            env.update({"HOME": str(home), "CODEX_HOME": str(home), "CODEX_SQLITE_HOME": str(sqlite_home)})
            recorder = AttemptRecorder(runtime / "evidence", {"operation": "doctor", "model_turn": False})
            proc = subprocess.Popen(app_server_argv(executable), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=work, env=env, start_new_session=True)
            rpc = _RpcSession(proc, recorder)
            deadline = time.monotonic() + 20
            disabled = 0
            try:
                rpc.request("initialize", {"clientInfo": {"name": "intelitex-doctor", "title": "Intelitex Doctor", "version": "1.11.0"},
                                           "capabilities": {"experimentalApi": True}}, deadline)
                rpc.send({"method": "initialized", "params": {}})
                listed = rpc.request("skills/list", {"cwds": [str(work)], "forceReload": True}, deadline)
                enabled = []
                for group in listed.get("data", []):
                    if group.get("errors"):
                        raise PipelineError(f"skills/list errors: {group['errors']}")
                    enabled.extend(skill["path"] for skill in group.get("skills", []) if skill.get("enabled"))
                for skill_path in sorted(set(enabled)):
                    result = rpc.request("skills/config/write", {"path": skill_path, "enabled": False}, deadline)
                    if result.get("effectiveEnabled") is not False:
                        raise PipelineError(f"skill remained enabled: {skill_path}")
                    disabled += 1
                verified = rpc.request("skills/list", {"cwds": [str(work)], "forceReload": True}, deadline)
                if any(skill.get("enabled") for group in verified.get("data", []) for skill in group.get("skills", [])):
                    raise PipelineError("skills remained enabled after re-list")
                thread = rpc.request("thread/start", {
                    "cwd": str(work), "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": False,
                    "baseInstructions": "Return only the supplied result. Do not use tools or runtime context.",
                    "developerInstructions": "", "personality": "none", "environments": [], "dynamicTools": [],
                    "selectedCapabilityRoots": [], "runtimeWorkspaceRoots": [],
                }, deadline).get("thread", {})
                if not thread.get("id") or not thread.get("path"):
                    raise PipelineError("persisted no-turn thread omitted id/path")
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=3)
        return {"status": "passed", "version": version, "executable": executable,
                "schema_hashes": hashes, "argv": app_server_argv(executable),
                "strict_config_startup": True, "skills_disabled": disabled,
                "persisted_thread_no_turn": True, "model_turn": False}
    except (OSError, subprocess.SubprocessError, PipelineError) as exc:
        return {"status": "failed", "error": str(exc), "model_turn": False}


def doctor_report(settings: dict[str, Any], project: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "recording": {"status": "passed" if os.access(project, os.W_OK) else "failed", "path": str(project)},
        "profiles": [], "network_checks": "not run", "token_count_checks": "not run", "generation": "not run",
    }
    for name, profile in settings["profiles"].items():
        row = {"name": name, "provider": profile.get("provider"), "enabled": profile.get("enabled", True),
               "credential": credential_status(profile)}
        if row["enabled"] and row["provider"] == "codex":
            row["protocol"] = codex_protocol_doctor(profile)
        report["profiles"].append(row)
    catalog, path = load_catalog(project)
    report["catalog"] = {"status": "passed", "path": str(path), "models": len(catalog["models"]), "sha256": digest(path.read_bytes())}
    return report


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))
