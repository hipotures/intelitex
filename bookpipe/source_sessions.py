"""Durable source sessions, outcome-first cleanup and same-home native recovery.

The Store owns task/scope inventory; each manifest owns one native slot. JSON
inventory is a read-only projection, never a second independently writable registry.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from . import source_session_codec as codec
from .catalog import apply_estimate, load_catalog, pricing_snapshot
from .contracts import normalized_usage
from .codex_effort import rollout_effort_evidence, verify_reported_selection
from .evidence import AttemptRecorder, EvidenceError, recorded_model_submission, utc_now
from .progress import ProgressEvent
from .util import PipelineError, atomic_json, digest, file_lock, read_json

MODE = codec.WIRE_FORMAT
DEFAULT_EXECUTION = {"version": 1, "codex_mode": MODE, "source_scope_policy": "exact-existing-unit",
                     "revert_policy": "after-each-attempt", "p0_output_reserve": 256}
STATES = {"absent", "preparing", "baseline_ready", "submission_pending", "running",
          "terminal_uncommitted", "accepted_cleanup_pending", "failed_cleanup_pending",
          "reverting", "recovery_required"}


def validate_execution(settings):
    mode = settings.get("pipeline_execution")
    if mode is None:
        return False
    if not isinstance(mode, dict) or any(mode.get(k) != v for k, v in DEFAULT_EXECUTION.items() if k != "p0_output_reserve"):
        raise PipelineError("Unsupported pipeline_execution; source-session-v1 requires exact-existing-unit and after-each-attempt.")
    if type(mode.get("p0_output_reserve", 256)) is not int or mode.get("p0_output_reserve", 256) <= 0:
        raise PipelineError("P0 readiness output reserve must be a positive integer.")
    return True


def scope_for(store, key, inputs, unit_context=None):
    chapter = unit_context.chapter_id if unit_context else key.split("/", 1)[1].split("_", 1)[0]
    book = read_json(store.root / "book.json") if (store.root / "book.json").is_file() else {}
    revision = book.get("source_fingerprint", "scratch-source-v1")
    scope = codec.resolve_scope(inputs["SOURCE_BLOCKS"], chapter, revision)
    with store.db:
        if not store.get("source_project_uuid"):
            store.set("source_project_uuid", uuid4().hex)
        inventory = store.get("source_scope_inventory", {"version": 1, "tasks": {}, "scopes": {}})
        inventory["tasks"][key] = scope.scope_id
        inventory["scopes"][scope.scope_id] = {**scope.reference, "chapter_id": chapter}
        store.set("source_scope_inventory", inventory)
    atomic_json(store.root / "source_sessions.json", {"authority": "Store source_scope_inventory",
                                                     "project_uuid": store.get("source_project_uuid"), **inventory})
    root = store.root / "artifacts" / chapter / "sources" / scope.scope_id
    for path, value in ((root / "source.json", scope.package()), (root / "source-map.json", scope.source_map)):
        if path.exists():
            if read_json(path) != value:
                raise PipelineError("Immutable source package/map changed; restore source evidence before inference.")
        else:
            atomic_json(path, value)
    return scope


def protocol_capabilities(executable):
    """Schema feature checks, independent of version ordering and authentication."""
    version = subprocess.check_output([executable, "--version"], text=True, timeout=10).strip()
    required = {
        "ThreadStartParams": {"historyMode", "ephemeral", "baseInstructions", "dynamicTools", "environments", "selectedCapabilityRoots", "runtimeWorkspaceRoots"},
        "ThreadResumeParams": {"threadId", "excludeTurns", "runtimeWorkspaceRoots"},
        "TurnStartParams": {"threadId", "input", "outputSchema", "effort", "environments", "runtimeWorkspaceRoots"},
        "ThreadTurnsListParams": {"threadId", "cursor", "sortDirection", "itemsView"},
        "ThreadItemsListParams": {"threadId", "turnId", "cursor", "sortDirection"},
        "ThreadRevertParams": {"threadId", "beforeTurnId"},
        "TurnInterruptParams": {"threadId", "turnId"},
    }
    hashes = {}
    with tempfile.TemporaryDirectory(prefix="intelitex-source-schema-") as directory:
        subprocess.run([executable, "app-server", "generate-json-schema", "--experimental", "--out", directory],
                       capture_output=True, text=True, check=True, timeout=30)
        for name, fields in required.items():
            path = Path(directory) / "v2" / (name + ".json")
            if not path.is_file() or not fields <= set(read_json(path).get("properties", {})):
                raise PipelineError(f"Installed Codex lacks required persistent source-session capability {name}; no model turn submitted.")
            hashes[name] = digest(path.read_bytes())
        start = read_json(Path(directory) / "v2" / "ThreadStartParams.json")
        if "paginated" not in start["definitions"]["ThreadHistoryMode"]["enum"]:
            raise PipelineError("Installed Codex does not support paginated persistent history.")
    return {"cli_version": version, "schema_hashes": hashes}


def _birth(pid):
    try:
        # Linux start time prevents PID-reuse ownership mistakes.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class PersistentSourceSessionManager:
    def __init__(self, store, provider, scope):
        self.store, self.provider, self.scope = store, provider, scope
        self.proc = self.rpc = self.lease = None
        self.record = None
        self.executable = shutil.which(provider.executable)
        if not self.executable:
            raise PipelineError("Codex executable is unavailable; no source-session inference submitted.")
        self.protocol = protocol_capabilities(self.executable)
        # Effort is deliberately slot-bound until cross-effort baseline reuse is
        # established for every supported model. Display profile is not identity.
        from .codex_transport import app_server_argv
        self.compatibility = {"source": scope.reference, "executable": str(Path(self.executable).resolve()),
            "argv_contract": digest(app_server_argv(self.executable)), "base": digest(codec.BASE_INSTRUCTIONS),
            "developer": digest(codec.DEVELOPER_INSTRUCTIONS), "schema": digest(codec.TRANSPORT_SCHEMA),
            "p0_prompt": digest(self.p0_prompt), "protocol": self.protocol,
            "model": provider.model, "effort": provider.settings.get("reasoning_effort"),
            "auth_binding": provider.settings.get("options", {}).get("auth_source"),
            "sandbox": "read-only", "runtime_contract": 1}
        self.compatibility_id = digest(self.compatibility)
        self.generation = store.get("source_slot_generation:" + self.compatibility_id, 1)
        self.slot_id = digest({"compatibility": self.compatibility_id, "generation": self.generation})
        self.source_root = store.root / "artifacts" / scope.chapter_id / "sources" / scope.scope_id
        self.directory = self.source_root / "sessions" / self.slot_id
        self.manifest_path = self.directory / "manifest.json"
        configured = provider.settings.get("runtime_root")
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        runtime_base = Path(configured).expanduser() if configured else state / "intelitex" / "codex"
        self.runtime = (runtime_base / store.get("source_project_uuid") / scope.chapter_id / scope.scope_id / self.slot_id).resolve()
        if self.runtime.is_relative_to(store.root.resolve()) or self.runtime.is_relative_to(Path(__file__).resolve().parents[1]):
            raise PipelineError("Private source-session runtime_root must be outside the workspace and repository.")
        self.home, self.sqlite, self.work = (self.runtime / n for n in ("home", "sqlite", "work"))

    @property
    def p0_prompt(self):
        path = self.store.root / "prompts" / "pass0.txt"
        if not path.is_file():
            raise PipelineError("source-session-v1 requires prompts/pass0.txt; install it explicitly before opting in.")
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise PipelineError("P0 prompt is empty.")
        return text

    def transition(self, state, **changes):
        if state not in STATES:
            raise ValueError(state)
        row = {"timestamp": utc_now(), "from": self.record.get("state"), "to": state, **changes}
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Journal first; the manifest is the authoritative committed transition.
        with (self.directory / "transitions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.record.update(state=state, **changes)
        atomic_json(self.manifest_path, self.record)
        os.chmod(self.manifest_path, 0o600)

    def acquire(self):
        existed = self.manifest_path.is_file()
        self.record = read_json(self.manifest_path) if existed else {
            "version": 1, "project_uuid": self.store.get("source_project_uuid"), "scope": self.scope.reference,
            "slot_id": self.slot_id, "generation": self.generation, "compatibility_id": self.compatibility_id, "compatibility": self.compatibility,
            "runtime": str(self.runtime), "state": "absent", "p0_status": "absent", "submissions": []}
        if (self.record.get("compatibility") != self.compatibility or self.record.get("runtime") != str(self.runtime)):
            raise PipelineError("Source-session binding mismatch; preserve evidence and explicitly rebuild in a new generation.")
        if self.record.get("retired") or (self.record.get("state") == "recovery_required" and
                self.record.get("reason") == "compaction/context alteration"):
            raise PipelineError("Source session is retired or compacted; explicitly rebuild its generation before new inference.")
        if existed and self.record.get("thread_id") and any(not p.is_dir() for p in (self.home, self.sqlite, self.work)):
            raise PipelineError("Durable native source-session state is missing. Restore the complete private runtime including SQLite/WAL; no automatic P0 rebuild.")
        for path in (self.runtime, self.home, self.sqlite, self.work):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        self.lease = file_lock(self.runtime, "owner.lock", "Source session is busy; another owner holds its lease.")
        self.lease.__enter__()
        try:
            owner = self.record.get("process_owner")
            if owner and _birth(owner["pid"]) == owner.get("birth"):
                raise PipelineError("Source session has a surviving app-server owner; reconcile that process before retrying. No duplicate submission.")
            auth = self.provider.settings.get("options", {}).get("auth_source")
            if auth and not (self.home / "auth.json").exists():
                source = Path(auth).expanduser().resolve()
                if not source.is_file():
                    raise PipelineError("Configured authorized Codex authentication source is unavailable.")
                temporary = self.home / ".auth.tmp"
                with source.open("rb") as src, temporary.open("wb") as dst:
                    os.chmod(temporary, 0o600)
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(temporary, self.home / "auth.json")
            self.transition(self.record["state"])
        except BaseException:
            self.close()
            raise

    def connect(self, recorder):
        connected_at = time.monotonic()
        from . import codex_transport as transport
        self.proc = subprocess.Popen(transport.app_server_argv(self.executable), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.work,
            env=self.provider._process_env(self.home, self.sqlite), start_new_session=True)
        self.transition(self.record["state"], process_owner={"pid": self.proc.pid, "birth": _birth(self.proc.pid)})
        self.rpc = transport._RpcSession(self.proc, recorder, self.provider.ui)
        deadline = time.monotonic() + self.provider.timeout
        self.rpc.request("initialize", {"clientInfo": {"name": "intelitex", "title": "Intelitex", "version": "1.11.0"},
                                         "capabilities": {"experimentalApi": True}}, deadline)
        self.rpc.send({"method": "initialized", "params": {}})
        self.disabled_skills = set()
        for group in self.rpc.request("skills/list", {"cwds": [str(self.work)], "forceReload": True}, deadline).get("data", []):
            if group.get("errors"):
                raise PipelineError("Private Codex skill isolation failed.")
            for skill in group.get("skills", []):
                if skill.get("enabled"):
                    self.disabled_skills.add(skill["path"])
        for path in sorted(self.disabled_skills):
            if self.rpc.request("skills/config/write", {"path": path, "enabled": False}, deadline).get("effectiveEnabled") is not False:
                raise PipelineError("Private Codex skill remained enabled.")
        for group in self.rpc.request("skills/list", {"cwds": [str(self.work)], "forceReload": True}, deadline).get("data", []):
            if group.get("errors") or any(s.get("enabled") for s in group.get("skills", [])):
                raise PipelineError("Private Codex resumed skill isolation failed.")
        installed = self.provider._installed_effort_feature(self.executable, self.home, self.sqlite)
        self.effort_plan, self.effort_capability = self.provider._effort_plan(
            self.rpc, self.home, installed, self.provider.settings.get("reasoning_effort"), deadline)
        params = {"model": self.provider.model, "cwd": str(self.work), "sandbox": "read-only", "approvalPolicy": "never",
                  "baseInstructions": codec.BASE_INSTRUCTIONS, "developerInstructions": codec.DEVELOPER_INSTRUCTIONS,
                  "runtimeWorkspaceRoots": [], "config": self.effort_plan.thread_config(), "personality": "none"}
        if self.record.get("thread_id"):
            params.update(threadId=self.record["thread_id"], excludeTurns=True)
            try:
                result = self.rpc.request("thread/resume", params, deadline)
            except PipelineError as exc:
                # A started thread is not persisted until its first user turn.
                # Replacing this empty allocation is safe ONLY before any intent
                # was recorded. Once submission is possible, never reload source.
                submitted = any(recorded_model_submission(self.store.root / s["attempt"]) is not False
                                for s in self.record.get("submissions", []))
                if (self.record.get("p0_status") == "ready" or submitted or
                        "no rollout found for thread id" not in str(exc)):
                    raise
                if self.record.get("active"):
                    self.recover_submission([])
                recorder.event("local", "unmaterialized_thread_replaced", {"thread_id": self.record["thread_id"]})
                self.transition("preparing", thread_id=None, session_id=None, thread_path=None)
                params.pop("threadId")
                params.pop("excludeTurns")
                params.update(ephemeral=False, historyMode="paginated", dynamicTools=[], environments=[], selectedCapabilityRoots=[])
                result = self.rpc.request("thread/start", params, deadline)
        else:
            params.update(ephemeral=False, historyMode="paginated", dynamicTools=[], environments=[], selectedCapabilityRoots=[])
            result = self.rpc.request("thread/start", params, deadline)
        thread = result.get("thread", {})
        if not thread.get("id") or not thread.get("path") or thread.get("ephemeral") is True:
            raise PipelineError("Codex did not confirm a persistent native source thread.")
        if self.record.get("thread_id") and (thread["id"] != self.record["thread_id"] or thread.get("sessionId") != self.record.get("session_id")):
            raise PipelineError("Cold resume changed the native binding; no source reload is allowed.")
        if not Path(thread["path"]).resolve().is_relative_to(self.home):
            raise PipelineError("Codex source thread path escapes its private home.")
        self.rpc.state.update(thread_id=thread["id"], session_id=thread.get("sessionId"), thread_path=thread["path"],
                              reported_model=result.get("model"), reported_effort=result.get("reasoningEffort"))
        self.transition(self.record["state"], thread_id=thread["id"], session_id=thread.get("sessionId"), thread_path=thread["path"])
        recorder.artifact_json("session.protocol.json", self.protocol)
        recorder.event("local", "session_connection", {"method": "thread/resume" if "threadId" in params else "thread/start", "params": params})
        self.history()  # Prove pagination/storage support before spending tokens.
        self.timing("resume" if "threadId" in params else "preparation_connection", connected_at, recorder)
        if "threadId" in params:
            self.provider.ui.emit(ProgressEvent(kind="source_session_resumed", values={
                "scope_id": self.scope.scope_id, "thread_id": thread["id"]}))
        return deadline

    def timing(self, phase, started, recorder=None):
        recorder = recorder or self.rpc.recorder
        path = recorder.directory / "session.timings.json"
        timings = read_json(path) if path.is_file() else {}
        timings.setdefault(phase, []).append(time.monotonic() - started)
        recorder.artifact_json("session.timings.json", timings)

    def history(self):
        """All pages, with all item pages separately (summary is insufficient)."""
        deadline = time.monotonic() + min(self.provider.timeout, 30)
        turns, cursor, seen = [], None, set()
        while True:
            try:
                page = self.rpc.request("thread/turns/list", {"threadId": self.record["thread_id"],
                    "limit": 100, "sortDirection": "asc", "itemsView": "notLoaded", "cursor": cursor}, deadline)
            except PipelineError as exc:
                # 0.160.0 materializes a new paginated thread on its first user
                # turn. This precise no-turn response is not a history proof.
                if not self.record.get("active") and self.record.get("p0_status") != "ready" and "unavailable before first user message" in str(exc):
                    return []
                raise
            turns.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise PipelineError("Native turn pagination repeated a cursor.")
            seen.add(cursor)
        if len({t["id"] for t in turns}) != len(turns):
            raise PipelineError("Native history has duplicate turn identities.")
        for turn in turns:
            items, cursor, seen = [], None, set()
            while True:
                page = self.rpc.request("thread/items/list", {"threadId": self.record["thread_id"],
                    "turnId": turn["id"], "limit": 100, "sortDirection": "asc", "cursor": cursor}, deadline)
                entries = page.get("data", [])
                if any(e.get("turnId") != turn["id"] or not isinstance(e.get("item"), dict) for e in entries):
                    raise PipelineError("Native item page has a foreign turn binding.")
                items.extend(e["item"] for e in entries)
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise PipelineError("Native item pagination repeated a cursor.")
                seen.add(cursor)
            turn["items"] = items
        return turns

    @staticmethod
    def user_text(turn):
        return [c["text"] for item in turn.get("items", []) if item.get("type") == "userMessage"
                for c in item.get("content", []) if c.get("type") == "text"]

    @staticmethod
    def final_text(turn):
        finals = [i["text"] for i in turn.get("items", []) if i.get("type") == "agentMessage" and i.get("phase") == "final_answer"]
        fallback = [i["text"] for i in turn.get("items", []) if i.get("type") == "agentMessage" and i.get("phase") is None]
        return (finals or fallback or [None])[-1]

    def proof(self, turn):
        source = codec.readiness_message(self.scope, self.p0_prompt)
        if turn.get("status") != "completed" or self.user_text(turn) != [source]:
            raise PipelineError("Native P0 source/status binding is missing or changed; session held for recovery.")
        codec.decode_output(json.loads(self.final_text(turn) or "null"), 0)
        if any(i.get("type") not in {"userMessage", "agentMessage", "reasoning"} for i in turn.get("items", [])):
            raise PipelineError("Unexpected native item/compaction in the P0 baseline.")
        logical = {"turn_id": turn["id"], "source": source, "ready": codec.READY}
        fingerprint = digest(logical)
        if self.record.get("baseline_digest") and fingerprint != self.record["baseline_digest"]:
            raise PipelineError("Retained P0 logical history differs from its accepted baseline.")
        return fingerprint

    def submit(self, text, pass_no, directory, recorder):
        """Intent is durable before turn/start; its exact text is recoverable."""
        request = {"threadId": self.record["thread_id"], "input": [{"type": "text", "text": text}],
            "model": self.provider.model, "effort": self.provider.settings.get("reasoning_effort"),
            "cwd": str(self.work), "environments": [], "runtimeWorkspaceRoots": [], "outputSchema": codec.TRANSPORT_SCHEMA}
        semantic = read_json(directory / "request.semantic.json")
        submission = {"intent_id": uuid4().hex, "input_sha256": digest(text), "text": text,
            "pass_no": pass_no, "attempt": str(directory.relative_to(self.store.root)),
            "task_key": semantic["task_key"], "fingerprint": semantic["task_fingerprint"], "turn_id": None,
            "dependency_hashes": {k: digest(v) for k, v in semantic["input_payload"].items() if k in (
                "EXISTING_MEMORY", "APPROVED_LEXICON", "OBSERVATIONS", "PREVIOUS_CONTEXT", "SEMANTIC_AUDIT", "POLISH_DRAFT", "CORRECTION_LEDGER")}}
        self.transition("submission_pending", active=submission, submissions=[*self.record["submissions"], submission])
        recorder.transport_request({"wire_format": MODE, "execution_strategy": MODE, "turn": request,
            "thread_id": self.record["thread_id"], "session_id": self.record["session_id"], "slot_id": self.slot_id,
            "base_instructions": codec.BASE_INSTRUCTIONS, "developer_instructions": codec.DEVELOPER_INSTRUCTIONS}, codec.TRANSPORT_SCHEMA)
        self.rpc.reset_turn(recorder, self.record["thread_id"])
        self.rpc.state["turn_submitted"] = True
        started = time.monotonic()
        deadline = started + self.provider.timeout
        turn = self.rpc.request("turn/start", request, deadline).get("turn", {})
        self.rpc.bind_turn(turn.get("id"))
        submission["turn_id"] = self.rpc.state["turn_id"]
        self.transition("running", active=submission, submissions=[*self.record["submissions"][:-1], submission])
        self.rpc.drain_until_terminal(deadline)
        self.rpc.wait_late_usage(float(self.provider.settings.get("options", {}).get("late_usage_wait", .75)))
        turns = self.history()
        matches = [t for t in turns if t["id"] == submission["turn_id"]]
        if len(matches) != 1 or self.user_text(matches[0]) != [text]:
            raise PipelineError("Submitted turn has no exact durable native intent binding.")
        terminal = matches[0]
        recorder.artifact_json("native.history.json", turns)
        answer = self.final_text(terminal)
        if answer:
            recorder.write_answer(answer)
        self.save_usage(recorder)
        observation = rollout_effort_evidence(Path(self.record["thread_path"]), submission["turn_id"])
        settings = self.rpc.state.get("turn_settings") or {}
        reported_model = observation["rollout_model"] or settings.get("model") or self.rpc.state.get("reported_model")
        reported_effort = observation["rollout_effort"] or settings.get("effort")
        if reported_effort is None and self.effort_plan.mode == "request_level":
            reported_effort = self.rpc.state.get("reported_effort")
        verify_reported_selection(self.provider.model, self.provider.settings.get("reasoning_effort"), reported_model, reported_effort)
        if self.provider.settings.get("reasoning_effort") and reported_effort is None:
            raise PipelineError("Codex did not provide verifiable applied effort evidence; session retained for recovery.")
        meta = {"provider": "codex", "wire_format": MODE, "codec_map_version": codec.MAP_VERSION,
            "execution_strategy": MODE, "cli_version": self.protocol["cli_version"], "requested_model": self.provider.model,
            "reported_model": reported_model, "reported_effort": reported_effort, **self.effort_plan.metadata(), **observation,
            "reasoning_effort_capability": self.effort_capability,
            "thread_id": self.record["thread_id"], "session_id": self.record["session_id"], "turn_id": submission["turn_id"],
            "scope_id": self.scope.scope_id, "slot_id": self.slot_id, "p0_turn_id": self.record.get("p0_turn_id"),
            "session_manifest": str(self.manifest_path.relative_to(self.store.root)),
            "accepted_attempt": str(directory.relative_to(self.store.root)),
            "source_message_utf8_bytes_added": len(text.encode()) if pass_no == 0 else 0,
            "retained_source_utf8_bytes": len(codec.readiness_message(self.scope, self.p0_prompt).encode()),
            "dynamic_suffix_utf8_bytes": 0 if pass_no == 0 else len(text.encode()),
            "status": terminal.get("status"), "finish_reason": "stop" if terminal.get("status") == "completed" else terminal.get("status"),
            "usage_status": read_json(directory / "usage.json")["status"], "elapsed_seconds": time.monotonic() - started,
            "isolation": {"skills_verified_disabled": True, "skills_disabled": len(self.disabled_skills),
                "sandbox": "read-only", "approval_policy": "never", "dynamic_tools": [], "environments": [],
                "runtime_workspace_roots": [], "capability_roots": [], "ephemeral": False}}
        recorder.artifact_json("response_meta.json", meta)
        self.timing("inference", started, recorder)
        self.transition("terminal_uncommitted", active={**submission, "terminal_status": terminal.get("status")})
        if self.rpc.state["context_altered"]:
            self.transition("recovery_required", reason="compaction/context alteration")
            raise PipelineError("Native source history was compacted; preserve evidence and rebuild explicitly.")
        if terminal.get("status") != "completed" or not answer or self.rpc.state.get("terminal_error"):
            raise PipelineError("Codex source-session turn did not complete with a final answer; evidence retained for recovery.")
        return answer, meta

    def save_usage(self, recorder):
        events = self.rpc.state.get("usage_events", [])
        last = ((events[-1] or {}).get("last") or {}) if events else {}
        usage = normalized_usage(input_tokens=last.get("inputTokens"), cached_input_tokens=last.get("cachedInputTokens"),
            cache_write_input_tokens=last.get("cacheWriteInputTokens"), output_tokens=last.get("outputTokens"),
            reasoning_output_tokens=last.get("reasoningOutputTokens"), total_tokens=last.get("totalTokens"),
            source="codex_thread_token_usage_last", scope="correlated_turn_last_internal_operation",
            status="reported" if events else "unavailable")
        usage["model_context_window"] = events[-1].get("modelContextWindow") if events else None
        recorder.usage(events, usage)
        pricing = recorder.directory / "pricing.json"
        if pricing.exists():
            recorder.pricing(apply_estimate(read_json(pricing), usage))

    def ensure_p0(self):
        turns = self.history()
        if self.record.get("p0_status") == "ready":
            if not turns or turns[0]["id"] != self.record["p0_turn_id"]:
                raise PipelineError("Native source-session P0 is missing; restore native state or explicitly rebuild.")
            self.proof(turns[0])
            return
        self.provider.ui.emit(ProgressEvent(kind="source_preload_started", values={"pass_no": 0, "scope_id": self.scope.scope_id}))
        p0_fp = self.slot_id
        root = self.source_root / "pass0" / p0_fp
        active = self.record.get("active")
        if active:
            if active["pass_no"] != 0:
                raise PipelineError("Unready P0 has a foreign active submission.")
            self.recover_submission(turns)
            if not self.record.get("active"):
                return self.ensure_p0()
            directory = self.store.root / active["attempt"]
            answer = (directory / "answer.txt").read_text()
            meta = read_json(directory / "response_meta.json")
            recorder = AttemptRecorder.resume(directory)
        else:
            if turns:
                raise PipelineError("Unowned native history before P0; no blind preload is allowed.")
            self.transition("preparing", p0_status="preparing")
            number = len(list(root.glob("attempt_*"))) + 1
            directory = root / f"attempt_{number:03d}"
            recorder = AttemptRecorder(directory, {"pass": 0, "pass_no": 0, "task_key": "pass0/" + self.scope.scope_id,
                "task_fingerprint": p0_fp, "unit_id": self.scope.scope_id, "chapter_id": self.scope.chapter_id,
                "scope_id": self.scope.scope_id, "parent_consumer_stage": self.consumer_pass,
                "provider": "codex", "profile": self.provider.profile_name, "requested_model": self.provider.model,
                "attempt_number": number, "attempt_id": f"{p0_fp[:20]}-{number:03d}"})
            recorder.semantic({"task_key": "pass0/" + self.scope.scope_id, "task_fingerprint": p0_fp,
                "pass_no": 0, "input_payload": self.scope.package(), "resolved_profile": self.provider.resolved_profile}, codec.TRANSPORT_SCHEMA)
            catalog, path = load_catalog(self.store.root)
            recorder.pricing(pricing_snapshot(catalog, path, "codex", self.provider.model))
            # Also record source-preload sizing; consumer preflight already proved
            # retained P0 + its complete dynamic context fits before this call.
            text = codec.readiness_message(self.scope, self.p0_prompt)
            body = {"developer_instructions": codec.DEVELOPER_INSTRUCTIONS, "input": text,
                    "output_schema": codec.TRANSPORT_SCHEMA, "wire_format": MODE, "codec_context": {},
                    "retained_p0_utf8_bytes": 0, "planning_output_reserve": self.provider.settings.get("pipeline_execution", {}).get("p0_output_reserve", 256)}
            value = self.provider.preflight(body, recorder)
            recorder.preflight({"value": value, "unit": "utf8_bytes", "quality": "conservative_upper_bound"})
            answer, meta = self.submit(text, 0, directory, recorder)
        codec.decode_output(json.loads(answer), 0)
        turns = self.history()
        if len(turns) != 1:
            raise PipelineError("P0 readiness requires exactly one durable native source turn.")
        baseline = self.proof(turns[0])
        result = root / "result.json"
        atomic_json(result, {"version": 1, **self.scope.reference, "ready": codec.READY,
                             "thread_id": self.record["thread_id"], "p0_turn_id": turns[0]["id"], "baseline_digest": baseline})
        recorder.finish(generation="completed", validation="passed", metadata=meta)
        self.store.save_job("pass0/" + self.scope.scope_id, p0_fp, result, meta)
        recorder.mark_accepted()
        self.transition("baseline_ready", p0_status="ready", p0_turn_id=turns[0]["id"], baseline_digest=baseline,
                        p0_attempt=str(directory.relative_to(self.store.root)), active=None, cleanup_required=False,
                        native_context_window=read_json(directory / "usage.json").get("model_context_window"))
        self.provider.ui.emit(ProgressEvent(kind="source_preload_completed", values={"pass_no": 0, "scope_id": self.scope.scope_id}))
        if meta.get("usage_status") == "unavailable":
            with self.store.db:
                self.store.set("observability_hold", {"attempt": str(directory.relative_to(self.store.root)),
                                                      "reason": "P0 completed without expected usage evidence."})
            if self.provider.settings.get("allow_incomplete_observability", False):
                return
            raise PipelineError("P0 completed without expected usage; evidence retained, explicit observability override required.")

    def recover_submission(self, turns):
        active = self.record.get("active")
        if not active:
            return
        matches = [t for t in turns if self.user_text(t) == [active["text"]] and
                   (not active.get("turn_id") or t["id"] == active["turn_id"])]
        directory = self.store.root / active["attempt"]
        if (not matches and len(turns) == (1 if self.record.get("p0_status") == "ready" else 0)
                and recorded_model_submission(directory) is False):
            # send() fsyncs the outbound RPC event before writing to native stdin.
            # Its absence in a complete readable log proves this intent unsent.
            recorder = AttemptRecorder.resume(directory)
            recorder.finish(generation="not_submitted", validation="not_run", metadata={
                "provider": "codex", "wire_format": MODE, "model_turn_submitted": False,
                "status": "not_submitted", "usage_status": "not_submitted"})
            self.transition("baseline_ready" if turns else "preparing", active=None, cleanup_required=False)
            return
        if len(matches) != 1:
            self.transition("recovery_required", reason="ambiguous submission intent")
            raise PipelineError("Source-session submission outcome is uncertain; inspect native intent/history before any retry. No duplicate turn submitted.")
        turn = matches[0]
        if turn.get("status") not in ("completed", "interrupted", "failed"):
            self.transition("recovery_required", reason="native turn still active")
            raise PipelineError("Source-session turn is still active; no duplicate submission or revert.")
        active = {**active, "turn_id": turn["id"], "terminal_status": turn["status"]}
        submissions = [({**s, "turn_id": turn["id"]} if s["intent_id"] == active["intent_id"] else s) for s in self.record["submissions"]]
        self.transition("terminal_uncommitted", active=active, submissions=submissions)
        directory = self.store.root / active["attempt"]
        atomic_json(directory / "native.history.json", turns)
        answer = self.final_text(turn)
        if answer:
            # Don't overwrite original terminal evidence on recovery.
            if not (directory / "answer.txt").is_file():
                from .util import atomic_text
                atomic_text(directory / "answer.txt", answer.rstrip() + "\n")
        if not (directory / "response_meta.json").is_file() or read_json(directory / "response_meta.json").get("finish_reason") != "stop":
            # Recover physical usage ONLY from correlated recorded inbound events.
            # Resume's cumulative snapshot must never be billed to the old turn.
            usage_events = []
            log = directory / "transport.jsonl"
            if log.is_file():
                for line in log.read_text().splitlines():
                    row = json.loads(line)
                    payload = row.get("payload") or {}
                    if isinstance(payload, dict) and payload.get("method") == "thread/tokenUsage/updated":
                        params = payload.get("params") or {}
                        if params.get("threadId") == self.record["thread_id"] and params.get("turnId") == turn["id"]:
                            usage_events.append(params["tokenUsage"])
            previous = self.rpc.state["usage_events"]
            self.rpc.state["usage_events"] = usage_events
            recorder = AttemptRecorder.resume(directory)
            self.save_usage(recorder)
            self.rpc.state["usage_events"] = previous
            observation = rollout_effort_evidence(Path(self.record["thread_path"]), turn["id"])
            meta = {"provider": "codex", "wire_format": MODE, "codec_map_version": codec.MAP_VERSION,
                    "finish_reason": "stop" if turn["status"] == "completed" else turn["status"],
                    "status": turn["status"], "thread_id": self.record["thread_id"], "session_id": self.record["session_id"],
                    "turn_id": turn["id"], "scope_id": self.scope.scope_id, "slot_id": self.slot_id,
                    "source_message_utf8_bytes_added": len(active["text"].encode()) if active["pass_no"] == 0 else 0,
                    "reported_model": observation["rollout_model"], "reported_effort": observation["rollout_effort"],
                    "cli_version": self.protocol["cli_version"], "recovered_native": True,
                    "session_manifest": str(self.manifest_path.relative_to(self.store.root)),
                    "accepted_attempt": str(directory.relative_to(self.store.root)),
                    "usage_status": "reported" if usage_events else "unavailable"}
            recorder.finish(generation="completed" if turn["status"] == "completed" else "failed", validation="not_run", metadata=meta)
        if active["pass_no"] and self.store.job(active["task_key"], active["fingerprint"]):
            self.transition("accepted_cleanup_pending", active=active, cleanup_required=True)
        elif active["pass_no"] and turn["status"] != "completed":
            self.transition("failed_cleanup_pending", active=active, cleanup_required=True)

    def reconcile(self):
        turns = self.history()
        if not turns:
            raise PipelineError("Ready source session has no native P0 history.")
        self.proof(turns[0])
        if len(turns) == 1 and self.record.get("cleanup_required") and self.record["state"] in (
                "reverting", "accepted_cleanup_pending", "failed_cleanup_pending"):
            # Revert may already have committed natively before its RPC reply
            # or our local clean marker was lost. Its removed turn is no longer
            # expected in active history; verify the durable outcome instead.
            active = self.record.get("active") or {}
            evidence = read_json(self.store.root / active["attempt"] / "attempt.json") if active else {}
            if not active or not evidence.get("evidence_complete") or not (
                    self.store.job(active["task_key"], active["fingerprint"]) or
                    evidence.get("lifecycle", {}).get("validation") == "failed"):
                raise PipelineError("Removed native suffix lacks a durable accepted/failed outcome.")
            self.cleanup()
            return "clean"
        if self.record.get("active"):
            self.recover_submission(turns)
            if not self.record.get("active"):
                return "clean"
            active = self.record["active"]
            manifest = read_json(self.store.root / active["attempt"] / "attempt.json")
            if manifest.get("lifecycle", {}).get("validation") == "failed":
                self.transition("failed_cleanup_pending", cleanup_required=True)
            if self.record["state"] == "terminal_uncommitted":
                # Exact completed answer must go through Runner acceptance before
                # cleanup or a new generation. Caller can recover it without tokens.
                return "completed_unaccepted"
        if self.record.get("cleanup_required") or len(turns) > 1:
            self.cleanup()
        else:
            self.transition("baseline_ready", cleanup_required=False, active=None)
        return "clean"

    def cleanup(self):
        cleanup_started = time.monotonic()
        self.provider.ui.emit(ProgressEvent(kind="source_session_cleanup_required", values={"scope_id": self.scope.scope_id}))
        turns = self.history()
        if not turns or turns[0]["id"] != self.record["p0_turn_id"]:
            raise PipelineError("Cannot revert: accepted P0 boundary is missing.")
        self.proof(turns[0])
        known = {s.get("turn_id"): s for s in self.record["submissions"] if s.get("turn_id")}
        for turn in turns[1:]:
            submission = known.get(turn["id"])
            if (not submission or self.user_text(turn) != [submission["text"]] or
                    turn.get("status") not in ("completed", "failed", "interrupted")):
                self.transition("recovery_required", reason="unknown or active native suffix")
                raise PipelineError("Unknown/foreign or active turn in source session; no blind revert is permitted.")
            attempt = self.store.root / submission["attempt"]
            manifest = read_json(attempt / "attempt.json")
            if not manifest.get("evidence_complete"):
                raise PipelineError("Cannot revert before terminal evidence is durably complete.")
            if turn.get("status") == "completed" and not (
                self.store.job(submission["task_key"], submission["fingerprint"]) or
                manifest.get("lifecycle", {}).get("validation") == "failed"):
                raise PipelineError("Completed source-session output needs canonical acceptance before revert.")
        if len(turns) > 1:
            boundary = turns[1]["id"]
            self.transition("reverting", revert_boundary=boundary, cleanup_required=True)
            result = self.rpc.request("thread/revert", {"threadId": self.record["thread_id"], "beforeTurnId": boundary},
                                      time.monotonic() + min(self.provider.timeout, 30))
            if (result.get("thread") or {}).get("path"):
                self.transition("reverting", thread_path=result["thread"]["path"])
            turns = self.history()
        if len(turns) != 1:
            raise PipelineError("Revert did not leave exactly the accepted P0 baseline.")
        self.proof(turns[0])
        self.rpc.recorder.artifact_json("native.baseline.json", turns)
        self.transition("baseline_ready", active=None, cleanup_required=False, revert_boundary=None,
                        last_verified_at=utc_now(), retained_active_turns=1)
        self.timing("cleanup", cleanup_started)

    def generate(self, body, directory, recorder):
        self.consumer_pass = body["pass_no"]
        self.acquire()
        try:
            self.connect(recorder)
            self.ensure_p0()
            native_window = self.record.get("native_context_window")
            if type(native_window) is int and 0 < native_window < self.provider.context:
                configured_context = self.provider.context
                try:
                    self.provider.context = native_window
                    # Narrow the complete retained-context bound using actual
                    # native evidence when it becomes available after P0.
                    self.provider.preflight(body, recorder)
                finally:
                    self.provider.context = configured_context
            if getattr(self.provider.ui, "stop_requested", False):
                raise KeyboardInterrupt
            if getattr(self.provider.ui, "reload_requested", False):
                from .application.pipeline import ReloadAtCheckpoint
                raise ReloadAtCheckpoint(0)
            outcome = self.reconcile()
            if outcome == "completed_unaccepted":
                active = self.record["active"]
                if active["task_key"] != recorder.identity["task_key"] or active["fingerprint"] != recorder.identity["task_fingerprint"]:
                    raise PipelineError("An earlier completed source-session task must be recovered before this task can generate.")
                source = self.store.root / active["attempt"]
                # Preserve original physical attempt identity and do not fabricate usage.
                raise PendingSourceRecovery(source)
            self.rpc.recorder = recorder
            return self.submit(body["input"], body["pass_no"], directory, recorder)
        except BaseException:
            self.close()
            raise

    def recover_only(self):
        """Reconcile an existing slot before Runner searches completed evidence.

        This never prepares a missing P0 or submits a model turn.
        """
        if not self.manifest_path.is_file():
            return
        self.acquire()
        retain = False
        try:
            control = self.directory / "reconciliations" / uuid4().hex
            recorder = AttemptRecorder(control, {"operation": "source_session_reconcile", "model_turn": False})
            self.connect(recorder)
            turns = self.history()
            if self.record.get("p0_status") == "ready":
                retain = self.reconcile() == "completed_unaccepted"
            elif self.record.get("active"):
                self.recover_submission(turns)
            elif turns:
                raise PipelineError("Unowned native source history; preparation outcome needs recovery.")
            recorder.finish(generation="not_run", validation="not_applicable", metadata={"model_turn": False})
        finally:
            if not retain:
                self.close()

    def cleanup_saved(self):
        """Canonical acceptance is already durable; perform RPC-only cleanup."""
        if not self.manifest_path.is_file():
            return
        saved = read_json(self.manifest_path)
        if saved.get("state") == "baseline_ready" and not saved.get("active") and not saved.get("cleanup_required"):
            return
        if self.lease:
            try:
                self.reconcile()
            finally:
                self.close()
            return
        self.acquire()
        try:
            recorder = AttemptRecorder(self.directory / "reconciliations" / uuid4().hex,
                                       {"operation": "accepted_source_cleanup", "model_turn": False})
            self.connect(recorder)
            self.reconcile()
            recorder.finish(generation="not_run", validation="not_applicable", metadata={"model_turn": False})
        finally:
            self.close()

    def finalize(self, accepted):
        if not self.lease:
            return
        try:
            self.transition("accepted_cleanup_pending" if accepted else "failed_cleanup_pending", cleanup_required=True)
            self.cleanup()
        finally:
            self.close()

    def close(self):
        try:
            self._close_process()
        finally:
            if self.lease:
                self.lease.__exit__(None, None, None)
                self.lease = None

    def _close_process(self):
        if self.proc is not None:
            if self.rpc and self.rpc.state.get("turn_id") and self.rpc.state.get("terminal") is None:
                try:
                    self.rpc.request("turn/interrupt", {"threadId": self.record["thread_id"], "turnId": self.rpc.state["turn_id"]}, time.monotonic() + 1)
                except Exception:
                    pass
            self.provider._cleanup_process(self.proc, graceful=True)
            self.proc = None
            if self.record:
                self.transition(self.record["state"], process_owner=None)


class PendingSourceRecovery(PipelineError):
    def __init__(self, attempt):
        self.attempt = attempt
        super().__init__("A completed native source-session answer was recovered; canonical acceptance must precede any new inference.")


def session_inventory(root):
    """Safe, read-only diagnostics; private paths and native content stay local."""
    rows = []
    for path in sorted(root.glob('artifacts/*/sources/*/sessions/*/manifest.json')):
        record = read_json(path)
        rows.append({"manifest": str(path.relative_to(root)), **{k: record.get(k) for k in (
            "version", "scope", "slot_id", "generation", "state", "p0_status", "thread_id", "session_id",
            "p0_turn_id", "cleanup_required", "retained_active_turns", "last_verified_at", "retired")},
            "model": record.get("compatibility", {}).get("model"),
            "effort": record.get("compatibility", {}).get("effort")})
    return {"format_version": 1, "sessions": rows, "model_turn": False}


def reconcile_analysis_before_reset(store, ui):
    """Don't archive a P1 attempt still referenced by active native history."""
    from .codex_transport import CodexAppServerClient
    for path in sorted(store.root.glob('artifacts/*/sources/*/sessions/*/manifest.json')):
        record = read_json(path)
        active = record.get('active') or {}
        if record.get('retired') or active.get('pass_no') != 1:
            continue
        semantic = read_json(store.root / active['attempt'] / 'request.semantic.json')
        profile = copy.deepcopy(semantic['resolved_profile'])
        source_root = path.parents[2]
        package = read_json(source_root / 'source.json')
        scope = codec.SourceScope(package['chapter_id'], package['scope_id'], package['source_sha256'],
            read_json(source_root / 'source-map.json'), package['source'], package['canonical_blocks'])
        provider = CodexAppServerClient({**profile, 'resolved_profile': profile,
            'profile_name': semantic['profile'], 'project_root': str(store.root)}, ui)
        manager = None
        try:
            manager = PersistentSourceSessionManager(store, provider, scope)
            if manager.manifest_path != path:
                raise PipelineError('P1 source slot changed; reconcile its recorded generation before reset.')
            manager.cleanup_saved()
            if manager.record.get('state') != 'baseline_ready':
                raise PipelineError('P1 has an unaccepted native answer; recover its outcome before resetting analysis.')
        finally:
            if manager:
                manager.close()
            provider.close()


def retire_session(store, slot_id, *, rebuild=False, purge=False):
    """Explicit locked maintenance; preserve all ordinary results and evidence."""
    import re
    if not isinstance(slot_id, str) or not re.fullmatch('[a-f0-9]{64}', slot_id):
        raise PipelineError("Specify one complete source-session slot ID from source-sessions inspect.")
    paths = list(store.root.glob(f'artifacts/*/sources/*/sessions/{slot_id}/manifest.json'))
    if len(paths) != 1:
        raise PipelineError("Source-session slot is missing or ambiguous.")
    path = paths[0]
    record = read_json(path)
    runtime = Path(record['runtime'])
    expected = (store.get('source_project_uuid'), path.parts[-6], record['scope']['scope_id'], slot_id)
    if tuple(runtime.parts[-4:]) != expected or runtime.is_symlink() or runtime.resolve().is_relative_to(store.root.resolve()):
        raise PipelineError("Unsafe private runtime binding; no maintenance performed.")
    if purge and not record.get('retired'):
        raise PipelineError("Archive or rebuild the slot before an explicit destructive purge.")
    if not runtime.is_dir() and purge:
        raise PipelineError("Private runtime already missing; retain the retired manifest as evidence.")
    with file_lock(runtime, 'owner.lock', 'Source session is busy; maintenance refuses active owners.'):
        owner = record.get('process_owner')
        if owner and _birth(owner['pid']) == owner.get('birth'):
            raise PipelineError("Source session has a live app-server owner; maintenance refused.")
        if purge and record.get('state') in ('running', 'submission_pending'):
            raise PipelineError("Uncertain/incomplete native submission cannot be purged.")
        if rebuild:
            identity = record['compatibility_id']
            with store.db:
                store.set('source_slot_generation:' + identity,
                          max(store.get('source_slot_generation:' + identity, 1), record['generation']) + 1)
        record.update(retired=True, retired_at=utc_now(), retirement='explicit_rebuild' if rebuild else 'explicit_archive')
        atomic_json(path, record)
        if purge:
            shutil.rmtree(runtime)
            record.update(purged_at=utc_now())
            atomic_json(path, record)
    return {"slot_id": slot_id, "retired": True, "rebuild": rebuild, "purged": purge, "model_turn": False}
