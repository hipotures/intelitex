from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import PipelineError, atomic_json, digest, read_json


CATALOG_SCHEMA_VERSION = 1


def bundled_catalog_path() -> Path:
    return Path(__file__).resolve().parent.parent / "catalog" / "models.json"


def validate_catalog(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise PipelineError(f"Catalog schema_version must be {CATALOG_SCHEMA_VERSION}.")
    models = value.get("models")
    if not isinstance(models, list):
        raise PipelineError("Catalog models must be an array.")
    seen: set[tuple[str, str]] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise PipelineError(f"Catalog model {index} must be an object.")
        unknown = set(model) - {"provider", "id", "context_tokens", "efforts", "pricing", "capabilities"}
        if unknown:
            raise PipelineError(f"Unknown catalog model fields: {sorted(unknown)}")
        provider, model_id = model.get("provider"), model.get("id")
        if provider not in {"llamacpp", "openai", "codex"} or not isinstance(model_id, str) or not model_id:
            raise PipelineError(f"Catalog model {index} has invalid provider/id.")
        if (provider, model_id) in seen:
            raise PipelineError(f"Duplicate catalog model: {provider}/{model_id}")
        seen.add((provider, model_id))
        context = model.get("context_tokens")
        if context is not None and (not isinstance(context, int) or context <= 0):
            raise PipelineError(f"Invalid context_tokens for {provider}/{model_id}.")
        efforts = model.get("efforts", [])
        if not isinstance(efforts, list) or any(not isinstance(x, str) or not x for x in efforts):
            raise PipelineError(f"Invalid efforts for {provider}/{model_id}.")
        pricing = model.get("pricing")
        if pricing is not None:
            if not isinstance(pricing, dict):
                raise PipelineError(f"Invalid pricing for {provider}/{model_id}.")
            allowed = {"currency", "unit", "input", "cached_input", "cache_write_input", "output", "reasoning_output", "estimate_type", "source"}
            if set(pricing) - allowed:
                raise PipelineError(f"Unknown pricing fields for {provider}/{model_id}: {sorted(set(pricing)-allowed)}")
            if pricing.get("currency") is None or pricing.get("unit") != "million_tokens":
                raise PipelineError(f"Pricing for {provider}/{model_id} needs currency and unit=million_tokens.")
            for key in ("input", "cached_input", "cache_write_input", "output", "reasoning_output"):
                rate = pricing.get(key)
                if rate is not None and (not isinstance(rate, (int, float)) or rate < 0):
                    raise PipelineError(f"Invalid {key} rate for {provider}/{model_id}.")
    return value


def load_catalog(project: Path | None = None) -> tuple[dict[str, Any], Path]:
    custom = project / "catalog" / "models.json" if project else None
    path = custom if custom and custom.is_file() else bundled_catalog_path()
    return validate_catalog(read_json(path)), path


def import_catalog(source: Path, project: Path) -> dict[str, Any]:
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Cannot read catalog import: {exc}") from exc
    validate_catalog(value)
    value = dict(value)
    value["activated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    value["import"] = {"source": str(source.resolve()), "sha256": digest(source.read_bytes())}
    destination = project / "catalog" / "models.json"
    atomic_json(destination, value)
    return {"path": str(destination), "models": len(value["models"]), "sha256": digest(destination.read_bytes())}


def model_entry(catalog: dict[str, Any], provider: str, model: str | None) -> dict[str, Any] | None:
    if not model:
        return None
    return next((x for x in catalog["models"] if x["provider"] == provider and x["id"] == model), None)


def pricing_snapshot(catalog: dict[str, Any], path: Path, provider: str, model: str | None) -> dict[str, Any]:
    entry = model_entry(catalog, provider, model)
    pricing = entry.get("pricing") if entry else None
    return {
        "catalog_schema_version": catalog["schema_version"],
        "catalog_source": str(path),
        "catalog_sha256": digest(path.read_bytes()),
        "provider": provider,
        "model": model,
        "rate": pricing,
        "estimate_status": "available" if pricing else "unknown",
        "estimate_type": (pricing or {}).get("estimate_type"),
        "note": "Codex API-equivalent estimates are estimates, not provider-reported charges or subscription invoices."
                if provider == "codex" else None,
    }


def apply_estimate(snapshot: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    value = dict(snapshot)
    rate = value.get("rate")
    if not rate or usage.get("status") != "reported":
        value["estimate"] = {"status": "unknown", "reason": "pricing or measured usage unavailable"}
        return value
    categories = {
        "input": usage.get("input_tokens"),
        "cached_input": usage.get("cached_input_tokens"),
        "cache_write_input": usage.get("cache_write_input_tokens"),
        "output": usage.get("output_tokens"),
        "reasoning_output": usage.get("reasoning_output_tokens"),
    }
    if isinstance(categories["input"], int):
        categories["input"] = max(0, categories["input"] - (categories["cached_input"] or 0) - (categories["cache_write_input"] or 0))
    if rate.get("reasoning_output") is not None and isinstance(categories["output"], int):
        categories["output"] = max(0, categories["output"] - (categories["reasoning_output"] or 0))
    components, unknown, total = {}, [], 0.0
    for name, tokens in categories.items():
        if not tokens:
            continue
        price = rate.get(name)
        if price is None:
            unknown.append(name)
            continue
        amount = tokens * float(price) / 1_000_000
        components[name] = {"tokens": tokens, "rate_per_million": price, "amount": amount}
        total += amount
    value["estimate"] = {
        "status": "complete" if not unknown else "partial",
        "type": rate.get("estimate_type") or ("codex_api_equivalent" if value.get("provider") == "codex" else "openai_api_estimate"),
        "currency": rate.get("currency"), "amount": total, "components": components,
        "unknown_components": unknown, "provider_reported_charge": None,
    }
    return value
