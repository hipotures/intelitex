"""Offline profile resolution only: no provider discovery or inference."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from bookpipe.application.projects import effective_settings
from bookpipe.engine import _semantic_execution_signature
from bookpipe.profiles import builtin_codex_profiles, resolve_profile, validate_profiles
from bookpipe.provider_registry import ProviderPool
from bookpipe.util import PipelineError, atomic_json, read_json


BUNDLE = Path(__file__).resolve().parent.parent
SHARED = "cache-shared-v2"


@pytest.fixture
def settings():
    value = read_json(BUNDLE / "settings.default.json")
    value.pop("pipeline_execution", None)  # This suite describes existing legacy projects.
    return value


@pytest.mark.parametrize("name", sorted(builtin_codex_profiles()))
def test_all_builtin_codex_profiles_default_to_shared_without_mutating_settings(settings, name, tmp_path):
    original = copy.deepcopy(settings)
    advertised = builtin_codex_profiles()[name]
    assert advertised["options"] == {
        "auth_source": "~/.codex/auth.json",
        "p1_wire_format": SHARED,
        "translation_wire_format": SHARED,
        "translation_thread_strategy": "paired-passes-v1",
    }
    for number in range(1, 6):
        resolved_name, profile, _ = resolve_profile(settings, number, command_profile=name, project=tmp_path)
        assert resolved_name == name
        assert profile["model"] == advertised["model"]
        assert profile["reasoning_effort"] == advertised["reasoning_effort"]
        assert profile["options"] == advertised["options"]
    assert settings == original


@pytest.mark.parametrize("options", [None, {}, {"auth_source": "/shared/auth.json"},
                                     {"p1_wire_format": "compact-v1"},
                                     {"translation_wire_format": "cache-v2"},
                                     {"translation_thread_strategy": "fresh-root"}])
def test_custom_codex_profiles_inherit_only_missing_options(settings, options, tmp_path):
    custom = {"provider": "codex", "model": "user-model", "context_size": 100000,
              "reasoning_effort": None, "request_timeout": 4321}
    if options is not None:
        custom["options"] = options
    settings["profiles"]["custom"] = custom
    settings["default_profile"] = "custom"
    before = copy.deepcopy(settings)
    _, profile, _ = resolve_profile(settings, 1, project=tmp_path)
    assert profile["options"] == {"p1_wire_format": SHARED, "translation_wire_format": SHARED,
                                  "translation_thread_strategy": "paired-passes-v1",
                                  **(options or {})}
    for key, value in custom.items():
        if key != "options":
            assert profile[key] == value
    assert settings == before


@pytest.mark.parametrize("p1", ["compact-v1", "canonical", "cache-v1", "cache-shared-v1", SHARED])
@pytest.mark.parametrize("translation", ["canonical", "cache-v1", "cache-v2", "cache-shared-v1", SHARED])
def test_explicit_project_wire_choices_override_builtin_defaults(settings, p1, translation, tmp_path):
    profile = builtin_codex_profiles()["codex-sol-high"]
    profile["options"].update(p1_wire_format=p1, translation_wire_format=translation)
    settings["profiles"]["codex-sol-high"] = profile
    settings["default_profile"] = "codex-sol-high"
    validate_profiles(settings, tmp_path)
    for number in range(1, 6):
        _, resolved, _ = resolve_profile(settings, number, project=tmp_path)
        assert resolved["options"] == profile["options"]
        assert resolved["model"] == "gpt-6.1-sol"
        assert resolved["reasoning_effort"] == "high"


@pytest.mark.parametrize("provider", ["openai", "vllm", "llamacpp"])
@pytest.mark.parametrize("options", [{}, {"p1_wire_format": "provider-specific",
                                        "translation_wire_format": "provider-specific"}])
def test_non_codex_profiles_never_acquire_or_validate_codex_defaults(settings, provider, options, tmp_path):
    settings["profiles"]["other"] = {"provider": provider, "model": "other-model",
        "endpoint": "http://127.0.0.1:9/v1", "context_size": 100000,
        "reasoning_effort": None, "options": options}
    settings["default_profile"] = "other"
    original = copy.deepcopy(settings)
    for number in range(1, 6):
        _, resolved, _ = resolve_profile(settings, number, project=tmp_path)
        assert resolved["provider"] == provider
        assert resolved["options"] == options
    assert settings == original


@pytest.mark.parametrize("field", ["p1_wire_format", "translation_wire_format"])
@pytest.mark.parametrize("value", ["unknown", None, [], 1])
def test_invalid_explicit_wire_format_still_fails_closed(settings, field, value):
    profile = builtin_codex_profiles()["codex-sol-high"]
    profile["options"][field] = value
    settings["profiles"]["invalid"] = profile
    with pytest.raises(PipelineError, match="wire_format"):
        validate_profiles(settings)


def salvation_settings(settings):
    """Frozen configuration shape inspected in salvation-03; no book/state fixture."""
    settings["default_profile"] = "codex-astra-high"
    settings["pass_profiles"] = {"2": "codex-astra-low", "3": "codex-astra-medium",
                                  "4": "codex-astra-low", "5": "codex-astra-medium"}
    for name in ("codex-astra-high", "codex-astra-medium", "codex-astra-low", "codex-sol-high"):
        profile = builtin_codex_profiles()[name]
        profile["options"] = {"auth_source": "~/.codex/auth.json"}
        settings["profiles"][name] = profile
    # Only this pre-existing explicit wire option needs a local deployment edit.
    settings["profiles"]["codex-sol-high"]["options"]["translation_wire_format"] = SHARED
    return settings


def test_salvation_resolution_and_restart_are_read_only(settings, tmp_path):
    configured = salvation_settings(settings)
    atomic_json(tmp_path / "settings.json", configured)
    atomic_json(tmp_path / "web.config.json", {"sections": {},
        "pass_profiles": {str(n): "codex-sol-high" for n in range(2, 6)}})
    original = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    snapshots = []
    for _ in range(2):  # New scope/pool simulates resolution after worker restart.
        loaded = effective_settings(BUNDLE, tmp_path)
        pool = ProviderPool(loaded, None, tmp_path)
        try:
            rows = []
            for number in range(1, 6):
                client = pool.for_pass(number)
                profile = client.resolved_profile
                assert client.profile_name == ("codex-astra-high" if number == 1 else "codex-sol-high")
                assert profile["model"] == ("gpt-6-astra" if number == 1 else "gpt-6.1-sol")
                assert profile["reasoning_effort"] == "high"
                assert profile["options"]["p1_wire_format"] == SHARED
                assert profile["options"]["translation_wire_format"] == SHARED
                assert profile["options"]["translation_thread_strategy"] == "paired-passes-v1"
                rows.append(copy.deepcopy(profile))
            snapshots.append(rows)
        finally:
            pool.close()
    assert snapshots[0] == snapshots[1]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == original


def test_profile_assignment_layers_keep_their_existing_precedence(settings, tmp_path):
    salvation_settings(settings)
    atomic_json(tmp_path / "web.config.json", {
        "pass_profiles": {"2": "codex-sol-high"},
        "sections": {"ch0022": {"profiles": {"2": "codex-sol-medium"}}},
    })
    # Core selector: CLI pass > CLI profile > project pass > project default.
    assert resolve_profile(settings, 2, project=tmp_path)[0] == "codex-astra-low"
    assert resolve_profile(settings, 2, command_profile="codex-luna-low", project=tmp_path)[0] == "codex-luna-low"
    assert resolve_profile(settings, 2, command_profile="codex-luna-low",
                           command_pass_profiles={2: "codex-sol-low"}, project=tmp_path)[0] == "codex-sol-low"
    # Pool supplies web/CLI-pass/section maps to the core selector in this order.
    pool = ProviderPool(settings, None, tmp_path, command_profile="codex-luna-low")
    try:
        assert pool.for_pass(2).profile_name == "codex-sol-high"
        pool.command_pass_profiles = {2: "codex-sol-low"}
        pool.select_section("absent")
        assert pool.for_pass(2).profile_name == "codex-sol-low"
        pool.select_section("ch0022")
        assert pool.for_pass(2).profile_name == "codex-sol-medium"
    finally:
        pool.close()


def test_default_wire_change_does_not_change_execution_compatibility(settings, tmp_path):
    profile = builtin_codex_profiles()["codex-sol-high"]
    profile["options"].update(p1_wire_format="compact-v1", translation_wire_format="cache-v2")
    settings["profiles"]["custom"] = profile
    settings["default_profile"] = "custom"
    _, before, _ = resolve_profile(settings, 4, project=tmp_path)
    settings["profiles"]["custom"]["options"].pop("p1_wire_format")
    settings["profiles"]["custom"]["options"].pop("translation_wire_format")
    _, after, _ = resolve_profile(settings, 4, project=tmp_path)
    assert before != after
    assert _semantic_execution_signature({"resolved_profile": before}) == _semantic_execution_signature(
        {"resolved_profile": after})
