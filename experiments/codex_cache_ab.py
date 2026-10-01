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
import jsonschema
from pathlib import Path

from bookpipe.codex_cache import COMMON_FIELDS, RETRY_FIELDS
from bookpipe.codex_transport import CodexAppServerClient
from bookpipe import codex_cache_v2 as v2
from bookpipe import codex_cache_shared as shared, p1_compact as p1
from bookpipe.engine import Runner, response_schema, validate_result
from bookpipe.codex_cache import developer_contract as v1_developer, serialize as v1_serialize, TRANSPORT_SCHEMA as V1_SCHEMA, encode_value, decode_output as v1_decode
from bookpipe.evidence import utc_now
from datetime import datetime
from bookpipe.profiles import resolve_profile, with_profiles
from bookpipe.store import Store
from bookpipe.ui import Display
from bookpipe.util import PipelineError, atomic_json, atomic_text, digest, read_json


def prepare(project: Path, chunk_id: str, scratch: Path, profile_name: str, pass_no: int | None = None) -> dict:
    project, scratch = project.resolve(), scratch.resolve()
    repository = Path(__file__).resolve().parents[1]
    if any(scratch.is_relative_to(root) or root.is_relative_to(scratch) for root in (project, repository)):
        raise PipelineError("A/B scratch must be outside the production project and repository and cannot contain them.")
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
    saved_tasks = {}
    for n in range(2, 6):
        paths = [p for p in (project / "artifacts" / f"pass{n}" / chunk_id).glob("*/attempt_*/request.semantic.json")
                 if (p.parent / "attempt.json").is_file()
                 and read_json(p.parent / "attempt.json").get("lifecycle", {}).get("acceptance") == "checkpointed"]
        if not paths:
            continue
        path = max(paths, key=lambda p: p.parent.stat().st_mtime)
        task = read_json(path)
        clean = copy.deepcopy(task["input_payload"])
        for key in RETRY_FIELDS:
            clean.pop(key, None)
        result = read_json(path.parent.parent / "result.json")
        validate_result(n, result, clean)
        if task["trusted_instructions"] != prompts[str(n)]:
            raise PipelineError(f"Saved P{n} prompt differs from current project definition; use the original project prompt snapshot.")
        saved_tasks[str(n)] = {"inputs": clean, "result": result, "source_attempt": str(path.parent)}
    if pass_no is not None and (pass_no not in (2, 3, 4, 5) or str(pass_no) not in saved_tasks):
        raise PipelineError("Requested saved same-task pass is unavailable; prepare from an accepted attempt.")
    # Both variants use this single frozen configuration, including retry policy,
    # model, effort, memory, context, source, and canonical prompt definitions.
    frozen = {
        "chunk_id": chunk_id, "p2_inputs": inputs, "prompts": prompts,
        "saved_tasks": saved_tasks, "pass_no": pass_no,
        "profile": profile, "profile_name": profile_name, "selection_provenance": provenance,
        "runner_settings": {"passes": settings["passes"], "json_retries": settings.get("json_retries", 1)},
        "source_request": str(selected), "source_request_sha256": digest(selected.read_bytes()),
    }
    scratch.mkdir(parents=True, exist_ok=False, mode=0o700)
    atomic_json(scratch / "frozen.json", frozen)
    atomic_json(scratch / "manifest.json", {"frozen_sha256": digest(frozen)})
    return {"scratch": str(scratch), "chunk_id": chunk_id, "pass_no": pass_no, "model": profile["model"],
            "effort": profile.get("reasoning_effort"), "source_request": str(selected)}


def load_frozen(scratch: Path) -> dict:
    value = read_json(scratch / "frozen.json")
    if digest(value) != read_json(scratch / "manifest.json")["frozen_sha256"]:
        raise PipelineError("Frozen A/B inputs/configuration changed; prepare a new experiment.")
    return value


def run(scratch: Path, wire: str) -> dict:
    if wire not in ("canonical", "cache-v1", "cache-v2"):
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
    started_at = utc_now()
    started = time.monotonic()
    try:
        if frozen.get("pass_no"):
            n = frozen["pass_no"]
            runner.run(n, f"pass{n}/{cid}", frozen["saved_tasks"][str(n)]["inputs"])
        else:
            p2, _, _ = runner.run(2, f"pass2/{cid}", {**common, "SOURCE_SENTENCES": sentences})
            p3, _, _ = runner.run(3, f"pass3/{cid}", {**common, "SEMANTIC_AUDIT": p2})
            p4, _, _ = runner.run(4, f"pass4/{cid}", {**common, "POLISH_DRAFT": p3,
                                                   "SEMANTIC_AUDIT": p2, "SOURCE_SENTENCES": sentences})
            runner.run(5, f"pass5/{cid}", {**common, "POLISH_DRAFT": p3, "CORRECTION_LEDGER": p4})
        timing = {"wall_time_seconds": round(time.monotonic() - started, 3), "status": "completed", "started_at": started_at, "completed_at": utc_now()}
    except BaseException as exc:
        atomic_json(root / "run.json", {"wall_time_seconds": round(time.monotonic() - started, 3),
                                         "status": "failed", "started_at": started_at, "completed_at": utc_now(), "error": str(exc)})
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
    for wire in ("canonical", "cache-v1", "cache-v2"):
        root = scratch / wire
        if not root.exists():
            continue
        passes = []
        attempts = []
        for n in range(2, 6):
            current = []
            for path in sorted((root / "artifacts" / f"pass{n}").glob("*/**/attempt_*/attempt.json")):
                directory = path.parent
                metadata = read_json(directory / "response_meta.json") if (directory / "response_meta.json").exists() else {}
                usage = read_json(directory / "usage.json") if (directory / "usage.json").exists() else {}
                current.append({"pass_no": n, "attempt": str(directory.relative_to(scratch)),
                                **{field: usage.get(field) for field in USAGE_FIELDS},
                                "elapsed_seconds": metadata.get("elapsed_seconds"),
                                "requested_model": metadata.get("requested_model"), "reported_model": metadata.get("reported_model"),
                                "requested_effort": metadata.get("requested_effort"), "reported_effort": metadata.get("reported_effort"),
                                "validation": read_json(directory / "validation.json") if (directory / "validation.json").exists() else None,
                                "cache_prefix_sha256": metadata.get("cache_prefix_sha256"),
                                **event_timings(directory),
                                "reasoning_output_tokens": usage.get("reasoning_output_tokens"), "total_tokens": usage.get("total_tokens"),
                                "checkpointed": read_json(path).get("lifecycle", {}).get("acceptance") == "checkpointed"})
            attempts.extend(current)
            passes.append({"pass_no": n, "attempts": current,
                           **{field: _sum_reported(current, field) for field in (*USAGE_FIELDS, "elapsed_seconds")}})
        totals = {field: _sum_reported(attempts, field) for field in USAGE_FIELDS}
        ratio = (totals["cached_input_tokens"] / totals["input_tokens"]
                 if totals["input_tokens"] and totals["cached_input_tokens"] is not None else None)
        timing = read_json(root / "run.json") if (root / "run.json").exists() else {}
        result["variants"][wire] = {"passes": passes, "totals": totals, "cache_read_ratio": ratio,
                                     "all_usage_totals": {field: _sum_reported(attempts, field) for field in (*USAGE_FIELDS, "reasoning_output_tokens", "total_tokens")},
                                     "wall_time_seconds": timing.get("wall_time_seconds"), "status": timing.get("status"),
                                     "time_to_valid_result_seconds": timing.get("wall_time_seconds") if timing.get("status") == "completed" else None,
                                     "attempts_to_valid_result": len(attempts) if timing.get("status") == "completed" else None,
                                     "execution_mode": "independent isolated app-server root per inference"}
    timed = []
    for variant in result["variants"].values():
        for current in variant["passes"]:
            for row in current["attempts"]:
                row["start_to_previous_start_seconds"] = None
                row["previous_completion_to_start_seconds"] = None
                elapsed = row.get("elapsed_seconds")
                row["output_tokens_per_wall_second"] = row["output_tokens"] / elapsed if elapsed and row["output_tokens"] is not None else None
                if row["submitted_at"] is not None:
                    timed.append(row)
    timed.sort(key=lambda row: row["submitted_at"])
    for previous, row in zip(timed, timed[1:]):
        def seconds(end, start):
            return (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        row["start_to_previous_start_seconds"] = seconds(row["submitted_at"], previous["submitted_at"])
        if previous["terminal_at"] is not None:
            row["previous_completion_to_start_seconds"] = seconds(row["submitted_at"], previous["terminal_at"])
    atomic_json(scratch / "report.json", result)
    return result


def offline_sizes(scratch: Path) -> dict:
    """Encode the same accepted tasks/results; no auth, providers, or production writes."""
    frozen = load_frozen(scratch)
    tasks = frozen.get("saved_tasks", {})
    if not tasks:
        raise PipelineError("No saved tasks in this snapshot; prepare a fresh scratch directory.")
    prompts = {int(n): p for n, p in frozen["prompts"].items()}
    v1_contract = v1_developer(prompts)
    v2_contract = v2.developer_contract({n: prompts[n] for n in range(2, 6)})
    rows = []
    for key, task in sorted(tasks.items()):
        n, inputs, output = int(key), task["inputs"], task["result"]
        schema = response_schema(n, inputs)
        jsonschema.Draft202012Validator(schema).validate(output)
        validate_result(n, output, inputs)
        layout, context = v2.encode_input(inputs, n)
        if v2.decode_input(layout, context) != inputs:
            raise PipelineError(f"P{n} compact input round trip failed.")
        compact_output = v2.encode_output(output, n, context)
        if v2.decode_output(compact_output, n, context) != output:
            raise PipelineError(f"P{n} compact output round trip failed.")
        wire = json.loads(layout.text)
        def size(v): return len(encode_value(v).encode("utf-8"))
        variants = {
            "canonical": (encode_value(inputs), prompts[n], schema, output, inputs),
            "cache-v1": (v1_serialize(inputs, schema, n).text, v1_contract, V1_SCHEMA,
                         {"payload_json": encode_value(output)}, inputs),
            "cache-v2": (layout.text, v2_contract, v2.TRANSPORT_SCHEMA, compact_output, wire),
        }
        for version, (text, developer, transport_schema, result, payload) in variants.items():
            if version == "canonical":
                valid = v2.parse_output(text) == inputs and v2.parse_output(encode_value(result)) == output
            elif version == "cache-v1":
                decoded = v2.parse_output(text)
                for k in ("CACHE_V1", "CANONICAL_OUTPUT_SCHEMA", "ACTIVE_PASS"):
                    decoded.pop(k)
                valid = decoded == inputs and v1_decode(result) == output
            else:
                valid = v2.decode_input(layout, context) == inputs and v2.decode_output(result, n, context) == output
            if not valid:
                raise PipelineError(f"P{n} {version} round trip failed.")
            row = {"pass_no": n, "wire_format": version, "input_utf8_bytes": len(text.encode("utf-8")),
                   "developer_utf8_bytes": len(developer.encode("utf-8")), "schema_utf8_bytes": size(transport_schema),
                   "output_utf8_bytes": size(result), "source_text_utf8_bytes": sum(len(b["text"].encode("utf-8")) for b in inputs["SOURCE_BLOCKS"]),
                   "source_records_utf8_bytes": size(payload["SOURCE_BLOCKS"]),
                   "memory_utf8_bytes": sum(size(payload[k]) for k in ("APPROVED_LEXICON", "OBSERVATIONS", "PREVIOUS_CONTEXT")),
                   "lookup_utf8_bytes": size(payload.get("SOURCE_LOOKUP", {})) if version == "cache-v2" else 0,
                   "roundtrip_valid": valid}
            row["total_request_component_utf8_bytes"] = row["input_utf8_bytes"] + row["developer_utf8_bytes"] + row["schema_utf8_bytes"]
            rows.append(row)
    result = {"chunk_id": frozen["chunk_id"], "method": "Offline deterministic UTF-8 bytes, not model tokens or latency", "rows": rows,
              "provider_cache_hits_measured": False, "latency_measured": False, "output_quality_measured": False,
              "retry_rate_measured": False}
    atomic_json(scratch / "sizes.json", result)
    return result


def shared_sizes(project: Path, chunk_id: str, analysis_unit: str, scratch: Path) -> dict:
    """Read accepted tasks only; compare old/shared layouts without model calls."""
    project, scratch = project.resolve(), scratch.resolve()
    repository = Path(__file__).resolve().parents[1]
    if any(scratch.is_relative_to(root) or root.is_relative_to(scratch) for root in (project, repository)):
        raise PipelineError("Size-report scratch must be outside the project/repository and cannot contain them.")
    for name in (chunk_id, analysis_unit):
        if not name or Path(name).name != name or name in (".", ".."):
            raise PipelineError("Chunk/analysis identifiers must be plain names.")
    tasks, prompts = {}, {}
    for n in range(1, 6):
        unit = analysis_unit if n == 1 else chunk_id
        paths = [p for p in (project / "artifacts" / f"pass{n}" / unit).glob("*/attempt_*/request.semantic.json")
                 if (p.parent / "attempt.json").is_file()
                 and read_json(p.parent / "attempt.json").get("lifecycle", {}).get("acceptance") == "checkpointed"]
        if not paths:
            raise PipelineError(f"No accepted P{n} attempt for {unit}; no inference will be made.")
        path = max(paths, key=lambda p: p.parent.stat().st_mtime)
        semantic = read_json(path)
        result_path = path.parent.parent / "result.json"
        tasks[n] = {"inputs": semantic["input_payload"], "result": read_json(result_path),
                    "source_attempt": str(path.parent), "semantic_sha256": digest(path.read_bytes()),
                    "result_sha256": digest(result_path.read_bytes())}
        prompts[n] = semantic["trusted_instructions"]
    developer = shared.developer_contract(prompts)
    translation_developer = v2.developer_contract({n: prompts[n] for n in range(2, 6)})
    def size(value): return len(encode_value(value).encode("utf-8"))
    rows, prefixes = [], {}
    for n, task in tasks.items():
        inputs, result = task["inputs"], task["result"]
        jsonschema.validate(result, response_schema(n, inputs))
        validate_result(n, result, inputs)
        layout, ctx = shared.encode_input(inputs, n)
        prefixes[str(n)] = {"source": digest(layout.source_prefix),
                            "translation_common": digest(layout.translation_common_prefix) if n > 1 else None,
                            "draft": digest(layout.draft_prefix) if n in (4, 5) else None}
        compact = shared.encode_output(result, n, ctx)
        if shared.decode_input(layout, ctx) != inputs or shared.decode_output(compact, n, ctx) != result:
            raise PipelineError(f"P{n} cache-shared-v1 round trip failed.")
        rows.append({"pass_no": n, "wire_format": shared.WIRE_FORMAT,
                     "developer_utf8_bytes": len(developer.encode()), "schema_utf8_bytes": size(shared.TRANSPORT_SCHEMA),
                     "source_prefix_utf8_bytes": len(layout.source_prefix.encode()),
                     "translation_common_prefix_utf8_bytes": len(layout.translation_common_prefix.encode()) if n > 1 else None,
                     "translation_common_section_utf8_bytes": len(layout.translation_common_prefix.encode()) - len(layout.source_prefix.encode()) if n > 1 else None,
                     "input_utf8_bytes": len(layout.text.encode()), "output_utf8_bytes": size(compact), "roundtrip_valid": True})
        if n == 1:
            old = p1.build_transport(prompts[n], inputs)
            # compact-v1 has no leading source-only prefix: memory precedes it.
            old_row = {"wire_format": "compact-v1", "developer_utf8_bytes": len(old.developer_instructions.encode()),
                       "schema_utf8_bytes": size(old.output_schema), "source_prefix_utf8_bytes": None,
                       "translation_common_prefix_utf8_bytes": None, "translation_common_section_utf8_bytes": None,
                       "input_utf8_bytes": size(old.input_payload)}
            old_output = {"t": compact["a"], "o": compact["o"]}
            if old.decode(old_output) != result:
                raise PipelineError("P1 compact-v1 output round trip failed.")
        else:
            old, old_ctx = v2.encode_input(inputs, n)
            old_wire = json.loads(old.text)
            source_prefix = '{"CACHE_V2":1,' + ''.join(encode_value(k) + ':' + encode_value(old_wire[k]) + ',' for k in ("SOURCE_BLOCKS", "SOURCE_LOOKUP"))
            old_row = {"wire_format": "cache-v2", "developer_utf8_bytes": len(translation_developer.encode()),
                       "schema_utf8_bytes": size(v2.TRANSPORT_SCHEMA), "source_prefix_utf8_bytes": len(source_prefix.encode()),
                       "translation_common_prefix_utf8_bytes": len(old.common_prefix.encode()),
                       "translation_common_section_utf8_bytes": len(old.common_prefix.encode()) - len(source_prefix.encode()),
                       "input_utf8_bytes": len(old.text.encode())}
            old_output = v2.encode_output(result, n, old_ctx)
            if v2.decode_input(old, old_ctx) != inputs or v2.decode_output(old_output, n, old_ctx) != result:
                raise PipelineError(f"P{n} cache-v2 round trip failed.")
        rows.append({"pass_no": n, **old_row, "output_utf8_bytes": size(old_output), "roundtrip_valid": True})
    report = {"chunk_id": chunk_id, "analysis_unit_id": analysis_unit,
              "method": "Offline deterministic UTF-8 bytes, not model tokens, hits, cost or latency; prefixes include prior fields and trailing comma.",
              "note": "compact-v1 has no source-only leading prefix (null); translation common prefix includes source; section excludes source. Original retry data is retained.",
              "source_attempts": {str(n): {k: t[k] for k in ("source_attempt", "semantic_sha256", "result_sha256")} for n, t in tasks.items()},
              "rows": rows, "prefixes": prefixes,
              "source_prefix_equal_p1_p2": prefixes["1"]["source"] == prefixes["2"]["source"],
              "translation_common_equal_p2_p5": len({prefixes[str(n)]["translation_common"] for n in range(2, 6)}) == 1,
              "draft_prefix_equal_p4_p5": prefixes["4"]["draft"] == prefixes["5"]["draft"], "live_model_calls": 0}
    scratch.mkdir(parents=True, exist_ok=False, mode=0o700)
    atomic_json(scratch / "shared-sizes.json", report)
    atomic_json(scratch / "schema.cache-shared-v1.json", shared.TRANSPORT_SCHEMA)
    return report


def event_timings(directory: Path) -> dict:
    """Timing from saved RPC events; absent observations remain null."""
    path = directory / "transport.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    observed = {"submitted_at": None, "first_reasoning_at": None, "first_answer_delta_at": None,
                "terminal_at": None, "local_validation_started_at": None, "local_validation_completed_at": None}
    for row in rows:
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        method = payload.get("method", "")
        key = None
        if row.get("direction") == "outbound" and row.get("kind") == "rpc" and method == "turn/start":
            key = "submitted_at"
        elif row.get("direction") == "inbound":
            item = (payload.get("params") or {}).get("item") or {}
            if method.startswith("item/reasoning/") or (method == "item/started" and item.get("type") == "reasoning"):
                key = "first_reasoning_at"
            elif method == "item/agentMessage/delta":
                key = "first_answer_delta_at"
            elif method == "turn/completed":
                key = "terminal_at"
        elif row.get("kind") == "validation_started":
            key = "local_validation_started_at"
        elif row.get("kind") == "validation_completed":
            key = "local_validation_completed_at"
        if key and observed[key] is None:
            observed[key] = row["timestamp"]
    def interval(end, start):
        if observed[end] is None or observed[start] is None:
            return None
        return (datetime.fromisoformat(observed[end].replace("Z", "+00:00")) - datetime.fromisoformat(observed[start].replace("Z", "+00:00"))).total_seconds()
    return {**observed, "submission_to_completion_seconds": interval("terminal_at", "submitted_at"),
            "local_validation_seconds": interval("local_validation_completed_at", "local_validation_started_at")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--project", type=Path, required=True)
    prep.add_argument("--chunk-id", required=True)
    prep.add_argument("--scratch", type=Path, required=True)
    prep.add_argument("--profile", default="codex-sol-low")
    prep.add_argument("--pass-no", type=int, choices=(2, 3, 4, 5))
    live = sub.add_parser("run")
    live.add_argument("--scratch", type=Path, required=True)
    live.add_argument("--wire", choices=("canonical", "cache-v1", "cache-v2"), required=True)
    sizes = sub.add_parser("sizes", help="Offline comparison of frozen accepted canonical tasks/results")
    sizes.add_argument("--scratch", type=Path, required=True)
    shared_report = sub.add_parser("shared-sizes", help="Offline P1 compact-v1/P2-P5 cache-v2 versus cache-shared-v1 sizes")
    shared_report.add_argument("--project", type=Path, required=True)
    shared_report.add_argument("--chunk-id", required=True)
    shared_report.add_argument("--analysis-unit", required=True)
    shared_report.add_argument("--scratch", type=Path, required=True)
    saved = sub.add_parser("report")
    saved.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.project, args.chunk_id, args.scratch, args.profile, args.pass_no)
        elif args.command == "run":
            result = run(args.scratch, args.wire)
        elif args.command == "sizes":
            result = offline_sizes(args.scratch)
        elif args.command == "shared-sizes":
            result = shared_sizes(args.project, args.chunk_id, args.analysis_unit, args.scratch)
        else:
            result = report(args.scratch)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, OSError, KeyError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
