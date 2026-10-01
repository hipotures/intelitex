from __future__ import annotations

import copy
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
from .codex_cache import diagnostics, load_developer_contract, output_schema, serialize
from . import codex_cache_v2
from . import codex_cache_shared
from . import codex_cache_shared_v2
from . import codex_parent, codex_pair
from .codex_effort import EffortPlan, resolve_effort_plan, runtime_model_metadata, rollout_effort_evidence, verify_reported_selection
from .evidence import AttemptRecorder, redact
from .p1_compact import build_transport
from .progress import ProgressEvent
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
    def __init__(self, proc: subprocess.Popen, recorder: AttemptRecorder, progress: Any | None = None):
        self.proc = proc
        self.recorder = recorder
        self.progress = progress
        self.lines: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
        self.next_id = 0
        self.stderr = bytearray()
        self.notifications: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {
            "thread_id": None, "session_id": None, "turn_id": None, "thread_path": None,
            "reported_model": None, "reported_effort": None, "model_provider": None, "cli_version": None,
            "terminal": None, "terminal_error": None, "final_messages": [],
            "fallback_messages": [], "usage_events": [], "context_altered": False,
            "last_emitted_usage": None,
            "turn_submitted": False, "turn_settings": None,
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
            usage = params.get("tokenUsage")
            self.state["usage_events"].append(usage)
            last = (usage or {}).get("last") or {}
            signature = tuple(last.get(key) for key in (
                "inputTokens", "cachedInputTokens", "cacheWriteInputTokens",
                "outputTokens", "reasoningOutputTokens", "totalTokens",
            ))
            progress = getattr(self, "progress", None)
            if progress is not None and signature != self.state.get("last_emitted_usage"):
                self.state["last_emitted_usage"] = signature
                progress.emit(ProgressEvent(kind="provider_usage_update", values={
                    **dict(self.recorder.progress_values),
                    "input_tokens": last.get("inputTokens"),
                    "cached_input_tokens": last.get("cachedInputTokens"),
                    "cache_write_input_tokens": last.get("cacheWriteInputTokens"),
                    "output_tokens": last.get("outputTokens"),
                    "reasoning_output_tokens": last.get("reasoningOutputTokens"),
                    "total_tokens": last.get("totalTokens"),
                    "source": "codex_thread_token_usage_last",
                    "status": "reported", "cumulative": True,
                }))
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
        elif method == "thread/settings/updated" and self.state.get("turn_submitted"):
            # Standard RPC observation of settings applied to the submitted
            # turn. Useful when an ephemeral child has no turn_context rollout.
            self.state["turn_settings"] = params.get("threadSettings")
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

    def _installed_effort_feature(self, executable: str, home: Path, sqlite_home: Path) -> dict[str, Any]:
        """Inspect this executable in the private home; never enable a global flag."""
        result: dict[str, Any] = {"available": False, "cli_version": None}
        try:
            kwargs = dict(stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
                          env=self._process_env(home, sqlite_home), cwd=home)
            version = subprocess.run([executable, "--version"], **kwargs)
            if version.returncode == 0:
                result["cli_version"] = version.stdout.strip()
            features = subprocess.run([executable, "features", "list"], **kwargs)
            if features.returncode == 0:
                result["available"] = any(
                    parts and parts[0] == "reasoning_effort_override" and "removed" not in parts
                    for parts in (line.split() for line in features.stdout.splitlines())
                )
        except (OSError, subprocess.SubprocessError):
            pass  # An unavailable optimization never changes the selected effort.
        return result

    def _effort_plan(self, rpc: _RpcSession, home: Path, installed: dict, requested: str | None,
                     deadline: float) -> tuple[EffortPlan, dict]:
        metadata = None
        evidence: dict[str, Any] = {"source": "installed Codex", "trusted": False, "feature": installed}
        if installed["available"]:
            cursor = None
            seen = set()
            while True:
                params = {"includeHidden": True, "limit": 100, **({"cursor": cursor} if cursor else {})}
                result = rpc.request("model/list", params, deadline)
                match = next((m for m in result.get("data", [])
                              if self.model in (m.get("id"), m.get("model"))), None)
                if match is not None or not result.get("nextCursor"):
                    metadata, evidence = runtime_model_metadata(home, self.model, installed["cli_version"], match)
                    evidence["feature"] = installed
                    break
                cursor = result["nextCursor"]
                if cursor in seen:
                    raise PipelineError("Codex model/list repeated a pagination cursor.")
                seen.add(cursor)
        return resolve_effort_plan(requested, metadata, feature_available=installed["available"]), evidence

    def body(self, prompt: str, inputs: dict, schema: dict, pass_no: int) -> dict[str, Any]:
        options = self.settings.get("options", {})
        wire_format = options.get("p1_wire_format", "compact-v1") if pass_no == 1 else options.get("translation_wire_format", "cache-v2")
        allowed = ("compact-v1", "canonical", "cache-v1", "cache-shared-v1", "cache-shared-v2") if pass_no == 1 else ("canonical", "cache-v1", "cache-v2", "cache-shared-v1", "cache-shared-v2")
        if wire_format not in allowed:
            label = "Pass-1" if pass_no == 1 else f"P{pass_no}"
            raise PipelineError(f"Unknown Codex {label} wire format: {wire_format!r}")
        if pass_no > 1:
            codex_parent.strategy(options)  # Validate even when using a rollback codec.
        if wire_format in ("cache-v2", "cache-shared-v1", "cache-shared-v2"):
            codec = {"cache-v2": codex_cache_v2, "cache-shared-v1": codex_cache_shared,
                     "cache-shared-v2": codex_cache_shared_v2}[wire_format]
            developer = codec.load_developer_contract(self.project_root, prompt, pass_no)
            layout, context = codec.encode_input(inputs, pass_no)
            return {
                "model": self.model, "effort": self.settings.get("reasoning_effort"),
                "developer_instructions": developer,
                "input": layout.active_pass_message if wire_format == "cache-shared-v2" else layout.text,
                **({"injected_items": [codex_cache_shared_v2.user_message(x) for x in layout.injected_messages]}
                   if wire_format == "cache-shared-v2" else {}),
                "output_schema": copy.deepcopy(codec.TRANSPORT_SCHEMA),
                "pass_no": pass_no, "wire_format": wire_format, "codec_context": context.as_dict(),
                "cache_diagnostics": codec.diagnostics(
                    layout, developer, self.model, self.settings.get("reasoning_effort"), pass_no, inputs,
                    BASE_INSTRUCTIONS.read_text(encoding="utf-8").strip(),
                ),
            }
        if wire_format == "cache-v1":
            developer = load_developer_contract(self.project_root, prompt, pass_no)
            layout = serialize(inputs, schema, pass_no)
            return {
                "model": self.model, "effort": self.settings.get("reasoning_effort"),
                "developer_instructions": developer, "input": layout.text,
                "output_schema": output_schema(), "pass_no": pass_no, "wire_format": wire_format,
                "cache_diagnostics": diagnostics(
                    layout, developer, self.model, self.settings.get("reasoning_effort"),
                    pass_no, inputs, BASE_INSTRUCTIONS.read_text(encoding="utf-8").strip(),
                ),
            }
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
        count += sum(len(content["text"].encode("utf-8")) for item in body.get("injected_items", [])
                     for content in item["content"])
        reserve = int(self.settings["planning_output_reserve"])
        margin = int(self.settings.get("options", {}).get("context_margin_tokens", 2048))
        parent = body.get("pair_parent")
        history_bytes = parent.get("additional_history_utf8_bytes", 0) if parent else 0
        if history_bytes and count + history_bytes + reserve + margin > self.context:
            # A pair's extra history must not make an otherwise valid complete
            # canonical request unusable. Opt out before any inference.
            body.pop("pair_parent")
            body.update(pair_parent_status="incompatible", execution_strategy="fresh_root_fallback",
                        pair_parent_reason="Additional conversation history exceeds configured context bound")
            history_bytes = 0
        count += history_bytes
        required = count + reserve + margin
        if recorder:
            if body.get("wire_format") in ("cache-v2", "cache-shared-v1", "cache-shared-v2"):
                recorder.codec_context(body["codec_context"])
            if body.get("cache_diagnostics"):
                recorder.cache_layout(body["cache_diagnostics"])
            recorder.context({
                "method": "UTF-8 byte upper bound; app-server has no verified preflight token-count RPC",
                "quality": "conservative_estimate", "tokenizer_identity": None,
                "input_upper_bound": count, "capacity_tokens": self.context,
                "additional_pair_history_utf8_bytes": history_bytes,
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
        installed = self._installed_effort_feature(executable, home, sqlite_home)
        effort_plan = resolve_effort_plan(body.get("effort"), None, feature_available=installed["available"])
        argv = app_server_argv(executable)
        base = BASE_INSTRUCTIONS.read_text(encoding="utf-8").strip()
        if not base:
            raise PipelineError("Codex base instructions must be non-empty.")
        strategy = codex_parent.strategy(self.settings.get("options", {})) if body.get("pass_no", 1) > 1 else "fresh-root"
        use_parent = (strategy == codex_parent.STRATEGY and body.get("wire_format") == "cache-shared-v2"
                      and body.get("pass_no") in (3, 4, 5))
        paired = strategy == codex_pair.STRATEGY and body.get("wire_format") == "cache-shared-v2"
        resume_parent = paired and body.get("pass_no") in codex_pair.PARENTS
        execution = {"translation_thread_strategy": strategy,
                     "execution_strategy": "p2_parent" if strategy == codex_parent.STRATEGY and
                     body.get("pass_no") == 2 and body.get("wire_format") == "cache-shared-v2" else "fresh-root"}
        parent = body.get("cache_parent") if use_parent else None
        if paired:
            execution.update(pair="P2-P3" if body["pass_no"] in (2, 3) else "P4-P5",
                             pair_role="pair_continuation" if resume_parent else "pair_parent",
                             execution_strategy="fresh_root_fallback" if resume_parent else "pair_parent")
        if resume_parent:
            execution.update({k: body.get(k) for k in ("pair_parent_status", "pair_parent_reason")})
            parent = body.get("pair_parent")
            if parent:
                try:
                    pair_history = codex_pair.inspect_snapshot(Path(parent["parent_rollout"]), parent, body)
                except (OSError, ValueError, KeyError, TypeError, PipelineError) as exc:
                    parent = None
                    execution.update(pair_parent_status="incompatible", pair_parent_reason=str(exc))
                else:
                    execution.update(execution_strategy="paired_resume", pair_parent_status="available",
                                     pair_parent=parent, parent_pass=parent["parent_pass"],
                                     parent_attempt=parent["parent_attempt"])
            elif not execution.get("pair_parent_status"):
                execution.update(pair_parent_status="unavailable", pair_parent_reason="No accepted pair parent supplied")
        if use_parent:
            execution.update({k: body.get(k) for k in ("cache_parent_status", "cache_parent_reason")})
            execution["execution_strategy"] = "fresh_root_fallback"
            if parent:
                try:
                    proof = codex_parent.inspect_parent(Path(parent["parent_rollout"]), parent["parent_thread_id"],
                                                        parent["parent_session_id"], parent["parent_turn_id"], body)
                    if (proof["parent_rollout_sha256"] != parent["parent_rollout_sha256"] or
                            any(parent[k] != body["cache_diagnostics"][k] for k in codex_parent.COMPATIBILITY_FIELDS)):
                        raise PipelineError("Accepted P2 parent changed before fork submission.")
                except (OSError, ValueError, KeyError, TypeError, PipelineError) as exc:
                    parent = None
                    execution.update(cache_parent_status="incompatible", cache_parent_reason=str(exc))
                else:
                    execution.update(execution_strategy="ephemeral_fork", cache_parent_status="available", cache_parent=parent)
            elif not execution.get("cache_parent_status"):
                execution.update(cache_parent_status="unavailable", cache_parent_reason="No accepted parent supplied")
        parent_native_path = home / "sessions" / Path(parent["native_rollout_name"]).name if parent else None
        if parent:
            execution["parent_runtime_rollout"] = str(parent_native_path)
        transport_plan = {
            "wire_format": body.get("wire_format", "canonical"),
            "requested_model": self.model, "reported_model": None, "reported_effort": None,
            **effort_plan.metadata(),
            **execution,
            **({"cache_diagnostics": body["cache_diagnostics"]} if body.get("cache_diagnostics") else {}),
            "argv": argv, "environment": {"CODEX_HOME": str(home), "CODEX_SQLITE_HOME": str(sqlite_home), "cwd": str(work)},
            "thread": {
                "model": self.model, "cwd": str(work), "sandbox": "read-only",
                "approvalPolicy": "never", "ephemeral": False,
                "baseInstructions": base, "developerInstructions": body["developer_instructions"],
                "personality": "none", "environments": [], "dynamicTools": [],
                "selectedCapabilityRoots": [], "runtimeWorkspaceRoots": [],
                "config": effort_plan.thread_config(),
            },
            "turn": {
                "input": [{"type": "text", "text": body["input"]}],
                "model": self.model, "effort": body.get("effort"), "cwd": str(work),
                "environments": [], "runtimeWorkspaceRoots": [], "outputSchema": body["output_schema"],
            },
        }
        if body.get("wire_format") == "cache-shared-v2":
            injected = ([] if resume_parent else body["injected_items"][2:]) if parent else body["injected_items"]
            transport_plan["injections"] = [{"method": "thread/inject_items", "params": {"items": [copy.deepcopy(item)]}}
                                            for item in injected]
            # Application-owned messages only. Codex can additionally add its
            # platform-owned sandbox/environment wrappers. This is evidence,
            # not a raw Responses request or extra app-server parameters.
            transport_plan["message_plan"] = [
                {"origin": "thread/start.baseInstructions", "text": base},
                {"origin": "thread/start.developerInstructions", "text": body["developer_instructions"]},
                *copy.deepcopy(body["injected_items"]), codex_cache_shared_v2.user_message(body["input"]),
            ]
            if parent and resume_parent:
                transport_plan["message_plan"][-1:-1] = [
                    codex_cache_shared_v2.user_message(pair_history["parent_active_input"]),
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": pair_history["parent_raw_answer"]}]},
                ]
        recorder.transport_request(transport_plan, body["output_schema"])
        started = time.monotonic()
        proc: subprocess.Popen | None = None
        rpc: _RpcSession | None = None
        graceful = False
        try:
            if parent:
                # 0.159.3 resolves paginated sources by ID after reading the
                # supplied path. Stage the unchanged native export in the
                # private session tree for Codex's own resolver/indexer.
                # No state DB copy, fabricated history, or retained runtime.
                parent_native_path.parent.mkdir(parents=True, mode=0o700)
                shutil.copyfile(parent["parent_rollout"], parent_native_path)
                os.chmod(parent_native_path, 0o600)
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=work, env=self._process_env(home, sqlite_home), start_new_session=True,
            )
            rpc = _RpcSession(proc, recorder, self.ui)
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
            effort_plan, effort_capability = self._effort_plan(rpc, home, installed, body.get("effort"), deadline)
            transport_plan.update(effort_plan.metadata(), reasoning_effort_capability=effort_capability)
            transport_plan["thread"]["config"] = effort_plan.thread_config()
            if parent:
                # Resume/fork params differ from ThreadStartParams: unsupported
                # start-only capability fields never enter this RPC. The private
                # runtime still suppresses all capabilities; turns override roots.
                lifecycle = "resume" if resume_parent else "fork"
                transport_plan[lifecycle] = {k: transport_plan["thread"][k] for k in (
                    "model", "cwd", "sandbox", "approvalPolicy", "baseInstructions",
                    "developerInstructions", "runtimeWorkspaceRoots", "config",
                )}
                transport_plan[lifecycle].update(
                    threadId=parent["parent_thread_id"], path=str(parent_native_path),
                    excludeTurns=True,
                )
                if not resume_parent:
                    transport_plan[lifecycle].update(beforeTurnId=parent["parent_turn_id"], ephemeral=True)
                transport_plan[lifecycle]["config"] = {**effort_plan.thread_config(), "personality": "none"}
                transport_plan.pop("thread")
            recorder.transport_request(transport_plan, body["output_schema"])
            try:
                lifecycle = ("resume" if resume_parent else "fork") if parent else "thread"
                method = "thread/" + ("start" if lifecycle == "thread" else lifecycle)
                thread_result = rpc.request(method, transport_plan[lifecycle], deadline)
            except PipelineError as exc:
                if not parent or not str(exc).startswith(f"Codex RPC {method} failed:"):
                    raise
                # A native resume/fork rejected before any turn is an optimization
                # miss. Reconstruct the complete independent request in this
                # same isolated process; never retry an already submitted turn.
                status_key = "pair_parent" if resume_parent else "cache_parent"
                execution.update(execution_strategy="fresh_root_fallback",
                                 **{status_key + "_status": "unavailable", status_key + "_reason": str(exc)})
                execution.pop(status_key, None)
                parent = None
                transport_plan["rejected_" + lifecycle] = transport_plan.pop(lifecycle)
                transport_plan.update(execution)
                transport_plan.pop(status_key, None)
                transport_plan["thread"] = {
                    "model": self.model, "cwd": str(work), "sandbox": "read-only", "approvalPolicy": "never",
                    "ephemeral": False, "baseInstructions": base,
                    "developerInstructions": body["developer_instructions"], "personality": "none",
                    "environments": [], "dynamicTools": [], "selectedCapabilityRoots": [],
                    "runtimeWorkspaceRoots": [], "config": effort_plan.thread_config(),
                }
                transport_plan["injections"] = [{"method": "thread/inject_items", "params": {"items": [copy.deepcopy(item)]}}
                                                for item in body["injected_items"]]
                transport_plan["message_plan"] = transport_plan["message_plan"][:2] + [
                    *copy.deepcopy(body["injected_items"]), codex_cache_shared_v2.user_message(body["input"])]
                recorder.transport_request(transport_plan, body["output_schema"])
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
            if not rpc.state["thread_id"] or ((not parent or resume_parent) and not rpc.state["thread_path"]):
                raise PipelineError("Codex persisted thread response omitted id or path.")
            if parent and not resume_parent and (thread.get("ephemeral") is not True or thread.get("forkedFromId") != parent["parent_thread_id"]
                           or rpc.state["thread_id"] == parent["parent_thread_id"] or not rpc.state["session_id"]
                           or rpc.state["session_id"] == parent["parent_session_id"]):
                raise PipelineError("Codex did not confirm an independent ephemeral child of the accepted P2; no turn submitted.")
            if parent and resume_parent:
                if (rpc.state["thread_id"] != parent["parent_thread_id"] or
                        rpc.state["session_id"] != parent["parent_session_id"] or thread.get("ephemeral") is True):
                    raise PipelineError("Codex resume did not retain the accepted pair identity; no turn submitted.")
                execution.update(resumed_thread_id=rpc.state["thread_id"], resumed_session_id=rpc.state["session_id"])
                transport_plan.update(execution)
            elif parent:
                execution.update(forked_from_id=thread["forkedFromId"], ephemeral=True,
                                 before_turn_id=parent["parent_turn_id"])
                transport_plan.update(execution)
            transport_plan["reported_model"] = rpc.state["reported_model"]
            if effort_plan.mode == "configuration_update" and rpc.state["reported_effort"] != effort_plan.baseline:
                raise PipelineError("Codex thread creation did not confirm the model-default effort baseline; no turn submitted.")
            turn_params = dict(transport_plan["turn"], threadId=rpc.state["thread_id"])
            # Replace the pre-submission plan with the exact physical turn,
            # including the provider-assigned ID. Diagnostics never enter RPC.
            transport_plan["turn"] = turn_params
            for injection in transport_plan.get("injections", []):
                injection["params"]["threadId"] = rpc.state["thread_id"]
            recorder.transport_request(transport_plan, body["output_schema"])
            recorder.artifact_json("codex_execution.json", {
                **execution, **effort_plan.metadata(), "requested_model": self.model,
                "reported_model": rpc.state["reported_model"],
                "thread_id": rpc.state["thread_id"], "session_id": rpc.state["session_id"],
                "thread_path": rpc.state["thread_path"], "turn_id": None,
            })
            for injection in transport_plan.get("injections", []):
                # Each raw user ResponseItem is appended without a model turn.
                # Failure stops this attempt; never emulate with extra turns.
                rpc.request(injection["method"], injection["params"], deadline)
            if parent and resume_parent:
                # Resume may emit the parent's latest usage snapshot. Preserve
                # it as evidence, but never bill it as this continuation.
                recorder.artifact_json("restored_thread_usage.json", rpc.state["usage_events"])
                rpc.state["usage_events"] = []
                rpc.state["last_emitted_usage"] = None
                rpc.state["turn_id"] = None
            rpc.state["turn_submitted"] = True
            turn_result = rpc.request("turn/start", turn_params, deadline)
            turn = turn_result.get("turn") or {}
            rpc.state["turn_id"] = turn.get("id") or rpc.state["turn_id"]
            recorder.artifact_json("codex_execution.json", {
                **execution, **effort_plan.metadata(), "requested_model": self.model,
                "reported_model": rpc.state["reported_model"],
                "thread_id": rpc.state["thread_id"], "session_id": rpc.state["session_id"],
                "thread_path": rpc.state["thread_path"], "turn_id": rpc.state["turn_id"],
            })
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
            copied = None if parent and not resume_parent else self._copy_rollout(rpc.state["thread_path"], home, directory / "codex")
            effort_observed = (rollout_effort_evidence(copied, rpc.state["turn_id"]) if copied else {
                "configuration_update_observed": None, "configuration_update_effort": None,
                "provider_request_effort": None, "rollout_model": None, "rollout_effort": None,
            })
            turn_settings = rpc.state.get("turn_settings") or {}
            reported_model = (effort_observed["rollout_model"] or terminal.get("model") or turn.get("model")
                              or turn_settings.get("model") or rpc.state["reported_model"])
            reported_effort = effort_observed["rollout_effort"] or terminal.get("effort") or turn.get("effort") or turn_settings.get("effort")
            reported_effort_source = ("rollout.turn_context" if effort_observed["rollout_effort"] else
                                      "turn" if terminal.get("effort") or turn.get("effort") else "thread.settings.updated")
            if reported_effort is None and effort_plan.mode == "request_level":
                reported_effort = rpc.state["reported_effort"]
                reported_effort_source = ("thread.resume" if parent and resume_parent else
                                         "thread.fork" if parent else "thread.start") if reported_effort is not None else None
            elif reported_effort is None:
                reported_effort_source = None
            # thread/start reports the baseline, not the override's effective effort.
            # Keep unavailable evidence null instead of substituting our intent.
            recorder.event("local", "reasoning_effort_observed", {
                **effort_plan.metadata(), **effort_observed,
                "reported_model": reported_model, "reported_effort": reported_effort,
                "reported_effort_source": reported_effort_source,
            })
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
            verify_reported_selection(self.model, body.get("effort"), reported_model, reported_effort)
            if effort_observed["configuration_update_observed"] and effort_plan.mode == "configuration_update":
                expected_update = body.get("effort") or effort_plan.baseline
                # Codex's persistent alias is named "disabled" by the backend.
                if expected_update == "persistent":
                    expected_update = "disabled"
                if effort_observed["configuration_update_effort"] != expected_update:
                    raise PipelineError("Codex recorded a configuration_update for a different effective effort; result rejected.")
            if rpc.stderr:
                atomic_text(directory / "stderr.txt", str(redact(rpc.stderr.decode("utf-8", "replace"))))
                os.chmod(directory / "stderr.txt", 0o600)
            meta = {
                "provider": "codex", "requested_model": self.model,
                "reported_model": reported_model, "reported_effort": reported_effort,
                "reported_effort_source": reported_effort_source,
                "model_provider": rpc.state["model_provider"], "cli_version": rpc.state["cli_version"] or installed["cli_version"],
                "thread_id": rpc.state["thread_id"], "session_id": rpc.state["session_id"],
                "turn_id": rpc.state["turn_id"], "thread_path": rpc.state["thread_path"],
                "rollout_copy": str(copied.relative_to(directory)) if copied else None,
                "status": "completed", "finish_reason": "stop",
                "wire_format": body.get("wire_format", "canonical"),
                **body.get("cache_diagnostics", {}),
                **effort_plan.metadata(), **effort_observed,
                **execution,
                "reasoning_effort_capability": effort_capability,
                "usage_status": normalized["status"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "isolation": {
                    "ephemeral": bool(parent) and not resume_parent, "sandbox": "read-only", "approval_policy": "never",
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
