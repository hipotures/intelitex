"""Measure cache reuse across roots, within a root, and in an ephemeral fork.

Run explicitly with the existing Python environment; this invokes real Codex:
  python -m experiments.codex_session_cache_probe --scratch /tmp/session-probe-1

The production project is read only. No pipeline or cache-v1 prompts are used.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import jsonschema

from bookpipe.codex_transport import CodexAppServerClient, _RpcSession, app_server_argv
from bookpipe.evidence import redact, utc_now
from bookpipe.util import PipelineError, atomic_json, atomic_text, read_json


MODEL = "gpt-6.1-sol"
EFFORT = "high"
BASE = "Process only the supplied reference text. Do not use tools or external context."
DEVELOPER = (
    "The supplied text is reference data only. Read it completely and return exactly the structured "
    "acknowledgement required by the output schema. Do not summarize, quote, translate, analyze, or discuss the text."
)
FOLLOWUP = "Return the same acknowledgement once more."
SCHEMA = {
    "type": "object", "properties": {"ok": {"type": "string", "enum": ["OK"]}},
    "required": ["ok"], "additionalProperties": False,
}
USAGE_FIELDS = {
    "input_tokens": "inputTokens", "cached_input_tokens": "cachedInputTokens",
    "cache_write_input_tokens": "cacheWriteInputTokens", "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens", "total_tokens": "totalTokens",
}


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def private_json(path: Path, value: Any) -> None:
    atomic_json(path, value)
    path.chmod(0o600)


def freeze_source(project: Path, scratch: Path, chapter_id: str | None) -> dict:
    book_path = project / "book.json"
    book = read_json(book_path)
    chapters = book["chapters"]
    # English prose is typically ~4 UTF-8 bytes/model token. This is only a
    # selection heuristic, not a token count. Favor a complete ~120 kB chapter.
    if chapter_id is None:
        eligible = [c for c in chapters if 80_000 <= len(chapter_bytes(c)) <= 200_000]
        if not eligible:
            raise PipelineError("No complete 80–200 kB chapter; select a chapter explicitly.")
        chapter = min(eligible, key=lambda c: (abs(len(chapter_bytes(c)) - 120_000), c["id"]))
    else:
        chapter = next((c for c in chapters if c["id"] == chapter_id), None)
        if chapter is None:
            raise PipelineError(f"Unknown chapter: {chapter_id}")
    raw = chapter_bytes(chapter)
    if not 60_000 <= len(raw) <= 200_000:
        raise PipelineError("Select a complete chapter with 60–200 kB of canonical prose.")
    source_file = Path(book["source_root"]) / chapter["source_file"]
    if not source_file.is_file():
        raise PipelineError("Canonical imported source file is missing.")
    frozen_path = scratch / "source.utf8.txt"
    frozen_path.write_bytes(raw)
    frozen_path.chmod(0o400)
    metadata = {
        "chapter_id": chapter["id"], "chapter_title": chapter.get("title"),
        "sha256": sha(raw), "utf8_bytes": len(raw), "complete_chapter": True,
        "source_words": chapter.get("source_words"),
        "available_source_estimate": chapter.get("source_tokens"),
        "source_estimate_identity": chapter.get("source_tokens_tokenizer", book.get("tokenizer_identity")),
        "source_estimate_warning": "utf8_byte_upper_bound is bytes, not an actual model-token count",
        "canonical_book_path": str(book_path), "canonical_book_sha256": sha(book_path.read_bytes()),
        "imported_source_path": str(source_file), "imported_source_sha256": sha(source_file.read_bytes()),
        "serialization": "All canonical chapter.blocks texts in original order, joined by two LF characters; no trailing LF.",
    }
    private_json(scratch / "source.json", metadata)
    return metadata


def chapter_bytes(chapter: dict) -> bytes:
    return "\n\n".join(block["text"] for block in chapter["blocks"]).encode("utf-8")


class Recorder:
    """Append complete RPC evidence globally and to the current scenario."""

    def __init__(self, scratch: Path):
        self.scratch = scratch
        self.scenario: str | None = None
        self.completion: tuple[str, float] | None = None
        self.expected_thread: str | None = None

    def select(self, scenario: str) -> Path:
        self.scenario = scenario
        self.completion = None
        directory = self.scratch / scenario
        directory.mkdir(mode=0o700)
        return directory

    def event(self, direction: str, kind: str, value: Any, context: Any = None) -> None:
        now, mono = utc_now(), time.monotonic()
        row = {"timestamp": now, "monotonic_seconds": mono, "scenario": self.scenario,
               "direction": direction, "kind": kind, "message": value, "context": context}
        # Auth is never sent over RPC. Stderr may contain credential-like text.
        if kind == "stderr":
            row["message"] = redact(value)
        paths = [self.scratch / "rpc.jsonl"]
        if self.scenario:
            paths.append(self.scratch / self.scenario / "rpc.jsonl")
        for path in paths:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
            path.chmod(0o600)
        if (direction == "inbound" and kind == "rpc" and isinstance(value, dict)
                and value.get("method") == "turn/completed"
                and value.get("params", {}).get("threadId") == self.expected_thread):
            self.completion = (now, mono)


class ProbeRpc(_RpcSession):
    """Retain one RPC connection, resetting only per-turn observations."""

    def reset_turn(self, thread_id: str) -> None:
        self.recorder.expected_thread = thread_id
        self.notifications.clear()
        self.state.update(thread_id=thread_id, turn_id=None, terminal=None, terminal_error=None,
                          final_messages=[], fallback_messages=[], usage_events=[], context_altered=False)

    def dispatch(self, message: dict[str, Any]) -> None:
        thread_id = (message.get("params") or {}).get("threadId")
        if thread_id and self.recorder.expected_thread and thread_id != self.recorder.expected_thread:
            # Late events from an idle root remain in raw evidence, but cannot
            # become another root's usage/final-answer/terminal observation.
            return
        super().dispatch(message)


def inspect_protocol(executable: str, scratch: Path) -> dict:
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    directory = scratch / "protocol"
    subprocess.run([executable, "app-server", "generate-json-schema", "--experimental", "--out", str(directory)],
                   check=True, capture_output=True, timeout=60)
    fork_path = directory / "v2" / "ThreadForkParams.json"
    fork = read_json(fork_path) if fork_path.exists() else {}
    needed = {"threadId", "ephemeral", "lastTurnId", "excludeTurns", "baseInstructions", "developerInstructions",
              "model", "config", "sandbox", "approvalPolicy", "runtimeWorkspaceRoots", "cwd"}
    list_path = directory / "v2" / "ThreadTurnsListParams.json"
    listing = read_json(list_path) if list_path.exists() else {}
    supported = (needed <= set(fork.get("properties", {}))
                 and {"threadId", "itemsView", "limit", "sortDirection"} <= set(listing.get("properties", {})))
    result = {"cli_version": version, "ephemeral_anchored_fork_supported": supported,
              "fork_fields": sorted(fork.get("properties", {})),
              "fork_skip_reason": None if supported else "Installed ThreadForkParams lacks supported ephemeral/anchored/isolation fields."}
    private_json(scratch / "protocol.json", result)
    return result


def validate_params(scratch: Path, name: str, params: dict) -> None:
    schema = read_json(scratch / "protocol" / "v2" / f"{name}.json")
    unknown = set(params) - set(schema.get("properties", {}))
    if unknown:
        raise PipelineError(f"Unsupported {name} fields: {sorted(unknown)}")
    jsonschema.validate(params, schema)


def thread_params(work: Path) -> dict:
    return {
        "model": MODEL, "cwd": str(work), "sandbox": "read-only", "approvalPolicy": "never",
        "ephemeral": False, "baseInstructions": BASE, "developerInstructions": DEVELOPER,
        "personality": "none", "environments": [], "dynamicTools": [],
        "selectedCapabilityRoots": [], "runtimeWorkspaceRoots": [],
        "config": {"model_reasoning_effort": EFFORT},
    }


def turn_params(work: Path, thread_id: str, text: str) -> dict:
    return {
        "threadId": thread_id, "input": [{"type": "text", "text": text}],
        "model": MODEL, "effort": EFFORT, "cwd": str(work), "environments": [],
        "runtimeWorkspaceRoots": [], "outputSchema": copy.deepcopy(SCHEMA),
    }


def isolate_skills(rpc: ProbeRpc, work: Path, timeout: float) -> list[str]:
    def listed():
        result = rpc.request("skills/list", {"cwds": [str(work)], "forceReload": True}, time.monotonic() + timeout)
        if any(group.get("errors") for group in result.get("data", [])):
            raise PipelineError("Skill discovery errors; refusing model requests.")
        return [skill for group in result.get("data", []) for skill in group.get("skills", [])]
    paths = sorted({skill["path"] for skill in listed() if skill.get("enabled")})
    for path in paths:
        result = rpc.request("skills/config/write", {"path": path, "enabled": False}, time.monotonic() + timeout)
        if result.get("effectiveEnabled") is not False:
            raise PipelineError(f"Skill remained enabled: {path}")
    if any(skill.get("enabled") for skill in listed()):
        raise PipelineError("Enabled skills remain; refusing model requests.")
    return paths


def validate_thread(result: dict, *, allow_local_fork_environment: bool = False) -> dict:
    thread = result.get("thread") or {}
    model = result.get("model") or thread.get("model")
    effort = result.get("reasoningEffort") or thread.get("reasoningEffort")
    if model != MODEL or effort != EFFORT:
        raise PipelineError(f"Reported configuration mismatch: {model!r}, {effort!r}")
    if not thread.get("id") or not thread.get("sessionId"):
        raise PipelineError("Thread response omitted thread/session identity.")
    if result.get("approvalPolicy") != "never" or result.get("sandbox", {}).get("type") != "readOnly":
        raise PipelineError("Thread did not confirm read-only sandbox and never approvals.")
    environments = thread.get("environments") or []
    # Fork has no environments override. The installed runtime may materialize
    # its private local cwd in metadata; turn/start explicitly replaces it with
    # []. Accept only that private cwd, never external capability environments.
    local_only = allow_local_fork_environment and all(
        env.get("environmentId") == "local" and env.get("cwd") == result.get("cwd")
        and not env.get("runtimeWorkspaceRoots") for env in environments
    )
    if result.get("instructionSources") or result.get("runtimeWorkspaceRoots") or (environments and not local_only):
        raise PipelineError("Unexpected external instructions or environment/workspace roots.")
    return {"thread_id": thread["id"], "session_id": thread["sessionId"], "path": thread.get("path"),
            "reported_model": model, "reported_effort": effort, "ephemeral": thread.get("ephemeral"),
            "forked_from_id": thread.get("forkedFromId"), "thread_environments_before_turn_override": environments}


def usage_values(events: list[dict]) -> dict:
    last = (events[-1].get("last") or {}) if events else {}
    # Use last, not accumulated thread total (A2 includes A1 in total).
    return {normalized: last.get(raw) for normalized, raw in USAGE_FIELDS.items()}


def substantial(row: dict) -> bool:
    cached, total = row.get("cached_input_tokens"), row.get("input_tokens")
    return row.get("status") == "completed" and isinstance(cached, int) and isinstance(total, int) and cached >= 10_000 and cached / max(total, 1) >= 0.5


def classify(rows: list[dict]) -> dict:
    by = {r["scenario"]: r for r in rows}
    if any(by.get(n, {}).get("cached_input_tokens") is None for n in ("A1", "B1", "A2")):
        return {"pattern": None, "conclusion": "Missing primary provider usage: experiment is inconclusive."}
    a, b, c = (by[n] for n in ("A1", "B1", "A2"))
    zero = lambda r: r["cached_input_tokens"] == 0
    if zero(b) and substantial(c) and "D1" in by and substantial(by["D1"]):
        return {"pattern": 4, "conclusion": "Same-session and ephemeral-fork cache reuse observed; fresh root reported none. Forks may retain parent affinity in this run."}
    if zero(a) and zero(b) and substantial(c):
        return {"pattern": 1, "conclusion": "Session-local cache reuse observed; independent fresh root reported none in this controlled run."}
    if zero(a) and substantial(b) and substantial(c):
        return {"pattern": 2, "conclusion": "Cross-root and same-session cache reuse observed; session affinity alone cannot explain pipeline misses."}
    if all(zero(r) for r in (a, b, c)):
        return {"pattern": 3, "conclusion": "No reported reuse, even within a warmed session. Investigate this before changing pipeline session architecture."}
    return {"pattern": None, "conclusion": "Mixed or partial cache reads; no predefined pattern fits. Do not infer affinity from this single run."}


class Probe:
    def __init__(self, scratch: Path, source: dict, protocol: dict, timeout: float):
        self.scratch, self.source, self.protocol, self.timeout = scratch, source, protocol, timeout
        self.work, self.home = scratch / "runtime" / "work", scratch / "runtime" / "home"
        self.recorder = Recorder(scratch)
        self.rows: list[dict] = []
        self.starts: dict[str, float] = {}
        self.completions: dict[str, float] = {}
        self.proc: subprocess.Popen | None = None
        self.rpc: ProbeRpc | None = None

    def start_root(self, scenario: str) -> dict:
        self.recorder.select(scenario)
        self.recorder.expected_thread = None
        params = thread_params(self.work)
        validate_params(self.scratch, "ThreadStartParams", params)
        private_json(self.scratch / scenario / "thread.start.params.json", params)
        result = self.rpc.request("thread/start", params, time.monotonic() + self.timeout)
        private_json(self.scratch / scenario / "thread.start.result.json", result)
        return validate_thread(result)

    def long_text(self) -> str:
        raw = (self.scratch / "source.utf8.txt").read_bytes()
        if sha(raw) != self.source["sha256"] or len(raw) != self.source["utf8_bytes"]:
            raise PipelineError("Frozen source bytes changed; refusing request.")
        return raw.decode("utf-8")

    def run_turn(self, scenario: str, thread: dict, text: str, relevant: str | None, relation: str) -> dict:
        directory = self.scratch / scenario
        params = turn_params(self.work, thread["thread_id"], text)
        validate_params(self.scratch, "TurnStartParams", params)
        private_json(directory / "turn.start.params.json", params)
        if scenario == "B1":
            original = read_json(self.scratch / "A1" / "turn.start.params.json")
            if original["input"][0]["text"].encode("utf-8") != params["input"][0]["text"].encode("utf-8"):
                raise PipelineError("A1/B1 source bytes differ; B1 will not be sent.")
            for key in ("model", "effort", "outputSchema", "cwd", "environments", "runtimeWorkspaceRoots"):
                if original[key] != params[key]:
                    raise PipelineError(f"A1/B1 {key} differs; B1 will not be sent.")
            if read_json(self.scratch / "A1" / "thread.start.params.json") != read_json(directory / "thread.start.params.json"):
                raise PipelineError("A1/B1 thread configuration differs; B1 will not be sent.")
        self.rpc.reset_turn(thread["thread_id"])
        started_at, started = utc_now(), time.monotonic()
        self.starts[scenario] = started
        row = {
            "scenario": scenario, "session_relation": relation, **thread,
            "turn_id": None, "started_at": started_at, "completed_at": None, "elapsed_seconds": None,
            "app_server_pid": self.proc.pid, "codex_home": str(self.home),
            "requested_model": MODEL, "requested_effort": EFFORT,
            "base_instructions_sha256": sha(BASE.encode()), "developer_instructions_sha256": sha(DEVELOPER.encode()),
            "output_schema_sha256": sha(json_bytes(SCHEMA)),
            "long_user_input_sha256": self.source["sha256"], "long_user_input_utf8_bytes": self.source["utf8_bytes"],
            "submitted_user_input_sha256": sha(text.encode()), "submitted_user_input_utf8_bytes": len(text.encode()),
            "previous_relevant_scenario": relevant,
            "previous_relevant_start_to_start_seconds": round(started - self.starts[relevant], 6) if relevant else None,
            "previous_relevant_completion_to_start_seconds": round(started - self.completions[relevant], 6) if relevant else None,
            "previous_scenario": self.rows[-1]["scenario"] if self.rows else None,
            "previous_start_to_start_seconds": round(started - self.starts[self.rows[-1]["scenario"]], 6) if self.rows else None,
            "previous_completion_to_start_seconds": round(started - self.completions[self.rows[-1]["scenario"]], 6) if self.rows else None,
            "status": "submitted", **{k: None for k in USAGE_FIELDS},
        }
        private_json(directory / "row.json", row)
        print(json.dumps({"scenario": scenario, "status": "submitted", "thread_id": thread["thread_id"],
                          "session_id": thread["session_id"], "user_utf8_bytes": len(text.encode())}), flush=True)
        try:
            deadline = started + self.timeout
            result = self.rpc.request("turn/start", params, deadline)
            private_json(directory / "turn.start.result.json", result)
            turn = result.get("turn") or {}
            self.rpc.state["turn_id"] = turn.get("id") or self.rpc.state["turn_id"]
            row["turn_id"] = self.rpc.state["turn_id"]
            self.rpc.drain_until_terminal(deadline)
            if self.rpc.state["terminal_error"]:
                raise PipelineError(f"Terminal error: {self.rpc.state['terminal_error']}")
            terminal = self.rpc.state["terminal"] or {}
            private_json(directory / "turn.completed.json", terminal)
            if terminal.get("status") != "completed" or self.rpc.state["context_altered"]:
                raise PipelineError("Turn failed/interrupted or context was compacted.")
            completed_at, completed = self.recorder.completion or (utc_now(), time.monotonic())
            self.completions[scenario] = completed
            row.update(completed_at=completed_at, elapsed_seconds=round(completed - started, 6))
            self.rpc.wait_late_usage(0.75)
            messages = self.rpc.state["final_messages"] or self.rpc.state["fallback_messages"]
            if not messages:
                raise PipelineError("Missing completed acknowledgement.")
            answer = messages[-1]
            atomic_text(directory / "answer.txt", answer)
            jsonschema.validate(json.loads(answer), SCHEMA)
            row.update(usage_values(self.rpc.state["usage_events"]))
            if row["input_tokens"] is None or row["cached_input_tokens"] is None:
                raise PipelineError("No provider input/cache usage reported.")
            if scenario in ("A1", "B1") and row["input_tokens"] < 10_000:
                raise PipelineError("Source produced fewer than 10k provider input tokens.")
            for key, expected in (("model", MODEL), ("effort", EFFORT)):
                reported = terminal.get(key) or turn.get(key)
                if reported and reported != expected:
                    raise PipelineError(f"Turn reported unexpected {key}: {reported}")
            row["cache_read_ratio"] = row["cached_input_tokens"] / row["input_tokens"] if row["input_tokens"] else None
            row["status"] = "completed"
        except BaseException as exc:
            row.update(status="failed", error=str(exc), **usage_values(self.rpc.state["usage_events"]))
            raise
        finally:
            usage_events = [m for m in self.rpc.notifications if m.get("method") == "thread/tokenUsage/updated"]
            private_json(directory / "usage.events.json", usage_events)
            private_json(directory / "row.json", row)
            self.rows.append(row)
            private_json(self.scratch / "rows.json", self.rows)
            print(json.dumps({k:row.get(k) for k in ("scenario", "status", *USAGE_FIELDS, "elapsed_seconds", "cache_read_ratio")}), flush=True)
        return row

    def run_fork(self, parent: dict, a1: dict) -> dict:
        directory = self.recorder.select("D1")
        # Ephemeral children cannot be hydrated/listed in this CLI. Verify the
        # completed anchor on the persisted parent, then use the documented
        # inclusive lastTurnId boundary in thread/fork (excludeTurns affects
        # response hydration, not the inherited model history).
        list_params = {"threadId": parent["thread_id"], "itemsView": "notLoaded", "limit": 10, "sortDirection": "asc"}
        validate_params(self.scratch, "ThreadTurnsListParams", list_params)
        private_json(directory / "parent.thread.turns.list.params.json", list_params)
        self.recorder.expected_thread = None
        listed = self.rpc.request("thread/turns/list", list_params, time.monotonic() + self.timeout)
        private_json(directory / "parent.thread.turns.list.result.json", listed)
        turns = listed.get("data", [])
        anchor = next((turn for turn in turns if turn.get("id") == a1["turn_id"]), None)
        if listed.get("nextCursor") or not anchor or anchor.get("status") != "completed":
            raise PipelineError("Parent A1 anchor is not verifiably completed.")
        params = {
            "threadId": parent["thread_id"], "lastTurnId": a1["turn_id"], "ephemeral": True,
            "excludeTurns": True,
            "model": MODEL, "config": {"model_reasoning_effort": EFFORT}, "cwd": str(self.work),
            "baseInstructions": BASE, "developerInstructions": DEVELOPER,
            "sandbox": "read-only", "approvalPolicy": "never", "runtimeWorkspaceRoots": [],
        }
        validate_params(self.scratch, "ThreadForkParams", params)
        private_json(directory / "thread.fork.params.json", params)
        self.recorder.expected_thread = None
        result = self.rpc.request("thread/fork", params, time.monotonic() + self.timeout)
        private_json(directory / "thread.fork.result.json", result)
        child = validate_thread(result, allow_local_fork_environment=True)
        if child["thread_id"] == parent["thread_id"] or child["ephemeral"] is not True:
            raise PipelineError("Fork did not produce a distinct ephemeral child.")
        if child["forked_from_id"] != parent["thread_id"]:
            raise PipelineError("Fork returned unexpected parent.")
        child.update(parent_thread_id=parent["thread_id"], parent_session_id=parent["session_id"],
                     anchor_turn_id=a1["turn_id"],
                     anchor_verification="Completed persisted-parent A1 + schema-documented inclusive lastTurnId; ephemeral history hydration unsupported")
        return self.run_turn("D1", child, FOLLOWUP, "A1", "ephemeral fork of A after A1, excluding A2")

    def summary(self, fork: dict) -> dict:
        checks = verify_rows(self.rows)
        audit_path = self.scratch / "rollout.audit.json"
        audit = read_json(audit_path) if audit_path.exists() else {}
        if audit.get("roots"):
            checks["persisted_rollout_configuration"] = all(r["all_passed"] for r in audit["roots"].values())
            checks["identical_initial_runtime_prefix"] = audit["a1_b1_initial_runtime_prefix_identical"] is True
        result = {"source": self.source, "protocol": self.protocol, "model": MODEL, "effort": EFFORT,
                  "rows": self.rows, "checks": checks, "fork": fork, "rollout_audit": audit,
                  "classification": classify(self.rows) if all(checks.values()) else {
                      "pattern": None, "conclusion": "Configuration/identity checks failed: do not interpret hits or misses."},
                  "substantial_threshold": "at least 10000 cached tokens and at least 50% of input",
                  "limitation": "One run; provider cache retention/routing is not controlled. Runtime may add platform sandbox/environment wrappers."}
        private_json(self.scratch / "summary.json", result)
        lines = ["# Codex session/cache probe", "", f"Chapter: {self.source['chapter_id']} / {self.source['chapter_title']}",
                 f"Source: {self.source['utf8_bytes']} UTF-8 bytes; SHA-256 {self.source['sha256']}",
                 f"CLI: {self.protocol['cli_version']}; model: {MODEL}; effort: {EFFORT}", "",
                 "| Scenario | Session relation | Input | Cached | Cache ratio | Elapsed (s) |",
                 "|---|---|---:|---:|---:|---:|"]
        for row in self.rows:
            ratio = row.get("cache_read_ratio")
            lines.append(f"| {row['scenario']} | {row['session_relation']} | {row.get('input_tokens')} | "
                         f"{row.get('cached_input_tokens')} | {ratio:.2%} | {row.get('elapsed_seconds')} |" if ratio is not None else
                         f"| {row['scenario']} | {row['session_relation']} | unavailable | unavailable | unavailable | unavailable |")
        lines.extend(["", json.dumps(result["classification"]), "", "Full identifiers, timing and raw usage are in summary.json and per-scenario evidence."])
        atomic_text(self.scratch / "summary.md", "\n".join(lines) + "\n")
        return result


def verify_rows(rows: list[dict]) -> dict[str, bool]:
    by = {row["scenario"]: row for row in rows}
    if not all(n in by for n in ("A1", "B1", "A2")):
        return {"primary_scenarios_completed": False}
    primary = [by[n] for n in ("A1", "B1", "A2")]
    checks = {
        "primary_scenarios_completed": all(r["status"] == "completed" for r in primary),
        "fixed_reported_model_effort": all(r["reported_model"] == MODEL and r["reported_effort"] == EFFORT for r in rows),
        "fixed_requested_model_effort": all(r["requested_model"] == MODEL and r["requested_effort"] == EFFORT for r in rows),
        "fixed_instructions_schema": all(len({r[k] for r in rows}) == 1 for k in
                                        ("base_instructions_sha256", "developer_instructions_sha256", "output_schema_sha256")),
        "identical_long_input": by["A1"]["submitted_user_input_sha256"] == by["B1"]["submitted_user_input_sha256"],
        "same_server_home": len({r["app_server_pid"] for r in rows}) == len({r["codex_home"] for r in rows}) == 1,
        "different_root_threads_sessions": by["A1"]["thread_id"] != by["B1"]["thread_id"] and by["A1"]["session_id"] != by["B1"]["session_id"],
        "a2_original_thread_session": all(by["A1"][k] == by["A2"][k] for k in ("thread_id", "session_id")),
        "a2_tiny_followup": by["A2"]["submitted_user_input_sha256"] == sha(FOLLOWUP.encode()),
        "distinct_turn_ids": len({r["turn_id"] for r in rows}) == len(rows) and all(r["turn_id"] for r in rows),
    }
    if "D1" in by:
        child = by["D1"]
        checks["ephemeral_fork_of_completed_a1"] = (
            child["ephemeral"] is True and child["thread_id"] != by["A1"]["thread_id"]
            and child["parent_thread_id"] == by["A1"]["thread_id"]
            and child["parent_session_id"] == by["A1"]["session_id"]
            and child["anchor_turn_id"] == by["A1"]["turn_id"]
        )
        checks["d1_same_tiny_followup"] = child["submitted_user_input_sha256"] == by["A2"]["submitted_user_input_sha256"]
    return checks


def audit_rollouts(scratch: Path, rows: list[dict]) -> dict:
    """Check persisted roots without emitting any source text in diagnostics."""
    source = (scratch / "source.utf8.txt").read_bytes().decode("utf-8")
    audits = {}
    for row in rows:
        if row.get("ephemeral") or not row.get("path"):
            continue
        path = Path(row["path"]).resolve()
        if not path.is_relative_to((scratch / "runtime/home").resolve()):
            raise PipelineError("Rollout path escaped private home.")
        prefix, contexts, meta, item_types = [], {}, {}, set()
        found_source = False
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            kind, payload = event.get("type"), event.get("payload", {})
            if kind == "session_meta":
                meta = payload
            elif kind == "turn_context":
                contexts[payload.get("turn_id")] = payload
            elif kind == "response_item":
                item_types.add(payload.get("type"))
                text = "\n".join(part.get("text", "") for part in payload.get("content", []))
                if not found_source:
                    if payload.get("role") == "user" and text == source:
                        found_source = True
                    else:
                        prefix.append(payload)
        base = meta.get("base_instructions", {})
        ctx = contexts.get(row["turn_id"], {})
        checks = {
            "custom_base_replaced_default": base.get("text") == BASE and base.get("provenance", {}).get("type") == "custom",
            "reported_model_effort": ctx.get("model") == MODEL and ctx.get("effort") == EFFORT,
            "read_only_never": ctx.get("approval_policy") == "never" and ctx.get("sandbox_policy", {}).get("type") == "read-only",
            "session_matches_rpc": meta.get("session_id") == row["session_id"],
            "empty_runtime_roots": meta.get("runtime_workspace_roots") == [],
            "frozen_source_found_in_history": found_source,
            "no_tool_calls_in_history": item_types <= {"message", "reasoning"},
            "fixed_developer_present": any(DEVELOPER in "\n".join(p.get("text", "") for p in item.get("content", []))
                                           for item in prefix if item.get("role") == "developer"),
        }
        audits[row["scenario"]] = {"checks": checks, "all_passed": all(checks.values()),
                                  "initial_runtime_prefix_sha256": sha(json_bytes([
                                      {"role": item.get("role"), "content": item.get("content")} for item in prefix])),
                                  "initial_runtime_message_records_sha256": sha(json_bytes(prefix)),
                                  "initial_developer_message_utf8_bytes": [len(json_bytes(p)) for p in prefix if p.get("role") == "developer"],
                                  "turn_context_model": ctx.get("model"), "turn_context_effort": ctx.get("effort")}
    identical = (audits.get("A1", {}).get("initial_runtime_prefix_sha256") == audits.get("B1", {}).get("initial_runtime_prefix_sha256"))
    result = {"roots": audits, "a1_b1_initial_runtime_prefix_identical": identical if audits else None,
              "limitation": "Codex adds platform sandbox/collaboration/role instructions even with capability flags disabled. Compared prefix hashes cover role/content; raw records also contain unique message IDs and creation timestamps, which remain in evidence."}
    private_json(scratch / "rollout.audit.json", result)
    return result


def run(project: Path, scratch: Path, chapter_id: str | None, timeout: float, with_fork: bool) -> dict:
    project, scratch = project.resolve(), scratch.resolve()
    repository = Path(__file__).resolve().parents[1]
    for protected in (project, repository):
        if scratch.is_relative_to(protected) or protected.is_relative_to(scratch):
            raise PipelineError("Scratch must be outside both repository and production project, and cannot contain either.")
    executable = shutil.which("codex")
    if not executable:
        raise PipelineError("Installed Codex not found.")
    auth = Path.home() / ".codex" / "auth.json"
    if not auth.is_file():
        raise PipelineError("Requested existing ~/.codex/auth.json is missing.")
    scratch.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = freeze_source(project, scratch, chapter_id)
    protocol = inspect_protocol(executable, scratch)
    probe = Probe(scratch, source, protocol, timeout)
    home, sqlite, work = probe.home, scratch / "runtime" / "sqlite", probe.work
    for path in (home, sqlite, work):
        path.mkdir(mode=0o700, parents=True)
    auth_copy = home / "auth.json"
    auth_copy.touch(mode=0o600)
    shutil.copyfile(auth, auth_copy)
    argv = app_server_argv(executable)
    private_json(scratch / "runtime.json", {"argv": argv, "codex_home": str(home), "sqlite_home": str(sqlite),
                                            "cwd": str(work), "auth_source": str(auth), "model": MODEL, "effort": EFFORT,
                                            "experiment_script_sha256": sha(Path(__file__).read_bytes())})
    private_json(scratch / "instructions.json", {"base": BASE, "developer": DEVELOPER, "schema": SCHEMA})
    fork = {"status": "not_requested" if not with_fork else "pending"}
    try:
        probe.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      cwd=work, env=CodexAppServerClient._process_env(home, sqlite), start_new_session=True)
        probe.rpc = ProbeRpc(probe.proc, probe.recorder)
        rpc = probe.rpc
        deadline = time.monotonic() + timeout
        rpc.request("initialize", {"clientInfo": {"name": "intelitex_session_cache_probe", "version": "1"},
                                   "capabilities": {"experimentalApi": True}}, deadline)
        rpc.send({"method": "initialized", "params": {}})
        disabled = isolate_skills(rpc, work, timeout)
        private_json(scratch / "isolation.json", {"disabled_skills": disabled, "skills_verified_disabled": True,
                                                 "flags": argv, "sandbox": "read-only", "approvalPolicy": "never"})
        a = probe.start_root("A1")
        a1 = probe.run_turn("A1", a, probe.long_text(), None, "fresh root warm")
        b = probe.start_root("B1")
        if a["thread_id"] == b["thread_id"] or a["session_id"] == b["session_id"]:
            raise PipelineError("B is not an independent root; refusing B1.")
        probe.run_turn("B1", b, probe.long_text(), "A1", "different fresh root")
        probe.recorder.select("A2")
        probe.run_turn("A2", a, FOLLOWUP, "A1", "same root/session as A1")
        if with_fork and protocol["ephemeral_anchored_fork_supported"]:
            try:
                probe.run_fork(a, a1)
                fork = {"status": "completed", "anchor": "lastTurnId=A1 inclusive, excluding A2"}
            except Exception as exc:
                fork = {"status": "failed", "reason": str(exc)}
                private_json(scratch / "fork.error.json", fork)
        elif with_fork:
            fork = {"status": "skipped", "reason": protocol["fork_skip_reason"]}
    except BaseException as exc:
        private_json(scratch / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        if probe.proc:
            CodexAppServerClient._cleanup_process(probe.proc, graceful=True)
        auth_copy.unlink(missing_ok=True)
        # Rollouts remain inspectable under the same private home after shutdown;
        # the explicitly authorized credential copy is always removed.
        private_json(scratch / "credential_cleanup.json", {"temporary_auth_removed": not auth_copy.exists()})
        audit_rollouts(scratch, probe.rows)
        probe.summary(fork)
    return read_json(scratch / "summary.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/home/user/translations/salvation-03"))
    parser.add_argument("--scratch", type=Path, required=True, help="Fresh directory outside repository and production project")
    parser.add_argument("--chapter", help="Complete canonical chapter ID; default selects an 80–200 kB chapter")
    parser.add_argument("--timeout", type=float, default=1200, help="Per-turn/RPC timeout in seconds")
    parser.add_argument("--no-fork", action="store_true", help="Skip the optional protocol-supported ephemeral fork")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        result = run(args.project, args.scratch, args.chapter, args.timeout, not args.no_fork)
    except (PipelineError, OSError, subprocess.SubprocessError, ValueError, jsonschema.ValidationError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), flush=True)
        return 1
    print(json.dumps({"scratch": str(args.scratch.resolve()), "classification": result["classification"],
                      "checks": result["checks"], "fork": result["fork"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
