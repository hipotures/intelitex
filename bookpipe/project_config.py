"""Capture continuation configuration under the predecessor's handoff lock.

Only settings, the optional model catalog, and text prompts are transferred.
The three configurable filesystem paths currently consumed by transports are
Codex runtime_root, executable, and options.auth_source. Endpoints, model IDs,
credential environment names and arbitrary option strings are not paths.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

from .catalog import validate_catalog
from .profiles import resolve_profile, validate_profiles, with_profiles
from .util import PipelineError, atomic_json, atomic_text, read_json


BUNDLE = Path(__file__).resolve().parent.parent


def _json(path: Path) -> dict:
    try:
        value = read_json(path)
    except (OSError, ValueError) as exc:
        # Do not include malformed input (which may contain credentials) in errors.
        raise PipelineError(f"Cannot inherit {path.name}: expected a readable UTF-8 JSON object.") from exc
    if not isinstance(value, dict):
        raise PipelineError(f"Cannot inherit {path.name}: expected a JSON object.")
    return value


def _reject_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold().replace("-", "_") in {
                "authorization", "api_key", "access_token", "refresh_token",
                "bearer_token", "token", "password", "secret", "secrets",
            }:
                raise PipelineError("Cannot inherit literal credential-like settings; use credential references.")
            _reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _reject_credentials(item)


def inherited_settings(previous: Path) -> dict:
    raw = _json(previous / "settings.json")
    _reject_credentials(raw)
    if type(raw.get("format_version", 1)) is not int or raw.get("format_version", 1) not in {1, 2}:
        raise PipelineError("Unsupported predecessor settings.json format_version.")
    defaults = read_json(BUNDLE / "settings.default.json")
    # Profiles and assignments are complete maps, never blended with templates.
    settings = {**defaults, **copy.deepcopy(raw)}
    if "profiles" not in raw:
        for field in ("profiles", "default_profile", "pass_profiles"):
            settings.pop(field, None)
    try:
        settings, _ = with_profiles(settings)  # Pure migration: never writes V1 or opens its database.
        if not isinstance(settings["pass_profiles"], dict):
            raise PipelineError("Inherited pass_profiles must be an object.")
        for field in ("whole_section_char_limit", "memory_tokens", "request_timeout"):
            if not isinstance(settings[field], (int, float)) or settings[field] <= 0:
                raise PipelineError(f"Inherited {field} must be positive.")
        for field in ("continuity_tokens", "json_retries", "analysis_source_limit"):
            if type(settings[field]) is not int or settings[field] < 0:
                raise PipelineError(f"Inherited {field} must be a nonnegative integer.")
        if not isinstance(settings["passes"], dict) or set(settings["passes"]) != {str(i) for i in range(1, 6)}:
            raise PipelineError("Inherited passes must configure passes 1 through 5.")
        for cfg in settings["passes"].values():
            if cfg["max_tokens"] <= 0 or not 0 <= cfg["temperature"] <= 2:
                raise PipelineError("Invalid inherited pass settings.")
        validate_profiles(settings, previous)
        for name, profile in settings["profiles"].items():
            reference = profile.get("credential_env")
            if reference is not None and (not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference)):
                raise PipelineError("Inherited credential_env must be an environment variable name.")
            if profile.get("enabled", True):
                resolve_profile(settings, 1, command_profile=name, project=previous)
    except (KeyError, TypeError, AttributeError) as exc:
        raise PipelineError("Malformed predecessor settings.json; fix the profile/pass configuration before continuing.") from exc
    return settings


def remap_path(value: str, field: str, old_project: Path, old_source: Path,
               new_project: Path, new_source: Path) -> str:
    """Remap known paths only; reject CWD-dependent paths rather than guessing V1's CWD."""
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise PipelineError(f"Inherited {field} must be a non-empty path string.")
    if field == "executable" and "/" not in value and value not in {".", ".."}:
        return value  # Bare executable names retain normal PATH lookup.
    path = Path(value) if field == "executable" else Path(value).expanduser()
    if not path.is_absolute():
        raise PipelineError(f"Inherited {field} is relative to an unknown working directory; configure an absolute path in V1.")
    resolved = path.resolve()
    pairs = [(old_project.resolve(), new_project.resolve()), (old_source.resolve(), new_source.resolve())]
    matches = [(old, new) for old, new in pairs if resolved.is_relative_to(old)]
    if len(matches) > 1:
        raise PipelineError(f"Ambiguous inherited {field}: predecessor project and source paths overlap.")
    # A symlink inside V1 pointing outside it must not retain a dependency on V1.
    if not matches and any(path.is_relative_to(old) for old, _ in pairs):
        raise PipelineError(f"Inherited {field} crosses a predecessor symlink; configure the external absolute target explicitly.")
    if not matches:
        return value
    if field == "options.auth_source":
        raise PipelineError("Project/source-local options.auth_source cannot be inherited automatically; "
                            "move credential material to a shared external location and configure that path in V1.")
    old, new = matches[0]
    target = new / resolved.relative_to(old)
    if not target.resolve().is_relative_to(new) or any(target.resolve().is_relative_to(base) for base, _ in pairs):
        raise PipelineError(f"Inherited {field} target escapes the new volume or still refers to the predecessor.")
    if field == "executable" and (not target.is_file() or not os.access(target, os.X_OK)):
        raise PipelineError("Inherited executable maps to a missing/non-executable resource; "
                            "provision the executable at the corresponding new-volume path or use a shared external executable.")
    return str(target)


def prepare_configuration(previous: Path, root: Path, old_source: str, new_source: Path) -> dict:
    """Read/validate everything before writing any configuration. Caller holds both locks."""
    for path in (root / "settings.json", root / "catalog" / "models.json", root / "prompts"):
        if path.exists() or path.is_symlink():
            raise PipelineError("Continuation destination already contains settings.json, catalog/models.json or prompts; "
                                "use a fresh project directory and apply explicit CLI overrides or edit after import.")
    if not isinstance(old_source, str) or not Path(old_source).is_absolute():
        raise PipelineError("Predecessor book.json source_root must be an absolute path for configuration inheritance.")
    catalog_path = previous / "catalog" / "models.json"
    catalog = validate_catalog(_json(catalog_path)) if catalog_path.exists() or catalog_path.is_symlink() else None
    if catalog is not None and isinstance(catalog.get("import"), dict):
        # catalog-import stores a historical source filename, not a resource
        # dependency. Retain its hash but do not propagate an old-volume path.
        source = catalog["import"].get("source")
        if isinstance(source, str) and Path(source).is_absolute():
            if any(Path(source).resolve().is_relative_to(base.resolve()) for base in (previous, Path(old_source))):
                catalog["import"].pop("source")
    settings = inherited_settings(previous)
    for profile in settings["profiles"].values():
        if profile["provider"] != "codex":
            continue
        for field in ("runtime_root", "executable"):
            if profile.get(field) is not None:
                profile[field] = remap_path(profile[field], field, previous, Path(old_source), root, new_source)
        options = profile.get("options", {})
        if options.get("auth_source") is not None:
            options["auth_source"] = remap_path(options["auth_source"], "options.auth_source",
                                               previous, Path(old_source), root, new_source)
    prompts = {}
    paths = {p.name: p for p in (previous / "prompts").glob("*.txt")}
    for number in range(1, 6):
        name = f"pass{number}.txt"
        paths.setdefault(name, previous / "prompts" / name)
    for name, path in sorted(paths.items()):
        try:
            # Decode raw bytes: read_text's universal-newline handling would change CRLF prompts.
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise PipelineError(f"Cannot inherit prompts/{name}: required readable UTF-8 text is missing or malformed.") from exc
        if not text.strip() or "\0" in text:
            raise PipelineError(f"Cannot inherit prompts/{name}: prompt is empty or contains NUL characters.")
        prompts[name] = text
    return {"settings": settings, "catalog": catalog, "prompts": prompts}


def materialize_configuration(root: Path, configuration: dict, settings: dict) -> None:
    if configuration["catalog"] is not None:
        atomic_json(root / "catalog" / "models.json", configuration["catalog"])
    atomic_json(root / "settings.json", settings)
    for name, text in configuration["prompts"].items():
        atomic_text(root / "prompts" / name, text)
