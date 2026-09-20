from __future__ import annotations

import copy
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .catalog import load_catalog, model_entry
from .util import PipelineError, atomic_json, digest


PROVIDERS = {"llamacpp", "openai", "codex"}
COMMON_KEYS = {
    "provider", "model", "enabled", "context_size", "planning_output_reserve",
    "max_output_tokens", "request_timeout", "reasoning_effort", "credential_env",
    "endpoint", "executable", "runtime_root", "options", "temperature", "seed",
}


def legacy_local_profile(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "llamacpp",
        "enabled": True,
        "model": settings.get("model"),
        "endpoint": settings.get("host", "127.0.0.1"),
        "context_size": settings.get("context_size"),
        "planning_output_reserve": max(int(p.get("max_tokens", 0)) for p in settings["passes"].values()),
        "request_timeout": settings.get("request_timeout", 1200),
        "credential_env": "LLAMA_API_KEY",
        "temperature": None,
        "seed": settings.get("seed", 42),
        "options": {"port": settings.get("port", 8080), "thinking": settings.get("thinking", "off"),
                    "request_extra": settings.get("request_extra", {})},
    }


def with_profiles(settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    value = copy.deepcopy(settings)
    if "profiles" in value:
        return value, False
    value["format_version"] = 2
    value["profiles"] = {
        "local": legacy_local_profile(value),
        "openai-template": {
            "provider": "openai", "enabled": False, "model": None,
            "endpoint": "https://api.openai.com/v1", "credential_env": "OPENAI_API_KEY",
            "context_size": None, "planning_output_reserve": 16000,
            "max_output_tokens": None, "request_timeout": value.get("request_timeout", 1200),
            "reasoning_effort": None, "options": {},
        },
        "codex-template": {
            "provider": "codex", "enabled": False, "model": None,
            "executable": "codex", "context_size": None,
            "planning_output_reserve": 16000, "max_output_tokens": None,
            "request_timeout": value.get("request_timeout", 1200),
            "reasoning_effort": None, "options": {},
        },
    }
    value["default_profile"] = "local"
    value["pass_profiles"] = {}
    return value, True


def backup_sqlite(project: Path) -> Path | None:
    source = project / "state.sqlite3"
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = project / "backups" / f"state-before-profiles-{stamp}-{digest(source.read_bytes())[:10]}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def migrate_settings_file(project: Path, settings: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    value, changed = with_profiles(settings)
    if not changed:
        return value, None
    backup = backup_sqlite(project)
    atomic_json(project / "settings.json", value)
    return value, backup


def validate_profiles(settings: dict[str, Any], project: Path | None = None) -> None:
    profiles = settings.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise PipelineError("At least one named profile is required.")
    if settings.get("default_profile") not in profiles:
        raise PipelineError("default_profile names an unknown profile.")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name or not isinstance(profile, dict):
            raise PipelineError("Profile names and values must be non-empty strings/objects.")
        unknown = set(profile) - COMMON_KEYS
        if unknown:
            raise PipelineError(f"Profile {name!r} has unknown keys: {sorted(unknown)}")
        provider = profile.get("provider")
        if provider not in PROVIDERS:
            raise PipelineError(f"Profile {name!r} has unknown provider {provider!r}.")
        if not isinstance(profile.get("options", {}), dict):
            raise PipelineError(f"Profile {name!r} options must be an object.")
        p1_wire_format = profile.get("options", {}).get("p1_wire_format")
        if provider == "codex" and p1_wire_format not in {None, "compact-v1", "canonical"}:
            raise PipelineError(
                f"Profile {name!r} options.p1_wire_format must be 'compact-v1' or 'canonical'."
            )
        def secret_key(value: Any) -> bool:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = str(key).casefold().replace("-", "_")
                    if normalized in {"authorization", "api_key", "access_token", "refresh_token", "password", "secret"}:
                        return True
                    if secret_key(item):
                        return True
            elif isinstance(value, list):
                return any(secret_key(item) for item in value)
            return False
        if secret_key(profile.get("options", {})):
            raise PipelineError(f"Profile {name!r} contains a literal credential-like option; use a credential reference.")
        if profile.get("enabled", True) and provider != "llamacpp" and not profile.get("model"):
            raise PipelineError(f"Enabled {provider} profile {name!r} requires an explicit model ID.")
        for field in ("context_size", "planning_output_reserve", "max_output_tokens"):
            number = profile.get(field)
            if number is not None and (not isinstance(number, int) or number <= 0):
                raise PipelineError(f"Profile {name!r} {field} must be a positive integer or null.")
        if provider == "codex" and profile.get("max_output_tokens") is not None:
            raise PipelineError(f"Profile {name!r}: Codex app-server has no verified hard output-token cap.")
        if provider in {"openai", "codex"} and profile.get("temperature") is not None:
            raise PipelineError(f"Profile {name!r}: explicit temperature is not verified for this provider/model.")
        if provider in {"openai", "codex"} and profile.get("seed") is not None:
            raise PipelineError(f"Profile {name!r}: seed is not supported by this native transport.")
    for pass_no, name in settings.get("pass_profiles", {}).items():
        if str(pass_no) not in {"1", "2", "3", "4", "5"} or name not in profiles:
            raise PipelineError(f"Invalid pass profile assignment: {pass_no}={name!r}")


def resolve_profile(
    settings: dict[str, Any],
    pass_no: int,
    *,
    command_profile: str | None = None,
    command_pass_profiles: dict[int, str] | None = None,
    project: Path | None = None,
) -> tuple[str, dict[str, Any], dict[str, str]]:
    validate_profiles(settings, project)
    assignments = command_pass_profiles or {}
    if pass_no in assignments:
        name, source = assignments[pass_no], "command_pass_override"
    elif command_profile:
        name, source = command_profile, "command_profile_override"
    elif str(pass_no) in settings.get("pass_profiles", {}):
        name, source = settings["pass_profiles"][str(pass_no)], "project_pass_profile"
    else:
        name, source = settings["default_profile"], "project_default_profile"
    try:
        profile = copy.deepcopy(settings["profiles"][name])
    except KeyError as exc:
        raise PipelineError(f"Unknown profile {name!r}.") from exc
    if not profile.get("enabled", True):
        raise PipelineError(f"Profile {name!r} is a template/disabled profile.")
    profile.setdefault("planning_output_reserve", settings["passes"][str(pass_no)]["max_tokens"])
    profile.setdefault("request_timeout", settings.get("request_timeout", 1200))
    if profile["provider"] == "llamacpp":
        profile["temperature"] = settings["passes"][str(pass_no)]["temperature"]
        profile["max_output_tokens"] = settings["passes"][str(pass_no)]["max_tokens"]
        profile["planning_output_reserve"] = profile["max_output_tokens"]
    catalog, _ = load_catalog(project)
    entry = model_entry(catalog, profile["provider"], profile.get("model"))
    if entry:
        profile.setdefault("context_size", entry.get("context_tokens"))
        efforts = entry.get("efforts", [])
        effort = profile.get("reasoning_effort")
        if effort is not None and effort not in efforts:
            raise PipelineError(f"Profile {name!r}: effort {effort!r} is not listed for {profile['model']!r}.")
    if not profile.get("context_size") and profile["provider"] in {"openai", "codex"}:
        raise PipelineError(f"Profile {name!r} needs a verified/configured context_size or matching catalog entry.")
    return name, profile, {"profile": source}


def credential_status(profile: dict[str, Any]) -> dict[str, Any]:
    env_name = profile.get("credential_env")
    return {"reference": env_name, "present": bool(env_name and os.environ.get(env_name)), "value": "[never displayed]"}
