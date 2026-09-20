from __future__ import annotations

import json
import stat

from bookpipe.util import read_json
from experiments.pass1_compact_transport import inspect_harness_rollout, parser, prepare_harness_suppression


def test_parser_accepts_max_reasoning_effort():
    args = parser().parse_args(
        [
            "run",
            "--scratch",
            "/tmp/scratch",
            "--report",
            "/tmp/report.md",
            "--effort",
            "max",
        ]
    )

    assert args.effort == "max"


def test_prepare_harness_suppression_is_scratch_only(tmp_path):
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    auth_source = auth_dir / "auth.json"
    auth_source.write_text("{}", encoding="utf-8")
    source_catalog = {
        "identity": {"must_not_be_copied": True},
        "models": [
            {"slug": "gpt-5.6-sol", "use_responses_lite": True, "multi_agent_version": "v2"},
            {"slug": "other", "use_responses_lite": True, "multi_agent_version": None},
        ],
    }
    (auth_dir / "models_cache.json").write_text(json.dumps(source_catalog), encoding="utf-8")
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    profile = {"executable": str(executable), "options": {"auth_source": str(auth_source)}}

    result = prepare_harness_suppression(scratch, profile, "gpt-5.6-sol")

    override = read_json(scratch / "harness-suppression" / "models.json")
    assert list(override) == ["models"]
    sol = next(item for item in override["models"] if item["slug"] == "gpt-5.6-sol")
    assert sol["use_responses_lite"] is False
    assert sol["multi_agent_version"] is None
    assert source_catalog == read_json(auth_dir / "models_cache.json")
    wrapper = (scratch / "harness-suppression" / "codex-no-harness").read_text(encoding="utf-8")
    assert "include_collaboration_mode_instructions=false" in wrapper
    assert "include_environment_context=false" in wrapper
    assert profile["executable"] == result["wrapper"]


def test_inspect_harness_rollout_accepts_only_prompt_and_permissions(tmp_path):
    attempt = tmp_path / "attempt"
    rollout = attempt / "codex" / "rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    prompt = "Trusted compact prompt"
    records = [
        {"type": "session_meta", "payload": {"base_instructions": {"text": "thin", "provenance": {"type": "custom"}}}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_text", "text": "<permissions instructions>\nread-only"},
        ]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": '{"x":1}'},
        ]}},
    ]
    rollout.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    result = inspect_harness_rollout(attempt, prompt)

    assert result["status"] == "passed"
    assert result["task_prompt_segments"] == 1
    assert result["permission_segments"] == 1
    assert result["unexpected_developer_segments"] == 0
    assert result["forbidden_markers"] == []
