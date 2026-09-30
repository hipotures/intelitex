"""Freeze one saved P2 task, run independent P2-P5 variants, report real usage.

Run with python -m experiments.codex_cache_ab --help from the repository.
Prepare and report are offline; run explicitly invokes the configured Codex.
The production project is read only. No browser or downloads are involved.
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

from bookpipe.codex_cache import COMMON_FIELDS, RETRY_FIELDS
from bookpipe.codex_transport import CodexAppServerClient
from bookpipe.engine import Runner
from bookpipe.profiles import resolve_profile, with_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, atomic_text, digest, read_json


def prepare(project: Path, chunk_id: str, scratch: Path, profile_name: str) -> dict:
    project, scratch = project.resolve(), scratch.resolve()
    if scratch.is_relative_to(project) or project.is_relative_to(scratch):
        raise PipelineError("A/B scratch must be outside the production project and cannot contain it.")
    if not chunk_id or Path(chunk_id).name != chunk_id or chunk_id in {".", ".."}:
        raise PipelineError("chunk-id must be one plain source chunk identifier.")
    candidates = []
    for path in (project / "artifacts" / "pass2" / chunk_id).glob("*/attempt_*/request.semantic.json"):
        manifest_path = path.parent / "attempt.json"
        if manifest_path.is_file() and read_json(manifest_path).get("lifecycle", {}).get("acceptance") == "checkpointed":
            candidates.append(path)
    if not candidates:
        raise PipelineError("No checkpoint-producing canonical P2 semantic request found for this chunk.")
    selected = max(candidates, key=lambda path: path.parent.stat().st_mtime)
    semantic = read_json(selected)
    inputs = copy.deepcopy(semantic["input_payload"])
    for field in RETRY_FIELDS:
        inputs.pop(field, None)
    if inputs.get("CHUNK_ID") != chunk_id or set(inputs) != set(COMMON_FIELDS) | {"SOURCE_SENTENCES"}:
        raise PipelineError("Saved P2 request has unexpected semantic inputs.")
    settings, _ = with_profiles(read_json(project / "settings.json"))
    _, profile, provenance = resolve_profile(settings, 2, command_profile=profile_name, project=project)
    if profile["provider"] != "codex":
        raise PipelineError("A/B requires a Codex profile.")
    prompts = {str(n): (project / "prompts" / f"pass{n}.txt").read_text(encoding="utf-8") for n in range(1, 6)}
    # Both variants use this single frozen configuration, including retry policy,
    # model, effort, memory, context, source, and canonical prompt definitions.
    frozen = {
        "chunk_id": chunk_id, "p2_inputs": inputs, "prompts": prompts,
        "profile": profile, "profile_name": profile_name, "selection_provenance": provenance,
        "runner_settings": {"passes": settings["passes"], "json_retries": settings.get("json_retries", 1)},
        "source_request": str(selected), "source_request_sha256": digest(selected.read_bytes()),
    }
    scratch.mkdir(parents=True, exist_ok=False, mode=0o700)
    atomic_json(scratch / "frozen.json", frozen)
    atomic_json(scratch / "manifest.json", {"frozen_sha256": digest(frozen)})
    return {"scratch": str(scratch), "chunk_id": chunk_id, "model": profile["model"],
            "effort": profile.get("reasoning_effort"), "source_request": str(selected)}


def load_frozen(scratch: Path) -> dict:
    value = read_json(scratch / "frozen.json")
    if digest(value) != read_json(scratch / "manifest.json")["frozen_sha256"]:
        raise PipelineError("Frozen A/B inputs/configuration changed; prepare a new experiment.")
    return value


def run(scratch: Path, wire: str) -> dict:
    if wire not in ("canonical", "cache-v1"):
        raise PipelineError(f"Unknown A/B wire format: {wire!r}")
    scratch = scratch.resolve()
    frozen = load_frozen(scratch)
    root = scratch / wire
    root.mkdir(mode=0o700, exist_ok=False)
    for n, prompt in frozen["prompts"].items():
        atomic_text(root / "prompts" / f"pass{n}.txt", prompt)
    profile = copy.deepcopy(frozen["profile"])
    profile.setdefault("options", {})["translation_wire_format"] = wire
    selected = {**profile, "profile_name": frozen["profile_name"], "resolved_profile": copy.deepcopy(profile),
                "project_root": str(root), "runtime_root": str(root / "runtimes")}
    provider = CodexAppServerClient(selected, Display(True))
    store = Store(root)
    cid = frozen["chunk_id"]
    store.register_chunks({"chunks": [{"id": cid}]})
    runner = Runner(store, provider, frozen["runner_settings"], Display(True))
    common = {name: frozen["p2_inputs"][name] for name in COMMON_FIELDS}
    sentences = frozen["p2_inputs"]["SOURCE_SENTENCES"]
    started = time.monotonic()
    try:
        p2, _, _ = runner.run(2, f"pass2/{cid}", {**common, "SOURCE_SENTENCES": sentences})
        p3, _, _ = runner.run(3, f"pass3/{cid}", {**common, "SEMANTIC_AUDIT": p2})
        p4, _, _ = runner.run(4, f"pass4/{cid}", {**common, "POLISH_DRAFT": p3,
                                               "SEMANTIC_AUDIT": p2, "SOURCE_SENTENCES": sentences})
        runner.run(5, f"pass5/{cid}", {**common, "POLISH_DRAFT": p3, "CORRECTION_LEDGER": p4})
        timing = {"wall_time_seconds": round(time.monotonic() - started, 3), "status": "completed"}
    except BaseException as exc:
        atomic_json(root / "run.json", {"wall_time_seconds": round(time.monotonic() - started, 3),
                                         "status": "failed", "error": str(exc)})
        raise
    finally:
        provider.close()
        store.close()
    atomic_json(root / "run.json", timing)
    return timing


USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")


def _sum_reported(rows: list[dict], field: str) -> int | float | None:
    values = [row.get(field) for row in rows]
    # Missing provider fields remain unknown, rather than becoming invented zeroes.
    return sum(values) if values and all(isinstance(value, (int, float)) for value in values) else None


def report(scratch: Path) -> dict:
    frozen = load_frozen(scratch)
    result = {"chunk_id": frozen["chunk_id"], "model": frozen["profile"]["model"],
              "effort": frozen["profile"].get("reasoning_effort"), "variants": {}}
    for wire in ("canonical", "cache-v1"):
        root = scratch / wire
        if not root.exists():
            continue
        passes = []
        attempts = []
        for n in range(2, 6):
            current = []
            for path in sorted((root / "artifacts" / f"pass{n}").glob("*/**/attempt_*/attempt.json")):
                directory = path.parent
                metadata = read_json(directory / "response_meta.json")
                usage = read_json(directory / "usage.json") if (directory / "usage.json").exists() else {}
                current.append({"pass_no": n, "attempt": str(directory.relative_to(scratch)),
                                **{field: usage.get(field) for field in USAGE_FIELDS},
                                "elapsed_seconds": metadata.get("elapsed_seconds"),
                                "cache_prefix_sha256": metadata.get("cache_prefix_sha256"),
                                "checkpointed": read_json(path).get("lifecycle", {}).get("acceptance") == "checkpointed"})
            attempts.extend(current)
            passes.append({"pass_no": n, "attempts": current,
                           **{field: _sum_reported(current, field) for field in (*USAGE_FIELDS, "elapsed_seconds")}})
        totals = {field: _sum_reported(attempts, field) for field in USAGE_FIELDS}
        ratio = (totals["cached_input_tokens"] / totals["input_tokens"]
                 if totals["input_tokens"] and totals["cached_input_tokens"] is not None else None)
        timing = read_json(root / "run.json") if (root / "run.json").exists() else {}
        result["variants"][wire] = {"passes": passes, "totals": totals, "cache_read_ratio": ratio,
                                     "wall_time_seconds": timing.get("wall_time_seconds"), "status": timing.get("status")}
    atomic_json(scratch / "report.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--project", type=Path, required=True)
    prep.add_argument("--chunk-id", required=True)
    prep.add_argument("--scratch", type=Path, required=True)
    prep.add_argument("--profile", default="codex-sol-low")
    live = sub.add_parser("run")
    live.add_argument("--scratch", type=Path, required=True)
    live.add_argument("--wire", choices=("canonical", "cache-v1"), required=True)
    saved = sub.add_parser("report")
    saved.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.project, args.chunk_id, args.scratch, args.profile)
        elif args.command == "run":
            result = run(args.scratch, args.wire)
        else:
            result = report(args.scratch)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, OSError, KeyError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
