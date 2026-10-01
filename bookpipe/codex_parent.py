"""Accepted P2 evidence used only for native, pre-turn Codex cache forks.

The selected canonical checkpoint owns this optimization. No cache registry,
conversation continuation, or local cache-expiry state is maintained.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import PipelineError, digest, read_json


STRATEGY = "p2-parent-ephemeral-fork-v1"
STRATEGIES = ("fresh-root", STRATEGY)
COMPATIBILITY_FIELDS = (
    "source_message_sha256", "translation_common_message_sha256",
    "developer_instructions_sha256", "base_instructions_sha256",
    "transport_output_schema_sha256",
)


def strategy(options: dict) -> str:
    value = options.get("translation_thread_strategy", STRATEGY)
    if value not in STRATEGIES:
        raise PipelineError(f"Unknown Codex translation_thread_strategy: {value!r}; expected {STRATEGIES}.")
    return value


def inspect_parent(path: Path, thread_id: str, session_id: str, turn_id: str, body: dict) -> dict:
    """Prove the exact native cutoff retains only SOURCE/COMMON application data.

    0.159.3's native file export has task_started turn boundaries. Unsupported
    exports (including incomplete paginated bundles) are optimization misses;
    they are never flattened/rewritten into a fabricated Codex history.
    """
    if not path.is_file():
        raise PipelineError("Parent has no self-contained native rollout file.")
    data = path.read_bytes()
    events = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if any(not isinstance(e, dict) or not isinstance(e.get("payload"), dict) for e in events):
        raise PipelineError("Malformed native parent rollout records.")
    sessions = [e["payload"] for e in events if e.get("type") == "session_meta"]
    if len(sessions) != 1 or sessions[0].get("id") != thread_id or sessions[0].get("session_id") != session_id:
        raise PipelineError("Parent rollout identity differs from accepted P2.")
    base = sessions[0].get("base_instructions", {})
    if not isinstance(base, dict) or digest(base.get("text", "")) != body["cache_diagnostics"]["base_instructions_sha256"]:
        raise PipelineError("Parent persisted base instructions differ from the current request.")
    boundaries = [i for i, e in enumerate(events) if e.get("type") == "event_msg"
                  and e.get("payload", {}).get("type") == "task_started"]
    if len(boundaries) != 1 or events[boundaries[0]]["payload"].get("turn_id") != turn_id:
        raise PipelineError("Parent must contain exactly the accepted P2 turn boundary.")
    prefix = events[:boundaries[0]]
    messages = []
    developers = []
    for event in prefix:
        if event.get("type") in {"compacted", "thread_history_reference"}:
            raise PipelineError("Parent pre-turn history is not a self-contained unmodified prefix.")
        if event.get("type") != "response_item":
            continue
        item = event["payload"]
        if item.get("type") == "configuration_update":
            continue  # Trusted Codex configuration, never a previous answer.
        if item.get("type") != "message" or item.get("role") not in {"user", "developer", "system"}:
            raise PipelineError("Parent pre-turn prefix contains model/tool history.")
        if item.get("role") != "user":
            if item.get("role") == "developer":
                developers.append("".join(c.get("text", "") for c in item.get("content", [])))
            continue
        text = "".join(c.get("text", "") for c in item.get("content", []))
        if text.startswith("<environment_context>"):
            continue  # Codex-owned environment wrapper; not pipeline input.
        messages.append({k: item[k] for k in ("type", "role", "content")})
    expected = [{k: item[k] for k in ("type", "role", "content")} for item in body["injected_items"][:2]]
    if messages != expected:
        raise PipelineError("Parent pre-P2 history does not contain exactly the current SOURCE and COMMON.")
    if sum(body["developer_instructions"] in text for text in developers) != 1:
        raise PipelineError("Parent persisted developer contract differs from the current request.")
    if not any(e.get("type") == "event_msg" and e.get("payload", {}).get("type") == "task_complete"
               and e["payload"].get("turn_id") == turn_id for e in events):
        raise PipelineError("Parent P2 turn has no persisted completion.")
    return {"parent_rollout_sha256": digest(data), "before_turn_boundary_verified": True,
            "inherited_message_count": 2, "prior_assistant_history": False}


def parent_metadata(meta: dict, attempt: Path, body: dict, accepted: dict) -> dict:
    """Called only after canonical validation; activated by the saved job receipt."""
    if meta.get("provider") != "codex" or meta.get("wire_format") != "cache-shared-v2":
        raise PipelineError("P2 parent requires Codex cache-shared-v2 evidence.")
    path = (attempt / meta["rollout_copy"]).resolve()
    if not path.is_relative_to(attempt.resolve()):
        raise PipelineError("Parent rollout escapes its attempt evidence.")
    proof = inspect_parent(path, meta["thread_id"], meta["session_id"], meta["turn_id"], body)
    return {"strategy": STRATEGY, "provider": "codex", "wire_format": "cache-shared-v2",
            "parent_thread_id": meta["thread_id"], "parent_session_id": meta["session_id"],
            "parent_turn_id": meta["turn_id"], "parent_rollout": meta["rollout_copy"],
            "native_rollout_name": Path(meta["thread_path"]).name,
            "model": meta.get("requested_model"), "requested_effort": meta.get("requested_effort"),
            "accepted_result_sha256": digest(accepted),
            **{k: meta.get(k) for k in COMPATIBILITY_FIELDS}, **proof}


def resolve_parent(store: Any, body: dict, inputs: dict) -> dict:
    """Find only the selected accepted P2, including historical v2 attempts."""
    fallback = {"cache_parent_status": "unavailable", "execution_strategy": "fresh_root_fallback"}
    if body.get("pass_no") not in (3, 4, 5):
        return fallback
    key = f"pass2/{inputs['CHUNK_ID']}"
    receipt = store.get("selected_pass:" + key)
    if not receipt or store.get("invalidated:" + key):
        return dict(fallback, cache_parent_reason="No current accepted P2 receipt")
    # Checkpoint integrity errors still follow the existing fail-closed policy.
    job = store.job(key, receipt["fingerprint"])
    if not job:
        return dict(fallback, cache_parent_reason="Selected P2 checkpoint unavailable")
    meta = job["meta"]
    incompatible = dict(fallback, cache_parent_status="incompatible")
    if meta.get("provider") != "codex" or meta.get("wire_format") != "cache-shared-v2":
        return dict(incompatible, cache_parent_reason="P2 was not Codex cache-shared-v2")
    if "SEMANTIC_AUDIT" in inputs and inputs["SEMANTIC_AUDIT"] != job["value"]:
        return dict(incompatible, cache_parent_reason="Current audit is not selected accepted P2")
    expected = body["cache_diagnostics"]
    if any(not meta.get(k) or meta[k] != expected[k] for k in COMPATIBILITY_FIELDS):
        return dict(incompatible, cache_parent_reason="P2 source/common/instructions/schema differ")
    root = store.root.resolve()
    candidates = [root / meta["accepted_attempt"]] if meta.get("accepted_attempt") else (
        [root / meta["recovered_from"]] if meta.get("recovered_from") else
        sorted((root / job["path"]).parent.glob("attempt_*")))
    for attempt in candidates:
        try:
            attempt = attempt.resolve()
            if not attempt.is_relative_to(root / "artifacts" / key):
                continue
            recorded = read_json(attempt / "response_meta.json")
            if recorded.get("status") != "completed" or any(recorded.get(k) != meta.get(k) for k in (
                "thread_id", "session_id", "turn_id", "rollout_copy", *COMPATIBILITY_FIELDS,
            )):
                continue
            request = read_json(attempt / "request.transport.json")
            if (request.get("thread", {}).get("ephemeral") is not False or
                    request.get("thread", {}).get("developerInstructions") != body["developer_instructions"] or
                    request.get("turn", {}).get("outputSchema") != body["output_schema"]):
                continue
            parent = parent_metadata(meta, attempt, body, job["value"])
            persisted = meta.get("cache_parent")
            if persisted and persisted != parent:
                return dict(incompatible, cache_parent_reason="P2 parent evidence changed after acceptance")
            path = (attempt / parent["parent_rollout"]).resolve()
            return {"cache_parent_status": "available", "execution_strategy": "ephemeral_fork",
                    "cache_parent": {**parent, "parent_rollout": str(path),
                                     "parent_attempt": str(attempt.relative_to(root))}}
        except (OSError, ValueError, KeyError, TypeError, PipelineError):
            continue
    return dict(incompatible, cache_parent_reason="No verifiable accepted P2 native rollout/cutoff")
