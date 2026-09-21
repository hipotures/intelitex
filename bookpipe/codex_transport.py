from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import normalized_usage
from .evidence import AttemptRecorder, redact
from .p1_compact import build_transport
from .util import PipelineError, atomic_text, dumps


APP_SERVER_DISABLE_FLAGS = [
    "apps", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "computer_use", "in_app_browser", "image_generation", "multi_agent",
    "multi_agent_v2", "plugins", "remote_plugin", "plugin_sharing", "skill_search",
    "skill_mcp_dependency_install", "shell_tool", "shell_snapshot", "unified_exec",
    "code_mode_host", "goals", "hooks", "memories", "tool_suggest",
    "workspace_dependencies",
]
BASE_INSTRUCTIONS = Path(__file__).resolve().parent / "resources" / "codex_base_instructions.txt"


def app_server_argv(executable: str) -> list[str]:
    argv = [executable, "app-server", "--stdio", "--strict-config"]
    for flag in APP_SERVER_DISABLE_FLAGS:
        argv.extend(["--disable", flag])
    argv.extend([
        "-c", "project_doc_max_bytes=0",
        "-c", "project_doc_fallback_filenames=[]",
        "-c", 'web_search="disabled"',
        "-c", "mcp_servers={}",
    ])
    return argv


class _RpcSession:
    def __init__(self, proc: subprocess.Popen, recorder: AttemptRecorder):
        self.proc = proc
        self.recorder = recorder
        self.lines: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
        self.next_id = 0
        self.stderr = bytearray()
        self.notifications: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {
            "thread_id": None, "session_id": None, "turn_id": None, "thread_path": None,
            "reported_model": None, "reported_effort": None, "model_provider": None, "cli_version": None,
            "terminal": None, "terminal_error": None, "final_messages": [],
            "fallback_messages": [], "usage_events": [], "context_altered": False,
        }
        self._threads = [
            threading.Thread(target=self._read, args=("stdout", proc.stdout), daemon=True),
            threading.Thread(target=self._read, args=("stderr", proc.stderr), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read(self, name: str, stream) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                self.lines.put((name, line))
        finally:
            self.lines.put((name, None))

    def send(self, message: dict[str, Any], *, kind: str = "rpc") -> None:
        self.recorder.event("outbound", kind, message, {"rpc_id": message.get("id")})
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        try:
            self.proc.stdin.write(raw)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PipelineError(f"Codex app-server stdin failed: {exc}") from exc

    def request(self, method: str, params: dict[str, Any], deadline: float) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.next_message(deadline)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    raise PipelineError(f"Codex RPC {method} failed: {message['error']}")
                return message["result"]
            self.dispatch(message)

    def next_message(self, deadline: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipelineError("Codex app-server timed out before terminal turn completion.")
            try:
                source, raw = self.lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise PipelineError(f"Codex app-server exited early with status {self.proc.returncode}.")
                continue
            if source == "stderr":
                if raw:
                    self.stderr.extend(raw[: max(0, 65536 - len(self.stderr))])
                    self.recorder.event("inbound", "stderr", raw.decode("utf-8", "replace"))
                continue
            if raw is None:
                raise PipelineError("Codex app-server stdout reached EOF before terminal completion.")
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            try:
                message = json.loads(text)
            except json.JSONDecodeError as exc:
                self.recorder.event("inbound", "malformed_jsonl", text)
                raise PipelineError(f"Malformed Codex JSONL: {exc}") from exc
            if not isinstance(message, dict):
                raise PipelineError("Codex app-server emitted a non-object JSONL message.")
            self.recorder.event("inbound", "rpc", message, {
                "rpc_id": message.get("id"),
                "thread_id": (message.get("params") or {}).get("threadId"),
                "turn_id": (message.get("params") or {}).get("turnId"),
            })
            return message

    def dispatch(self, message: dict[str, Any]) -> None:
        # Server-to-client request: reject explicitly so the server never waits.
        if "id" in message and "method" in message and "result" not in message and "error" not in message:
            self.send({
                "id": message["id"],
                "error": {"code": -32601, "message": f"Unsupported server request: {message['method']}"},
            }, kind="unsupported_server_request_reply")
            return
        method = message.get("method")
        if not method:
            return
        self.notifications.append(message)
        params = message.get("params") or {}
        if params.get("threadId"):
            self.state["thread_id"] = params["threadId"]
        if params.get("turnId"):
            self.state["turn_id"] = params["turnId"]
        if method == "thread/tokenUsage/updated":
            self.state["usage_events"].append(params.get("tokenUsage"))
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                phase = item.get("phase")
                if phase == "final_answer":
                    self.state["final_messages"].append(item["text"])
                elif phase is None:
                    self.state["fallback_messages"].append(item["text"])
        elif method == "turn/completed":
            self.state["terminal"] = params.get("turn") or params
        elif method == "error" and not params.get("willRetry", False):
            self.state["terminal_error"] = params
        elif method == "thread/status/changed":
            status = params.get("status") or {}
            if status.get("type") == "systemError":
                self.state["terminal_error"] = params
        if "compact" in method.casefold() or "compaction" in dumps(params).casefold():
            self.state["context_altered"] = True

    def drain_until_terminal(self, deadline: float) -> None:
        while self.state["terminal"] is None and self.state["terminal_error"] is None:
            self.dispatch(self.next_message(deadline))

    def wait_late_usage(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.dispatch(self.next_message(deadline))
            except PipelineError as exc:
                if "timed out" in str(exc):
                    return
                if "EOF" in str(exc) or "exited early" in str(exc):
                    return
                raise


class CodexAppServerClient:
    provider = "codex"
    preflight_input_unit = "utf8_bytes"
    preflight_input_quality = "conservative_upper_bound"
    preflight_input_method = "UTF-8 byte length; no verified app-server input-token RPC"

    def __init__(self, profile: dict[str, Any], ui: Any):
        self.settings = profile
        self.ui = ui
        self.profile_name = profile["profile_name"]
        self.resolved_profile = profile["resolved_profile"]
        self.model = profile.get("model")
        self.context = int(profile["context_size"])
        self.timeout = float(profile.get("request_timeout", 1200))
        self.executable = profile.get("executable") or "codex"
        self.project_root = Path(profile["project_root"])
        self.identity = {"provider": "codex", "requested_model": self.model}

    def close(self) -> None:
        pass

    @property
    def tokenizer_identity(self) -> dict[str, Any]:
        return {"provider": "codex", "model": self.model, "method": "utf8_byte_upper_bound"}

    def discover(self) -> dict[str, Any]:
        executable = shutil.which(self.executable)
        if not executable:
            raise PipelineError(f"Codex executable not found: {self.executable}")
        try:
            run = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PipelineError(f"Cannot inspect Codex executable: {exc}") from exc
        self.identity = {
            "provider": "codex", "requested_model": self.model,
            "executable": executable, "cli_version": run.stdout.strip(),
        }
        discovery = self.project_root / "provider-discovery" / f"codex-{time.time_ns()}"
        recorder = AttemptRecorder(discovery, {"operation": "model/list", "provider": "codex", "model_turn": False})
        home, sqlite_home, work = self._runtime(discovery)
        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(app_server_argv(executable), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, cwd=work, env=self._process_env(home, sqlite_home),
                                    start_new_session=True)
            rpc = _RpcSession(proc, recorder)
            deadline = time.monotonic() + min(self.timeout, 30)
            rpc.request("initialize", {
                "clientInfo": {"name": "intelitex", "title": "Intelitex", "version": "1.11.0"},
                "capabilities": {"experimentalApi": True},
            }, deadline)
            rpc.send({"method": "initialized", "params": {}})
            result = rpc.request("model/list", {"includeHidden": False, "limit": 100}, deadline)
            models = result.get("data", [])
            match = next((item for item in models if item.get("id") == self.model or item.get("model") == self.model), None)
            if self.model and match is None:
                raise PipelineError(f"Requested Codex model {self.model!r} was not returned by model/list.")
            self.identity.update({"reported_model": (match or {}).get("id") or (match or {}).get("model"),
                                  "model_metadata": match, "models": models})
            recorder.finish(generation="not_run", validation="not_applicable", metadata=self.identity)
        except BaseException as exc:
            recorder.finish(generation="not_run", validation="failed", metadata=self.identity,
                            error={"type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            if proc is not None:
                self._cleanup_process(proc, graceful=True)
            if home.parent.is_dir():
                shutil.rmtree(home.parent, ignore_errors=True)
        return self.identity

    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))

    def body(self, prompt: str, inputs: dict, schema: dict, pass_no: int) -> dict[str, Any]:
        wire_format = self.settings.get("options", {}).get("p1_wire_format", "compact-v1") if pass_no == 1 else "canonical"
        if pass_no == 1 and wire_format not in {"compact-v1", "canonical"}:
            raise PipelineError(f"Unknown Codex Pass-1 wire format: {wire_format!r}")
        if pass_no == 1 and wire_format == "compact-v1":
            transport = build_transport(prompt, inputs)
            return {
                "model": self.model,
                "effort": self.settings.get("reasoning_effort"),
                "developer_instructions": transport.developer_instructions,
                "input": dumps(transport.input_payload),
                "output_schema": transport.output_schema,
                "pass_no": pass_no,
                "wire_format": transport.wire_format,
                # Decoder-only maps: retained in process memory and never
                # included in the app-server transport request.
                "codec_context": {
                    "block_ids": list(transport.block_ids),
                    "block_id_to_index": dict(transport.block_id_to_index),
                    "scene_ids": list(transport.scene_ids),
                },
            }
        return {
            "model": self.model,
            "effort": self.settings.get("reasoning_effort"),
            "developer_instructions": prompt,
            "input": dumps(inputs),
            "output_schema": schema,
            "pass_no": pass_no,
            "wire_format": "canonical",
        }

    def preflight(self, body: dict, recorder: AttemptRecorder | None = None) -> int:
        # The installed app-server exposes usage after a turn, not a no-turn
        # token counter. UTF-8 bytes are a conservative upper bound for packing.
        count = len(body["developer_instructions"].encode("utf-8")) + len(body["input"].encode("utf-8"))
        reserve = int(self.settings["planning_output_reserve"])
        margin = int(self.settings.get("options", {}).get("context_margin_tokens", 2048))
        required = count + reserve + margin
        if recorder:
            recorder.context({
                "method": "UTF-8 byte upper bound; app-server has no verified preflight token-count RPC",
                "quality": "conservative_estimate", "tokenizer_identity": None,
                "input_upper_bound": count, "capacity_tokens": self.context,
                "planning_output_reserve": reserve, "enforced_output_cap": None,
                "safety_margin": margin, "required_upper_bound": required,
                "silent_truncation": False,
            })
        if required > self.context:
            raise PipelineError(f"Codex conservative request bound {required:,} exceeds configured context {self.context:,}; source was not altered.")
        return count

    def _runtime(self, directory: Path) -> tuple[Path, Path, Path]:
        attempt_tag = directory.name + "-" + str(os.getpid()) + "-" + str(time.time_ns())
        configured = self.settings.get("runtime_root")
        state_home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        root = (Path(configured).expanduser() if configured else state_home / "intelitex" / "codex") / attempt_tag
        root = root.resolve()
        home, sqlite_home, work = root / "home", root / "sqlite", root / "work"
        for path in (root, home, sqlite_home, work):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        auth_source = self.settings.get("options", {}).get("auth_source")
        if auth_source:
            source = Path(auth_source).expanduser().resolve()
            if not source.is_file():
                raise PipelineError(f"Authorized Codex authentication source does not exist: {source}")
            target = home / "auth.json"
            temporary = home / ".auth.json.tmp"
            with source.open("rb") as src, temporary.open("wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        return home, sqlite_home, work

    @staticmethod
    def _process_env(home: Path, sqlite_home: Path) -> dict[str, str]:
        allowed = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update({"HOME": str(home), "CODEX_HOME": str(home), "CODEX_SQLITE_HOME": str(sqlite_home)})
        return env

    @staticmethod
    def _cleanup_process(proc: subprocess.Popen, graceful: bool) -> None:
        if graceful:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)

    @staticmethod
    def _copy_rollout(path_value: str | None, home: Path, destination: Path) -> Path:
        if not path_value:
            raise PipelineError("Persisted Codex thread did not return an opaque rollout path.")
        source = Path(path_value).resolve()
        if not source.is_relative_to(home.resolve()):
            raise PipelineError("Codex returned a rollout path outside the isolated application home.")
        if not source.exists():
            raise PipelineError("Returned Codex rollout path does not exist after graceful flush.")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = destination / "rollout"
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target.with_suffix(source.suffix or ".jsonl"))
            target = target.with_suffix(source.suffix or ".jsonl")
        return target

    def generate(self, body: dict, directory: Path, recorder: AttemptRecorder | None = None) -> tuple[str, dict]:
        if recorder is None:
            raise PipelineError("Codex generation requires an active evidence recorder.")
        executable = shutil.which(self.executable)
        if not executable:
            raise PipelineError(f"Codex executable not found: {self.executable}")
        home, sqlite_home, work = self._runtime(directory)
        argv = app_server_argv(executable)
        base = BASE_INSTRUCTIONS.read_text(encoding="utf-8").strip()
        if not base:
            raise PipelineError("Codex base instructions must be non-empty.")
        transport_plan = {
            "wire_format": body.get("wire_format", "canonical"),
            "argv": argv, "environment": {"CODEX_HOME": str(home), "CODEX_SQLITE_HOME": str(sqlite_home), "cwd": str(work)},
            "thread": {
                "model": self.model, "cwd": str(work), "sandbox": "read-only",
                "approvalPolicy": "never", "ephemeral": False,
                "baseInstructions": base, "developerInstructions": body["developer_instructions"],
                "personality": "none", "environments": [], "dynamicTools": [],
                "selectedCapabilityRoots": [], "runtimeWorkspaceRoots": [],
                "config": {"model_reasoning_effort": body.get("effort")},
            },
            "turn": {
                "input": [{"type": "text", "text": body["input"]}],
                "model": self.model, "effort": body.get("effort"), "cwd": str(work),
                "environments": [], "runtimeWorkspaceRoots": [], "outputSchema": body["output_schema"],
            },
        }
        recorder.transport_request(transport_plan, body["output_schema"])
        started = time.monotonic()
        proc: subprocess.Popen | None = None
        rpc: _RpcSession | None = None
        graceful = False
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=work, env=self._process_env(home, sqlite_home), start_new_session=True,
            )
            rpc = _RpcSession(proc, recorder)
            deadline = time.monotonic() + self.timeout
            rpc.request("initialize", {
                "clientInfo": {"name": "intelitex", "title": "Intelitex", "version": "1.11.0"},
                "capabilities": {"experimentalApi": True},
            }, deadline)
            rpc.send({"method": "initialized", "params": {}})
            listed = rpc.request("skills/list", {"cwds": [str(work)], "forceReload": True}, deadline)
            paths: set[str] = set()
            for group in listed.get("data", []):
                if group.get("errors"):
                    raise PipelineError(f"Codex skills isolation discovery failed: {group['errors']}")
                for skill in group.get("skills", []):
                    if skill.get("enabled") is True and isinstance(skill.get("path"), str):
                        paths.add(skill["path"])
            for path in sorted(paths):
                result = rpc.request("skills/config/write", {"path": path, "enabled": False}, deadline)
                if result.get("effectiveEnabled") is not False:
                    raise PipelineError(f"Codex skill remained enabled: {path}")
            verified = rpc.request("skills/list", {"cwds": [str(work)], "forceReload": True}, deadline)
            for group in verified.get("data", []):
                if group.get("errors"):
                    raise PipelineError(f"Codex skills re-list failed: {group['errors']}")
                enabled = [s.get("path") for s in group.get("skills", []) if s.get("enabled")]
                if enabled:
                    raise PipelineError(f"Codex skills remain enabled: {enabled}")
            thread_result = rpc.request("thread/start", transport_plan["thread"], deadline)
            thread = thread_result.get("thread") or {}
            rpc.state.update({
                "thread_id": thread.get("id"), "session_id": thread.get("sessionId"),
                "thread_path": thread.get("path"),
                "reported_model": thread_result.get("model") or thread.get("model"),
                "reported_effort": thread_result.get("reasoningEffort") or thread.get("reasoningEffort"),
                "model_provider": thread_result.get("modelProvider") or thread.get("modelProvider"),
                "cli_version": thread.get("cliVersion"),
            })
            if not rpc.state["thread_id"] or not rpc.state["thread_path"]:
                raise PipelineError("Codex persisted thread response omitted id or path.")
            turn_params = dict(transport_plan["turn"], threadId=rpc.state["thread_id"])
            turn_result = rpc.request("turn/start", turn_params, deadline)
            turn = turn_result.get("turn") or {}
            rpc.state["turn_id"] = turn.get("id") or rpc.state["turn_id"]
            rpc.drain_until_terminal(deadline)
            if rpc.state["terminal_error"] is not None:
                raise PipelineError(f"Codex terminal error: {rpc.state['terminal_error']}")
            terminal = rpc.state["terminal"] or {}
            if terminal.get("status") != "completed":
                raise PipelineError(f"Codex turn ended with status {terminal.get('status')!r}.")
            if rpc.state["context_altered"]:
                raise PipelineError("Codex reported compaction/context alteration; source-preserving invariant rejected this attempt.")
            # Usage may be cumulative and may arrive just after turn/completed.
            # A short bounded drain captures the final snapshot; snapshots are
            # retained individually and never added together.
            rpc.wait_late_usage(float(self.settings.get("options", {}).get("late_usage_wait", 0.75)))
            finals = rpc.state["final_messages"] or rpc.state["fallback_messages"]
            if not finals:
                raise PipelineError("Codex completed without a completed final-answer agent message.")
            answer = finals[-1].strip()
            if not answer:
                raise PipelineError("Codex final-answer item was empty.")
            # Close stdin first so Codex flushes the persisted rollout.
            self._cleanup_process(proc, graceful=True)
            graceful = True
            copied = self._copy_rollout(rpc.state["thread_path"], home, directory / "codex")
            usage_raw = rpc.state["usage_events"]
            last_event = usage_raw[-1] if usage_raw else {}
            last = last_event.get("last") or {}
            normalized = normalized_usage(
                input_tokens=last.get("inputTokens"), cached_input_tokens=last.get("cachedInputTokens"),
                cache_write_input_tokens=last.get("cacheWriteInputTokens"), output_tokens=last.get("outputTokens"),
                reasoning_output_tokens=last.get("reasoningOutputTokens"), total_tokens=last.get("totalTokens"),
                source="codex_thread_token_usage_last", scope="last_internal_operation",
                status="reported" if usage_raw else "unavailable",
            )
            normalized["thread_total"] = last_event.get("total")
            normalized["model_context_window"] = last_event.get("modelContextWindow")
            recorder.usage(usage_raw, normalized)
            recorder.write_answer(answer)
            if rpc.stderr:
                atomic_text(directory / "stderr.txt", str(redact(rpc.stderr.decode("utf-8", "replace"))))
                os.chmod(directory / "stderr.txt", 0o600)
            meta = {
                "provider": "codex", "requested_model": self.model,
                "reported_model": terminal.get("model") or turn.get("model") or rpc.state["reported_model"],
                "requested_effort": body.get("effort"),
                "reported_effort": terminal.get("effort") or turn.get("effort") or rpc.state["reported_effort"],
                "model_provider": rpc.state["model_provider"], "cli_version": rpc.state["cli_version"],
                "thread_id": rpc.state["thread_id"], "session_id": rpc.state["session_id"],
                "turn_id": rpc.state["turn_id"], "thread_path": rpc.state["thread_path"],
                "rollout_copy": str(copied.relative_to(directory)),
                "status": "completed", "finish_reason": "stop",
                "wire_format": body.get("wire_format", "canonical"),
                "usage_status": normalized["status"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "isolation": {
                    "ephemeral": False, "sandbox": "read-only", "approval_policy": "never",
                    "skills_disabled": len(paths), "skills_verified_disabled": True,
                    "dynamic_tools": [], "environments": [], "capability_roots": [], "runtime_workspace_roots": [],
                    "base_instructions": base, "developer_instructions": body["developer_instructions"],
                    "residual_limitation": "App-server may add platform-owned sandbox and minimal environment wrappers.",
                },
            }
            return answer, meta
        except BaseException:
            if rpc and rpc.state.get("thread_id") and rpc.state.get("turn_id") and proc and proc.poll() is None:
                try:
                    rpc.request("turn/interrupt", {
                        "threadId": rpc.state["thread_id"], "turnId": rpc.state["turn_id"],
                    }, time.monotonic() + 1.0)
                except Exception:
                    pass
            raise
        finally:
            if proc is not None and not graceful:
                self._cleanup_process(proc, graceful=False)
            if rpc and rpc.state.get("usage_events") and not (directory / "usage.json").exists():
                try:
                    usage_raw = rpc.state["usage_events"]
                    event = usage_raw[-1] or {}
                    last = event.get("last") or {}
                    usage = normalized_usage(
                        input_tokens=last.get("inputTokens"), cached_input_tokens=last.get("cachedInputTokens"),
                        cache_write_input_tokens=last.get("cacheWriteInputTokens"), output_tokens=last.get("outputTokens"),
                        reasoning_output_tokens=last.get("reasoningOutputTokens"), total_tokens=last.get("totalTokens"),
                        source="codex_thread_token_usage_last", scope="last_internal_operation",
                    )
                    usage["thread_total"] = event.get("total")
                    recorder.usage(usage_raw, usage)
                except (OSError, PipelineError):
                    pass
            if rpc and rpc.stderr and not (directory / "stderr.txt").exists():
                try:
                    atomic_text(directory / "stderr.txt", str(redact(rpc.stderr.decode("utf-8", "replace"))))
                    os.chmod(directory / "stderr.txt", 0o600)
                except OSError:
                    pass
            has_persisted_thread = bool(rpc and rpc.state.get("thread_path"))
            if has_persisted_thread and not (directory / "codex").exists():
                try:
                    self._copy_rollout(rpc.state["thread_path"], home, directory / "codex")
                except (OSError, PipelineError):
                    pass
            # The rollout has been copied into attempt evidence. Remove the
            # unique private runtime (including the authorized auth copy) only
            # after the owned process has been reaped.
            runtime_root = home.parent if "home" in locals() else None
            copy_is_durable = (directory / "codex").exists()
            if runtime_root and runtime_root.is_dir() and (not has_persisted_thread or copy_is_durable):
                try:
                    shutil.rmtree(runtime_root)
                except OSError:
                    pass
            elif runtime_root and runtime_root.is_dir():
                # Keep the rollout for repair, but never keep the temporary
                # authentication copy merely because evidence finalization failed.
                try:
                    (home / "auth.json").unlink(missing_ok=True)
                except OSError:
                    pass
