"""Detached, provider-neutral historical usage accounting by semantic unit."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import apply_estimate


_TASK_KEY = re.compile(r"^pass([1-5])/([^/]+)$")
_CHUNK_ID = re.compile(r"^(ch\d+)_c\d+$")
_ANALYSIS_ID = re.compile(r"^(ch\d+)_a\d+$")


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    """Sum of known values plus explicit coverage; unknown is never coerced to zero."""

    value: int | float | None
    known_attempts: int
    unknown_attempts: int


@dataclass(frozen=True, slots=True)
class PreflightInput:
    value: int
    unit: str
    quality: str
    method: str


@dataclass(frozen=True, slots=True)
class CostEstimate:
    status: str
    amount: float | None = None
    currency: str | None = None
    estimate_type: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    attempt_id: str
    attempt_number: int | None
    generation_status: str
    validation_status: str
    acceptance_status: str
    provider_contacted: bool | None
    accepted_checkpoint: bool
    checkpoint_source_for_recovery: bool
    usage_status: str
    usage_source: str | None
    reported_model: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    total_tokens: int | None
    elapsed_seconds: float | None
    preflight_input: PreflightInput | None
    cost: CostEstimate | None


@dataclass(frozen=True, slots=True)
class PassUsage:
    pass_no: int
    task_key: str
    provider: str | None
    profile: str | None
    requested_model: str | None
    reported_model: str | None
    physical_attempt_count: int
    provider_call_count: int
    unknown_provider_call_count: int
    retry_count: int
    failed_attempt_count: int
    failed_before_submission_count: int
    accepted_attempt_id: str | None
    recovered_from_existing_attempt: bool
    result_status: str
    usage_status: str
    usage_source: str | None
    preflight_input: PreflightInput | None
    input_tokens: UsageAggregate
    cached_input_tokens: UsageAggregate
    cache_write_input_tokens: UsageAggregate
    output_tokens: UsageAggregate
    reasoning_output_tokens: UsageAggregate
    total_tokens: UsageAggregate
    elapsed_seconds: UsageAggregate
    cost: CostEstimate | None
    attempts: tuple[AttemptUsage, ...]


@dataclass(frozen=True, slots=True)
class UnitUsage:
    unit_id: str
    chapter_id: str | None
    chunk_id: str | None
    analysis_unit_id: str | None
    unit_index: int | None
    passes: tuple[PassUsage, ...]
    input_tokens: UsageAggregate
    cached_input_tokens: UsageAggregate
    cache_write_input_tokens: UsageAggregate
    output_tokens: UsageAggregate
    reasoning_output_tokens: UsageAggregate
    total_tokens: UsageAggregate
    elapsed_seconds: UsageAggregate


@dataclass(frozen=True, slots=True)
class UsageByUnitResult:
    scope: str
    units: tuple[UnitUsage, ...]
    warning: str


@dataclass(frozen=True, slots=True)
class _AttemptRecord:
    unit_id: str
    chapter_id: str | None
    chunk_id: str | None
    analysis_unit_id: str | None
    unit_index: int | None
    pass_no: int
    task_key: str
    provider: str | None
    profile: str | None
    requested_model: str | None
    relative_path: str
    usage: AttemptUsage
    created_at: str


def _optional_json(path: Path) -> Any | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _project_units(project: Path, *, book: dict | None = None, plan: list | None = None) -> dict[str, tuple[str | None, str | None, str | None, int | None]]:
    result: dict[str, tuple[str | None, str | None, str | None, int | None]] = {}
    book_value = book if book is not None else _optional_json(project / "book.json")
    book = book_value if isinstance(book_value, dict) else {}
    for order, chunk in enumerate(book.get("chunks") or (), 1):
        if not isinstance(chunk, dict) or not isinstance(chunk.get("id"), str):
            continue
        unit_id = chunk["id"]
        result[unit_id] = (
            chunk.get("chapter_id"), unit_id, None,
            _integer(chunk.get("number")) or order,
        )
    plan_value = plan if plan is not None else _optional_json(project / "analysis_plan.json")
    plan_rows = plan_value if isinstance(plan_value, list) else []
    for order, unit in enumerate(plan_rows, 1):
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str):
            continue
        unit_id = unit["id"]
        result[unit_id] = (unit.get("chapter_id"), None, unit_id, order)
    return result


def _legacy_unit(task_key: str, pass_no: int) -> tuple[str, str | None, str | None, str | None]:
    match = _TASK_KEY.fullmatch(task_key)
    unit_id = match.group(2) if match else task_key
    if pass_no == 1:
        chapter = _ANALYSIS_ID.fullmatch(unit_id)
        return unit_id, chapter.group(1) if chapter else None, None, unit_id if chapter else None
    chunk = _CHUNK_ID.fullmatch(unit_id)
    return unit_id, chunk.group(1) if chunk else None, unit_id if chunk else None, None


def _preflight(manifest: dict[str, Any], response: dict[str, Any], context: dict[str, Any]) -> PreflightInput | None:
    value = manifest.get("preflight_input") or response.get("input_preflight")
    if isinstance(value, dict) and _integer(value.get("value")) is not None:
        return PreflightInput(
            value=value["value"], unit=str(value.get("unit") or "unknown"),
            quality=str(value.get("quality") or "unknown"),
            method=str(value.get("method") or "unknown"),
        )
    if _integer(context.get("input_tokens")) is not None:
        return PreflightInput(
            context["input_tokens"], "tokens", str(context.get("quality") or "unknown"),
            str(context.get("method") or "unknown"),
        )
    if _integer(context.get("input_upper_bound")) is not None:
        return PreflightInput(
            context["input_upper_bound"], "utf8_bytes", "conservative_upper_bound",
            str(context.get("method") or "UTF-8 byte upper bound"),
        )
    return None


def _cost(pricing: dict[str, Any] | None, usage: dict[str, Any] | None) -> CostEstimate | None:
    if pricing is None:
        return None
    priced = pricing
    if not isinstance(pricing.get("estimate"), dict) and usage is not None:
        priced = apply_estimate(pricing, usage)
    estimate = priced.get("estimate")
    if isinstance(estimate, dict):
        return CostEstimate(
            status=str(estimate.get("status") or "unknown"), amount=_number(estimate.get("amount")),
            currency=estimate.get("currency"), estimate_type=estimate.get("type"),
            note=priced.get("note"),
        )
    return CostEstimate(
        status=str(pricing.get("estimate_status") or "unknown"),
        estimate_type=pricing.get("estimate_type"), note=pricing.get("note"),
    )


def _recovery_sources(project: Path) -> set[str]:
    sources: set[str] = set()
    for path in project.glob("artifacts/**/recovery.json"):
        value = _optional_json(path)
        if not isinstance(value, dict):
            continue
        source = value.get("source_attempt") if value else None
        if isinstance(source, str):
            sources.add(source)
    return sources


def _aggregate(attempts: Iterable[AttemptUsage], field: str, *, elapsed: bool = False) -> UsageAggregate:
    known: list[int | float] = []
    unknown = 0
    for attempt in attempts:
        if attempt.provider_contacted is False:
            continue
        value = getattr(attempt, field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            known.append(value)
        else:
            unknown += 1
    total: int | float | None
    if known:
        total = sum(known)
        if elapsed:
            total = round(float(total), 3)
    elif unknown:
        total = None
    else:
        total = 0.0 if elapsed else 0
    return UsageAggregate(total, len(known), unknown)


def _aggregate_cost(attempts: Iterable[AttemptUsage]) -> CostEstimate | None:
    eligible = [attempt for attempt in attempts if attempt.provider_contacted is not False]
    costs = [attempt.cost for attempt in eligible if attempt.cost is not None]
    if not costs:
        return None
    known = [cost for cost in costs if cost.amount is not None]
    identities = {(cost.currency, cost.estimate_type) for cost in known}
    if known and len(known) == len(eligible) and len(identities) == 1:
        currency, estimate_type = next(iter(identities))
        return CostEstimate("complete", sum(cost.amount or 0.0 for cost in known), currency, estimate_type,
                            "Estimate aggregated across physical attempts; not a provider invoice.")
    return CostEstimate("partial" if known else "unknown",
                        sum(cost.amount or 0.0 for cost in known) if known else None,
                        known[0].currency if known else None,
                        known[0].estimate_type if known else None,
                        "Some physical attempts have unknown pricing or usage.")


def usage_by_unit_report(project: Path, unit_filter: str | None = None, *,
                         book: dict | None = None, plan: list | None = None) -> UsageByUnitResult:
    """Read evidence internally and return immutable semantic usage values."""
    project = project.resolve()
    known_units = _project_units(project, book=book, plan=plan)
    recovered_sources = _recovery_sources(project)
    records: list[_AttemptRecord] = []
    for manifest_path in sorted(project.glob("artifacts/**/attempt_*/attempt.json")):
        manifest = _optional_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        identity = manifest.get("identity") if isinstance(manifest.get("identity"), dict) else {}
        raw_pass = identity.get("pass_no", identity.get("pass"))
        try:
            pass_no = int(raw_pass)
        except (TypeError, ValueError):
            continue
        task_key = identity.get("task_key")
        if pass_no not in range(1, 6) or not isinstance(task_key, str):
            continue
        fallback_unit, fallback_chapter, fallback_chunk, fallback_analysis = _legacy_unit(task_key, pass_no)
        unit_id = identity.get("unit_id") or identity.get("chunk_id") or identity.get("analysis_unit_id") or fallback_unit
        if not isinstance(unit_id, str) or (unit_filter is not None and unit_id != unit_filter):
            continue
        mapped = known_units.get(unit_id, (None, None, None, None))
        chapter_id = identity.get("chapter_id") or mapped[0] or fallback_chapter
        chunk_id = identity.get("chunk_id") or mapped[1] or fallback_chunk
        analysis_unit_id = identity.get("analysis_unit_id") or mapped[2] or fallback_analysis
        unit_index = _integer(identity.get("unit_index")) or mapped[3]
        lifecycle = manifest.get("lifecycle") if isinstance(manifest.get("lifecycle"), dict) else {}
        response_value = _optional_json(manifest_path.parent / "response_meta.json")
        response = response_value if isinstance(response_value, dict) else {}
        context_value = _optional_json(manifest_path.parent / "context.json")
        context = context_value if isinstance(context_value, dict) else {}
        usage_json = _optional_json(manifest_path.parent / "usage.json")
        pricing = _optional_json(manifest_path.parent / "pricing.json")
        usage_json = usage_json if isinstance(usage_json, dict) else None
        pricing = pricing if isinstance(pricing, dict) else None
        generation = str(lifecycle.get("generation") or "unknown")
        validation = str(lifecycle.get("validation") or "unknown")
        acceptance = str(lifecycle.get("acceptance") or "not_accepted")
        reported = usage_json is not None and usage_json.get("status") == "reported"
        if reported:
            provider_contacted: bool | None = True
            usage_status = "reported"
        elif response.get("status") == "preflight_failed":
            provider_contacted = False
            usage_status = "failed_before_submission"
        elif generation in {"completed", "failed"}:
            provider_contacted = True
            usage_status = "unavailable"
        elif generation == "not_run":
            provider_contacted = False
            usage_status = "failed_before_submission"
        else:
            provider_contacted = None
            usage_status = "incomplete"
        if reported:
            pass
        elif lifecycle.get("evidence") == "incomplete" or manifest.get("evidence_complete") is False:
            usage_status = "incomplete"
        elif provider_contacted is True:
            usage_status = str((usage_json or {}).get("status") or "unavailable")
        relative = str(manifest_path.parent.relative_to(project))
        attempt_number = _integer(identity.get("attempt_number"))
        if attempt_number is None:
            match = re.fullmatch(r"attempt_(\d+)", manifest_path.parent.name)
            attempt_number = int(match.group(1)) if match else None
        attempt_id = str(identity.get("attempt_id") or f"{task_key}:{relative}")
        is_recovery_source = relative in recovered_sources
        values = usage_json if reported else {}
        attempt = AttemptUsage(
            attempt_id=attempt_id, attempt_number=attempt_number,
            generation_status=generation, validation_status=validation, acceptance_status=acceptance,
            provider_contacted=provider_contacted,
            accepted_checkpoint=acceptance == "checkpointed" or is_recovery_source,
            checkpoint_source_for_recovery=is_recovery_source,
            usage_status=usage_status, usage_source=(usage_json or {}).get("source"),
            reported_model=response.get("reported_model") or response.get("model"),
            input_tokens=_integer(values.get("input_tokens")),
            cached_input_tokens=_integer(values.get("cached_input_tokens")),
            cache_write_input_tokens=_integer(values.get("cache_write_input_tokens")),
            output_tokens=_integer(values.get("output_tokens")),
            reasoning_output_tokens=_integer(values.get("reasoning_output_tokens")),
            total_tokens=_integer(values.get("total_tokens")),
            elapsed_seconds=_number(response.get("elapsed_seconds")),
            preflight_input=_preflight(manifest, response, context), cost=_cost(pricing, usage_json),
        )
        records.append(_AttemptRecord(
            unit_id, chapter_id, chunk_id, analysis_unit_id, unit_index, pass_no, task_key,
            identity.get("provider"), identity.get("profile"), identity.get("requested_model"),
            relative, attempt, str(lifecycle.get("accepted_at") or lifecycle.get("terminal_at") or lifecycle.get("created_at") or ""),
        ))

    pass_groups: dict[tuple[Any, ...], list[_AttemptRecord]] = {}
    for record in records:
        key = (record.unit_id, record.pass_no, record.task_key, record.provider,
               record.profile, record.requested_model)
        pass_groups.setdefault(key, []).append(record)
    unit_passes: dict[str, list[tuple[_AttemptRecord, PassUsage]]] = {}
    for rows in pass_groups.values():
        rows.sort(key=lambda row: (row.created_at, row.usage.attempt_number or 0, row.usage.attempt_id))
        attempts = tuple(row.usage for row in rows)
        accepted = [row for row in rows if row.usage.accepted_checkpoint]
        recovered = any(row.usage.checkpoint_source_for_recovery for row in rows)
        submitted = [attempt for attempt in attempts if attempt.provider_contacted is True]
        unknown_submission = [attempt for attempt in attempts if attempt.provider_contacted is None]
        reported_count = sum(attempt.usage_status == "reported" for attempt in submitted)
        if not submitted and unknown_submission:
            usage_status = "incomplete"
        elif not submitted:
            usage_status = "failed_before_submission"
        elif reported_count == len(submitted):
            usage_status = "reported"
        elif reported_count:
            usage_status = "partial"
        elif any(attempt.usage_status == "incomplete" for attempt in submitted):
            usage_status = "incomplete"
        else:
            usage_status = "unavailable"
        sources = {attempt.usage_source for attempt in attempts if attempt.usage_source}
        reported_models = {attempt.reported_model for attempt in attempts if attempt.reported_model}
        preflight_attempts = [attempt for attempt in attempts if attempt.preflight_input is not None]
        accepted_preflight = [row.usage for row in accepted if row.usage.preflight_input is not None]
        first = rows[0]
        pass_usage = PassUsage(
            pass_no=first.pass_no, task_key=first.task_key, provider=first.provider,
            profile=first.profile, requested_model=first.requested_model,
            reported_model=(next(iter(reported_models)) if len(reported_models) == 1 else
                            ("multiple" if reported_models else None)),
            physical_attempt_count=len(attempts), provider_call_count=len(submitted),
            unknown_provider_call_count=len(unknown_submission),
            retry_count=max(0, len(attempts) - 1),
            failed_attempt_count=sum(
                attempt.generation_status == "failed" or attempt.validation_status == "failed"
                for attempt in attempts
            ),
            failed_before_submission_count=sum(attempt.provider_contacted is False for attempt in attempts),
            accepted_attempt_id=accepted[-1].usage.attempt_id if accepted else None,
            recovered_from_existing_attempt=recovered,
            result_status="recovered_without_new_call" if recovered else (
                "checkpointed" if accepted else "not_checkpointed"
            ),
            usage_status=usage_status,
            usage_source=next(iter(sources)) if len(sources) == 1 else ("multiple" if sources else None),
            preflight_input=(accepted_preflight[-1].preflight_input if accepted_preflight else
                             (preflight_attempts[-1].preflight_input if preflight_attempts else None)),
            input_tokens=_aggregate(attempts, "input_tokens"),
            cached_input_tokens=_aggregate(attempts, "cached_input_tokens"),
            cache_write_input_tokens=_aggregate(attempts, "cache_write_input_tokens"),
            output_tokens=_aggregate(attempts, "output_tokens"),
            reasoning_output_tokens=_aggregate(attempts, "reasoning_output_tokens"),
            total_tokens=_aggregate(attempts, "total_tokens"),
            elapsed_seconds=_aggregate(attempts, "elapsed_seconds", elapsed=True),
            cost=_aggregate_cost(attempts), attempts=attempts,
        )
        unit_passes.setdefault(first.unit_id, []).append((first, pass_usage))

    units: list[UnitUsage] = []
    for unit_id, rows in unit_passes.items():
        rows.sort(key=lambda item: (item[1].pass_no, item[1].task_key, item[1].provider or "",
                                    item[1].requested_model or ""))
        identity = rows[0][0]
        passes = tuple(item[1] for item in rows)
        attempts = tuple(attempt for pass_usage in passes for attempt in pass_usage.attempts)
        units.append(UnitUsage(
            unit_id=unit_id, chapter_id=identity.chapter_id, chunk_id=identity.chunk_id,
            analysis_unit_id=identity.analysis_unit_id, unit_index=identity.unit_index,
            passes=passes,
            input_tokens=_aggregate(attempts, "input_tokens"),
            cached_input_tokens=_aggregate(attempts, "cached_input_tokens"),
            cache_write_input_tokens=_aggregate(attempts, "cache_write_input_tokens"),
            output_tokens=_aggregate(attempts, "output_tokens"),
            reasoning_output_tokens=_aggregate(attempts, "reasoning_output_tokens"),
            total_tokens=_aggregate(attempts, "total_tokens"),
            elapsed_seconds=_aggregate(attempts, "elapsed_seconds", elapsed=True),
        ))
    units.sort(key=lambda unit: (unit.unit_index is None, unit.unit_index or 0, unit.unit_id))
    return UsageByUnitResult(
        scope=("Historical physical attempts that contacted a provider are summed, including failed "
               "attempts with reported usage. Preflight failures and checkpoint reuse consume no model usage."),
        units=tuple(units),
        warning=("Unknown measurements remain null and carry explicit unknown_attempts coverage; totals across "
                 "different models/tokenizers are accounting totals, not comparable text quantities."),
    )
