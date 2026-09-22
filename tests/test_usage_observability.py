from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from bookpipe.contracts import InferenceUnitContext, normalized_usage
from bookpipe.engine import Runner
from bookpipe.evidence import AttemptRecorder
from bookpipe.store import Store
from bookpipe.usage import UsageByUnitResult, usage_by_unit_report
from bookpipe.util import atomic_json, dumps, read_json


class CaptureProgress:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def _assert_no_mutable_artifact_values(value):
    assert not isinstance(value, (dict, list, set))
    if is_dataclass(value):
        for field in fields(value):
            _assert_no_mutable_artifact_values(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_no_mutable_artifact_values(item)


class ObservableProvider:
    profile_name = "local-qwen"
    model = "Qwen3.8-27B-Q6_K_XL"
    timeout = 5.0
    context = 10000
    resolved_profile = {"planning_output_reserve": 100, "max_output_tokens": 100}

    def __init__(self, *, byte_bound: bool):
        self.provider = "codex" if byte_bound else "llamacpp"
        self.identity = {"id": self.model}
        self.calls = 0
        self.preflight_input_unit = "utf8_bytes" if byte_bound else "tokens"
        self.preflight_input_quality = "conservative_upper_bound" if byte_bound else "provider_exact"
        self.preflight_input_method = "UTF-8 byte length" if byte_bound else "native token counter"

    def body(self, prompt, inputs, schema, pass_no):
        return {"prompt": prompt, "inputs": inputs, "schema": schema, "pass_no": pass_no}

    def preflight(self, body, recorder=None):
        return 128862 if self.preflight_input_unit == "utf8_bytes" else 27318

    def generate(self, body, directory, recorder):
        self.calls += 1
        value = {"translations": [{"id": row["id"], "text": "Przekład."}
                                  for row in body["inputs"]["SOURCE_BLOCKS"]]}
        recorder.transport_request(body, body["schema"])
        recorder.usage([], normalized_usage(
            input_tokens=20, output_tokens=5, total_tokens=25,
            source="fixture", scope="attempt",
        ))
        raw = dumps(value)
        recorder.write_answer(raw)
        return raw, {"finish_reason": "stop", "usage_status": "reported", "elapsed_seconds": 1.0}


@pytest.mark.parametrize(
    "byte_bound,value,unit,quality",
    [
        (False, 27318, "tokens", "provider_exact"),
        (True, 128862, "utf8_bytes", "conservative_upper_bound"),
    ],
)
def test_provider_waiting_has_structured_request_and_truthful_preflight(
    tmp_path, byte_bound, value, unit, quality,
):
    root = tmp_path / ("bytes" if byte_bound else "tokens")
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "pass5.txt").write_text("Translate.", encoding="utf-8")
    store = Store(root)
    progress = CaptureProgress()
    provider = ObservableProvider(byte_bound=byte_bound)
    settings = {"json_retries": 0, "passes": {str(i): {"max_tokens": 100} for i in range(1, 6)}}
    inputs = {"SOURCE_BLOCKS": [{"id": "B1", "text": "Source."}], "POLISH_DRAFT": {
        "translations": [{"id": "B1", "text": "Szkic."}],
    }, "CORRECTION_LEDGER": {"checks": [], "corrections": []}}
    try:
        runner = Runner(store, provider, settings, progress)
        runner.run(
            5, "pass5/ch0016_c0004", inputs,
            unit_context=InferenceUnitContext(
                unit_id="ch0016_c0004", chapter_id="ch0016", chunk_id="ch0016_c0004",
                unit_index=4,
            ),
        )
        # The accepted semantic checkpoint returns before attempt creation or provider contact.
        runner.run(
            5, "pass5/ch0016_c0004", inputs,
            unit_context=InferenceUnitContext(
                unit_id="ch0016_c0004", chapter_id="ch0016", chunk_id="ch0016_c0004",
                unit_index=4,
            ),
        )
    finally:
        store.close()
    waiting = next(event for event in progress.events if event.kind == "provider_waiting")
    assert provider.calls == 1
    assert len(list((root / "artifacts").rglob("attempt.json"))) == 1
    assert waiting.values == {
        "pass_no": 5, "task_key": "pass5/ch0016_c0004", "attempt_number": 1,
        "chapter_id": "ch0016", "unit_id": "ch0016_c0004", "chunk_id": "ch0016_c0004",
        "analysis_unit_id": None, "unit_index": 4,
        "provider": provider.provider, "profile": "local-qwen",
        "requested_model": "Qwen3.8-27B-Q6_K_XL", "input_value": value,
        "input_unit": unit, "input_quality": quality,
        "input_method": provider.preflight_input_method,
    }
    manifest = next((root / "artifacts").rglob("attempt.json"))
    assert read_json(manifest)["preflight_input"]["unit"] == unit


def _attempt(root, task_key, number, *, pass_no, unit_identity=None, generation="completed",
             validation="passed", usage=None, elapsed=None, accepted=False, preflight=None):
    unit = task_key.split("/", 1)[1]
    directory = root / "artifacts" / task_key / "fingerprint" / f"attempt_{number:03d}"
    identity = {
        "pass": pass_no, "task_key": task_key, "attempt_number": number,
        "attempt_id": f"fp-{pass_no}-{number}", "provider": "codex",
        "profile": "codex-sol-medium", "requested_model": "gpt-5.6-sol",
    }
    if unit_identity:
        identity.update(unit_identity)
    recorder = AttemptRecorder(directory, identity)
    if preflight:
        recorder.preflight(preflight)
    if usage is not None:
        recorder.usage([usage], normalized_usage(source="fixture_usage", scope="attempt", **usage))
    recorder.finish(
        generation=generation, validation=validation,
        metadata={"elapsed_seconds": elapsed,
                  "status": "preflight_failed" if generation == "not_submitted" else generation,
                  "reported_model": "gpt-5.6-sol"},
        error={"type": "FixtureError", "message": "fixture"} if generation != "completed" else None,
    )
    if accepted:
        recorder.mark_accepted()
    return directory


def test_usage_by_unit_accounts_physical_attempts_recovery_unknowns_and_legacy_p1(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    atomic_json(root / "book.json", {"chunks": [{
        "id": "ch0016_c0004", "chapter_id": "ch0016", "number": 44,
    }]})
    atomic_json(root / "analysis_plan.json", [{
        "id": "ch0001_a001", "chapter_id": "ch0001",
    }])
    identity = {"unit_id": "ch0016_c0004", "chapter_id": "ch0016",
                "chunk_id": "ch0016_c0004", "unit_index": 44}
    first = _attempt(
        root, "pass2/ch0016_c0004", 1, pass_no=2, unit_identity=identity,
        generation="failed", validation="not_run",
        usage={"input_tokens": 10, "cached_input_tokens": 2, "cache_write_input_tokens": 1,
               "output_tokens": 4, "reasoning_output_tokens": 3, "total_tokens": 14},
        elapsed=1.5,
    )
    second = _attempt(
        root, "pass2/ch0016_c0004", 2, pass_no=2, unit_identity=identity,
        usage={"input_tokens": 20, "cached_input_tokens": 5, "cache_write_input_tokens": 2,
               "output_tokens": 8, "reasoning_output_tokens": 4, "total_tokens": 28},
        elapsed=2.5, accepted=True,
        preflight={"value": 128862, "unit": "utf8_bytes",
                   "quality": "conservative_upper_bound", "method": "UTF-8 byte length"},
    )
    _attempt(
        root, "pass2/ch0016_c0004", 3, pass_no=2, unit_identity=identity,
        generation="not_submitted", validation="not_run", elapsed=None,
    )
    _attempt(
        root, "pass2/ch0016_c0004", 4, pass_no=2, unit_identity=identity,
        generation="failed", validation="not_run", elapsed=None,
    )
    for pass_no in (3, 4, 5):
        _attempt(
            root, f"pass{pass_no}/ch0016_c0004", 1, pass_no=pass_no,
            unit_identity=identity,
            usage={"input_tokens": 1, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                   "output_tokens": 1, "reasoning_output_tokens": 0, "total_tokens": 2},
            elapsed=0.25, accepted=True,
        )
    recovery = root / "artifacts" / "pass2" / "ch0016_c0004" / "new-fingerprint" / "recovery.json"
    atomic_json(recovery, {"source_attempt": str(second.relative_to(root)), "repairs": []})

    # Legacy identity has no new unit/chapter fields. Reporting alone performs the narrow task-key fallback.
    _attempt(
        root, "pass1/ch0001_a001", 1, pass_no=1,
        usage={"input_tokens": 7, "cached_input_tokens": None, "cache_write_input_tokens": None,
               "output_tokens": 3, "reasoning_output_tokens": None, "total_tokens": 10},
        elapsed=0.75, accepted=True,
        preflight={"value": 7, "unit": "tokens", "quality": "provider_exact", "method": "native"},
    )

    result = usage_by_unit_report(root)
    assert isinstance(result, UsageByUnitResult)
    _assert_no_mutable_artifact_values(result)
    assert [unit.unit_id for unit in result.units] == ["ch0001_a001", "ch0016_c0004"]
    p1 = result.units[0]
    assert p1.chapter_id == "ch0001" and p1.chunk_id is None
    assert p1.analysis_unit_id == "ch0001_a001"
    assert p1.passes[0].preflight_input.unit == "tokens"

    chunk = result.units[1]
    assert chunk.chapter_id == "ch0016" and chunk.chunk_id == "ch0016_c0004"
    assert chunk.analysis_unit_id is None and chunk.unit_index == 44
    assert [item.pass_no for item in chunk.passes] == [2, 3, 4, 5]
    row = chunk.passes[0]
    assert (row.provider, row.profile, row.requested_model) == (
        "codex", "codex-sol-medium", "gpt-5.6-sol",
    )
    assert row.reported_model == "gpt-5.6-sol"
    assert row.physical_attempt_count == 4 and row.provider_call_count == 3
    assert row.retry_count == 3 and row.failed_attempt_count == 2
    assert row.failed_before_submission_count == 1
    assert row.accepted_attempt_id == "fp-2-2"
    assert row.recovered_from_existing_attempt is True
    assert row.result_status == "recovered_without_new_call"
    assert row.input_tokens.value == 30
    assert (row.input_tokens.known_attempts, row.input_tokens.unknown_attempts) == (2, 1)
    assert row.cached_input_tokens.value == 7
    assert row.cache_write_input_tokens.value == 3
    assert row.output_tokens.value == 12 and row.reasoning_output_tokens.value == 7
    assert row.total_tokens.value == 42
    assert row.elapsed_seconds.value == 4.0 and row.elapsed_seconds.unknown_attempts == 1
    assert row.preflight_input.unit == "utf8_bytes"
    assert row.attempts[1].preflight_input.unit == "utf8_bytes"
    assert row.attempts[2].usage_status == "failed_before_submission"
    assert row.attempts[3].usage_status == "unavailable"
    assert chunk.input_tokens.value == 33
    assert (chunk.input_tokens.known_attempts, chunk.input_tokens.unknown_attempts) == (5, 1)
    assert usage_by_unit_report(root).units[1].passes[0].physical_attempt_count == 4
    assert usage_by_unit_report(root, "ch0016_c0004").units == (chunk,)
    with pytest.raises(FrozenInstanceError):
        chunk.unit_id = "changed"
    assert first.is_dir()  # failed usage evidence remains part of physical accounting
