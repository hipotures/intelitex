"""Verified immutable accepted snapshots for native P2→P3 and P4→P5 resumes.

Canonical selected checkpoints own the parent. Every continuation copies its
native export into a new private runtime; failed children never modify it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from . import codex_cache_v2, codex_cache_shared_v2, codex_parent
from .util import PipelineError, digest, read_json

STRATEGY = codex_parent.PAIRED_STRATEGY
PARENTS = {3: 2, 5: 4}
DEPENDENCIES = {3: "SEMANTIC_AUDIT", 5: "CORRECTION_LEDGER"}


def inspect_snapshot(path: Path, parent: dict, body: dict) -> dict:
    """Prove a complete single-parent conversation and its raw answer boundary."""
    count = 2 if parent["parent_pass"] == 2 else 3
    proof = codex_parent.inspect_parent(
        path, parent["parent_thread_id"], parent["parent_session_id"],
        parent["parent_turn_id"], body, message_count=count,
    )
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(e.get("type") in {"compacted", "thread_history_reference"} for e in events):
        raise PipelineError("Pair snapshot contains rewritten/incomplete native history.")
    boundary = next(i for i, e in enumerate(events) if e.get("type") == "event_msg"
                    and e["payload"].get("type") == "task_started")
    users, answers = [], []
    history_bytes = 0
    for e in events[boundary:]:
        if e.get("type") != "response_item":
            continue
        item = e["payload"]
        history_bytes += len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if item.get("type") in {"reasoning", "configuration_update"}:
            continue  # Trusted Codex reasoning/configuration records.
        if item.get("type") != "message" or item.get("role") not in {"user", "assistant", "developer", "system"}:
            raise PipelineError("Pair snapshot contains tool/unsupported conversation items.")
        text = "".join(c.get("text", "") for c in item.get("content", []))
        if item.get("role") == "user":
            if not text.startswith("<environment_context>"):
                users.append(text)
        elif item.get("role") == "assistant":
            answers.append(text.strip())
    if (len(users) != 1 or len(answers) != 1 or
            digest(users[0]) != parent["parent_active_input_sha256"] or
            digest(answers[0]) != parent["raw_response_sha256"]):
        raise PipelineError("Pair snapshot must contain exactly the accepted parent prompt and answer.")
    if proof["parent_rollout_sha256"] != parent["parent_rollout_sha256"]:
        raise PipelineError("Accepted pair snapshot changed after validation.")
    for key in codex_parent.COMPATIBILITY_FIELDS:
        if parent[key] != body["cache_diagnostics"][key]:
            raise PipelineError("Pair source/common/instructions/schema differ.")
    if count == 3 and parent["draft_message_sha256"] != body["cache_diagnostics"]["draft_message_sha256"]:
        raise PipelineError("Pair draft differs from accepted P4.")
    return {**proof, "parent_active_input": users[0], "parent_raw_answer": answers[0],
            "additional_history_utf8_bytes": history_bytes}


def parent_metadata(meta: dict, attempt: Path, body: dict, accepted: dict, pass_no: int) -> dict:
    """Only the normal canonical acceptance path may promote a new snapshot."""
    from .engine import _decode_transport_result, conservative_repair, response_schema, validate_result

    if pass_no not in (2, 4) or meta.get("provider") != "codex" or meta.get("wire_format") != "cache-shared-v2":
        raise PipelineError("Pair parent requires a completed Codex cache-shared-v2 P2/P4.")
    path = (attempt / meta["rollout_copy"]).resolve()
    if not path.is_relative_to(attempt.resolve()):
        raise PipelineError("Pair rollout escapes its attempt evidence.")
    request = read_json(attempt / "request.transport.json")
    thread = request.get("thread", {})
    if thread.get("ephemeral") is not False or "resume" in request or "fork" in request:
        raise PipelineError("Pair parent must be a fresh persistent root.")
    raw = (attempt / "answer.txt").read_text(encoding="utf-8").strip()
    semantic = read_json(attempt / "request.semantic.json")
    inputs = semantic["input_payload"]
    layout, context = codex_cache_shared_v2.encode_input(inputs, pass_no)
    recorded_context = read_json(attempt / "codec.context.json")
    if (semantic.get("pass_no") != pass_no or request["turn"]["input"] != [
            {"type": "text", "text": layout.active_pass_message}] or
            recorded_context != context.as_dict()):
        raise PipelineError("Pair semantic task, active input or reference maps differ from saved transport.")
    decoded = _decode_transport_result(
        codex_cache_v2.parse_output(raw), "cache-shared-v2", inputs,
        recorded_context, expected_pass=pass_no,
    )
    decoded, repairs = conservative_repair(pass_no, decoded, inputs)
    jsonschema.Draft202012Validator(response_schema(pass_no, inputs)).validate(decoded)
    validate_result(pass_no, decoded, inputs)
    if decoded != accepted:
        raise PipelineError("Pair raw response does not decode/repair to the accepted canonical dependency.")
    if (thread.get("developerInstructions") != body["developer_instructions"] or
            request["turn"]["outputSchema"] != body["output_schema"]):
        raise PipelineError("Pair instructions/schema differ from the current task.")
    # First prove pre-turn isolation; then verify exactly one accepted answer.
    proof = codex_parent.inspect_parent(path, meta["thread_id"], meta["session_id"],
                                       meta["turn_id"], body, message_count=2 if pass_no == 2 else 3)
    parent = {
        "strategy": STRATEGY, "provider": "codex", "wire_format": "cache-shared-v2",
        "parent_pass": pass_no, "pair": "P2-P3" if pass_no == 2 else "P4-P5",
        "parent_thread_id": meta["thread_id"], "parent_session_id": meta["session_id"],
        "parent_turn_id": meta["turn_id"], "parent_rollout": meta["rollout_copy"],
        "native_rollout_name": Path(meta["thread_path"]).name,
        "parent_rollout_sha256": proof["parent_rollout_sha256"],
        "accepted_result_sha256": digest(accepted), "raw_response_sha256": digest(raw),
        "canonical_consistency_verified": True, "deterministic_repairs": repairs,
        "parent_active_input_sha256": digest(request["turn"]["input"][0]["text"]),
        **{key: meta[key] for key in codex_parent.COMPATIBILITY_FIELDS},
        **({"draft_message_sha256": meta["draft_message_sha256"]} if pass_no == 4 else {}),
    }
    history = inspect_snapshot(path, parent, body)
    parent["additional_history_utf8_bytes"] = history["additional_history_utf8_bytes"]
    return parent


def resolve_parent(store: Any, body: dict, inputs: dict) -> dict:
    pass_no = body["pass_no"]
    parent_pass = PARENTS[pass_no]
    key = f"pass{parent_pass}/{inputs['CHUNK_ID']}"
    fallback = {"pair_parent_status": "unavailable", "execution_strategy": "fresh_root_fallback"}
    receipt = store.get("selected_pass:" + key)
    if not receipt or store.get("invalidated:" + key):
        return dict(fallback, pair_parent_reason=f"No current accepted P{parent_pass} receipt")
    job = store.job(key, receipt["fingerprint"])
    if not job:
        return dict(fallback, pair_parent_reason="Selected pair checkpoint unavailable")
    meta = job["meta"]
    incompatible = dict(fallback, pair_parent_status="incompatible")
    if meta.get("provider") != "codex" or meta.get("wire_format") != "cache-shared-v2":
        return dict(incompatible, pair_parent_reason="Parent was not Codex cache-shared-v2")
    if inputs[DEPENDENCIES[pass_no]] != job["value"]:
        return dict(incompatible, pair_parent_reason="Current canonical dependency is not the selected accepted parent")
    if any(not meta.get(k) or meta[k] != body["cache_diagnostics"][k]
           for k in codex_parent.COMPATIBILITY_FIELDS):
        return dict(incompatible, pair_parent_reason="Parent source/common/instructions/schema differ")
    if parent_pass == 4 and meta.get("draft_message_sha256") != body["cache_diagnostics"]["draft_message_sha256"]:
        return dict(incompatible, pair_parent_reason="Accepted P4 draft differs")
    root = store.root.resolve()
    selected_attempt = meta.get("accepted_attempt") or meta.get("recovered_from")
    candidates = [root / selected_attempt] if selected_attempt else sorted((root / job["path"]).parent.glob("attempt_*"))
    for attempt in candidates:
        try:
            attempt = attempt.resolve()
            if not attempt.is_relative_to(root / "artifacts" / key):
                continue
            recorded = read_json(attempt / "response_meta.json")
            if recorded.get("status") != "completed" or any(recorded.get(k) != meta.get(k) for k in (
                "thread_id", "session_id", "turn_id", "rollout_copy", *codex_parent.COMPATIBILITY_FIELDS,
            )):
                continue
            parent = parent_metadata(meta, attempt, body, job["value"], parent_pass)
            if meta.get("pair_parent") and meta["pair_parent"] != parent:
                return dict(incompatible, pair_parent_reason="Pair evidence changed after acceptance")
            return {"pair_parent_status": "available", "execution_strategy": "paired_resume",
                    "pair_parent": {**parent, "parent_attempt": str(attempt.relative_to(root)),
                                    "parent_rollout": str((attempt / parent["parent_rollout"]).resolve())}}
        except (OSError, ValueError, KeyError, TypeError, PipelineError, jsonschema.ValidationError):
            continue
    return dict(incompatible, pair_parent_reason="No complete verified native pair snapshot")
