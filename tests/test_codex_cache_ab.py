from __future__ import annotations

from pathlib import Path

import pytest

from experiments import codex_cache_ab as ab
from bookpipe.engine import Runner
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, digest, read_json
from test_codex_cache import PROMPTS, SETTINGS, SyntheticCodex, inputs_for


@pytest.fixture
def prepared(tmp_path):
    project = tmp_path / "production"
    for n, prompt in PROMPTS.items():
        path = project / "prompts" / f"pass{n}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    atomic_json(project / "settings.json", {**SETTINGS, "profiles": {}, "default_profile": "codex-sol-low"})
    store = Store(project)
    try:
        Runner(store, SyntheticCodex(project, wire="canonical"), SETTINGS, Display(True)).run(
            2, "pass2/chunk1", inputs_for(2))
    finally:
        store.close()
    snapshot = {str(path.relative_to(project)): digest(path.read_bytes()) for path in project.rglob("*") if path.is_file()}
    scratch = tmp_path / "scratch"
    ab.prepare(project, "chunk1", scratch, "codex-sol-low")
    after = {str(path.relative_to(project)): digest(path.read_bytes()) for path in project.rglob("*") if path.is_file()}
    assert after == snapshot
    return project, scratch


def test_prepare_freezes_same_source_context_memory_and_profile(prepared):
    _, scratch = prepared
    frozen = ab.load_frozen(scratch)
    assert frozen["p2_inputs"] == inputs_for(2)
    assert frozen["profile"]["model"] == "gpt-6.1-sol"
    assert frozen["profile"]["reasoning_effort"] == "low"
    assert frozen["prompts"] == {str(n): prompt for n, prompt in PROMPTS.items()}
    assert not (scratch / "canonical").exists() and not (scratch / "cache-v1").exists()


def test_run_variants_preserve_dependencies_and_report_provider_usage(prepared, monkeypatch):
    _, scratch = prepared
    class OfflineCodex(SyntheticCodex):
        def __init__(self, profile, ui):
            super().__init__(Path(profile["project_root"]), wire=profile["options"]["translation_wire_format"])

        def generate(self, body, directory, recorder):
            raw, meta = super().generate(body, directory, recorder)
            n = body["pass_no"]
            usage = {"input_tokens": n * 100,
                     "cached_input_tokens": n * 50 if body["wire_format"] == "cache-v1" and n > 2 else 0,
                     "cache_write_input_tokens": n, "output_tokens": n * 10}
            recorder.usage({"fake": True}, usage)
            return raw, {**meta, "elapsed_seconds": n / 10}
    monkeypatch.setattr(ab, "CodexAppServerClient", OfflineCodex)
    for wire in ("canonical", "cache-v1"):
        assert ab.run(scratch, wire)["status"] == "completed"
        with pytest.raises(FileExistsError):
            ab.run(scratch, wire)
    report = ab.report(scratch)
    assert read_json(scratch / "report.json") == report
    for wire, cache in (("canonical", 0), ("cache-v1", 600)):
        variant = report["variants"][wire]
        assert variant["totals"] == {"input_tokens": 1400, "cached_input_tokens": cache,
                                     "cache_write_input_tokens": 14, "output_tokens": 140}
        assert variant["cache_read_ratio"] == cache / 1400
        assert variant["wall_time_seconds"] is not None
        assert len(variant["passes"]) == 4
        semantic = {
            n: read_json(next((scratch / wire / "artifacts" / f"pass{n}").rglob("request.semantic.json")))["input_payload"]
            for n in range(2, 6)
        }
        assert semantic[2] == inputs_for(2)
        assert semantic[3] == inputs_for(3)
        assert semantic[4] == inputs_for(4)
        assert semantic[5] == inputs_for(5)
    hashes = [row["attempts"][0]["cache_prefix_sha256"] for row in report["variants"]["cache-v1"]["passes"]]
    assert len(set(hashes)) == 1 and hashes[0] is not None
    assert all(row["attempts"][0]["cache_prefix_sha256"] is None for row in report["variants"]["canonical"]["passes"])


def test_frozen_inputs_reject_changes(prepared):
    _, scratch = prepared
    frozen = read_json(scratch / "frozen.json")
    frozen["p2_inputs"]["SOURCE_BLOCKS"][0]["text"] = "changed"
    atomic_json(scratch / "frozen.json", frozen)
    with pytest.raises(PipelineError, match="changed"):
        ab.load_frozen(scratch)


def test_unknown_telemetry_does_not_become_zero():
    assert ab._sum_reported([{"input_tokens": 12}, {"input_tokens": None}], "input_tokens") is None
    assert ab._sum_reported([], "input_tokens") is None


def test_report_accounts_for_validation_retries(prepared):
    _, scratch = prepared
    root = scratch / "cache-v1"
    for attempt_no, count in ((1, 100), (2, 200)):
        attempt = root / "artifacts" / "pass3" / "chunk1" / "fingerprint" / f"attempt_{attempt_no:03d}"
        atomic_json(attempt / "attempt.json", {"lifecycle": {"acceptance": "checkpointed" if attempt_no == 2 else "not_accepted"}})
        atomic_json(attempt / "response_meta.json", {"elapsed_seconds": 2, "cache_prefix_sha256": "hash"})
        atomic_json(attempt / "usage.json", {field: count for field in ab.USAGE_FIELDS})
    result = ab.report(scratch)["variants"]["cache-v1"]
    assert result["totals"]["input_tokens"] == 300
    assert result["passes"][1]["input_tokens"] == 300
    assert len(result["passes"][1]["attempts"]) == 2
    assert result["wall_time_seconds"] is None
