"""One server owns N independent synchronous worker processes."""
from dataclasses import dataclass, replace, asdict
from pathlib import Path
import signal
import subprocess
import sys
import threading
import uuid

from .events import EventBroker
from .models import ACTIVE, Job, JobSpec, ImportJobSpec, parse_spec, now
from .processes import descendants, enable_child_reaping, reap_descendants, signal_descendants, signal_worker
from .protocol import MAX_FRAME, decode, encode
from .registry import JobRegistry


class JobConflict(Exception):
    pass


@dataclass
class OwnedWorker:
    process: subprocess.Popen
    monitor: threading.Thread | None = None
    stopper: threading.Thread | None = None


def worker_command(spec: JobSpec | ImportJobSpec) -> list[str]:
    return [sys.executable, '-m', 'bookpipe.runtime.worker']


class JobSupervisor:
    def __init__(self, registry: JobRegistry, workspace_root: Path, *, command_factory=worker_command,
                 interrupt_grace: float = 15, terminate_grace: float = 5):
        enable_child_reaping()
        self.registry = registry
        self.workspace_root = str(workspace_root.resolve())
        self.broker = EventBroker(registry)
        self.command_factory = command_factory
        self.interrupt_grace, self.terminate_grace = interrupt_grace, terminate_grace
        self.lock = threading.RLock()
        self.owned: dict[str, OwnedWorker] = {}
        self.closing = False

    def get(self, job_id: str) -> Job:
        job = self.registry.get(job_id)
        if job.workspace_root != self.workspace_root:
            raise KeyError(job_id)
        return job

    def list(self) -> list[Job]:
        return [j for j in self.registry.list() if j.workspace_root == self.workspace_root]

    def active_for_project(self, project: Path) -> Job | None:
        with self.lock:
            return next((j for j in self.list() if j.project == str(project.resolve()) and j.state in ACTIVE), None)

    def snapshot(self, *, workspace_id: str | None = None, job_id: str | None = None) -> dict:
        with self.lock:
            jobs = [j.public() for j in self.list()
                    if (workspace_id is None or j.workspace_id == workspace_id)
                    and (job_id is None or j.job_id == job_id)]
            return {"jobs": jobs, "cursor": self.registry.cursor()}

    def _state(self, job_id: str, **changes) -> Job:
        self.registry.save(replace(self.get(job_id), **changes))
        self.broker.publish(job_id, {"kind": "job_state", "values": {"state": changes["state"]}})
        return self.get(job_id)

    def start(self, spec: JobSpec | ImportJobSpec) -> Job:
        # Revalidate at launch too, including symlink containment.
        spec = parse_spec(asdict(spec))
        if spec.workspace_root != self.workspace_root:
            raise ValueError("Wrong workspace root.")
        with self.lock:
            if self.closing:
                raise JobConflict("Server is shutting down.")
            if self.active_for_project(Path(spec.project)):
                raise JobConflict("Workspace already has an active mutating job.")
            job = Job(uuid.uuid4().hex, spec.workspace_root, spec.workspace_id,
                      spec.project, spec.operation, started_at=now())
            self.registry.save(job)
            self.broker.publish(job.job_id, {"kind": "job_state", "values": {"state": "starting"}})
            try:
                proc = subprocess.Popen(self.command_factory(spec), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                        start_new_session=True, cwd=Path(__file__).resolve().parents[2])
            except OSError:
                return self._state(job.job_id, state="failed", finished_at=now(), error={
                    "type": "WorkerLaunchError", "message": "Unable to start worker."})
            owned = OwnedWorker(proc)
            self.owned[job.job_id] = owned
            self._state(job.job_id, state="running", pid=proc.pid)
            owned.monitor = threading.Thread(target=self._monitor, args=(job.job_id, owned), daemon=True)
            owned.monitor.start()
            try:
                proc.stdin.write(encode(asdict(spec)))
                proc.stdin.close()
            except (OSError, ValueError):
                signal_worker(proc, signal.SIGTERM)
            return self.get(job.job_id)

    def _monitor(self, job_id: str, owned: OwnedWorker):
        terminal = None
        protocol_error = False
        try:
            while line := owned.process.stdout.readline(MAX_FRAME + 1):
                frame = decode(line)
                if terminal is not None:
                    raise ValueError("Frame after terminal result")
                if frame["type"] == "progress":
                    with self.lock:
                        self.broker.publish(job_id, frame["event"])
                else:
                    terminal = frame
        except (ValueError, TypeError, KeyError, UnicodeError):
            protocol_error = True
            self.stop(job_id)
        finally:
            owned.process.stdout.close()
        code = owned.process.wait()
        with self.lock:
            job = self.get(job_id)
            if job.state == "stopping" and not protocol_error:
                state, error = "cancelled", None
            elif not protocol_error and terminal and terminal["type"] == "result" and code == 0:
                state, error = "succeeded", None
            elif not protocol_error and terminal and terminal["type"] == "cancelled":
                state, error = "cancelled", None
            else:
                state = "failed"
                error = (terminal or {}).get("error") or {
                    "type": "WorkerProtocolError" if protocol_error else "WorkerExit",
                    "message": "Worker failed or exited without a successful terminal result."}
            self._state(job_id, state=state, exit_code=code, finished_at=now(), error=error)
            stopper = owned.stopper
        # Terminal state prevents a later stop() from creating another stopper.
        # Keep ownership until detached-child cleanup finishes, and join outside
        # the lifecycle mutex so concurrent shutdown/start/get remain safe.
        if stopper is not None:
            stopper.join()
        with self.lock:
            self.owned.pop(job_id)

    def stop(self, job_id: str) -> Job:
        with self.lock:
            job = self.get(job_id)
            owned = self.owned.get(job_id)
            if job.state not in ACTIVE or owned is None or owned.stopper is not None:
                return job
            job = self._state(job_id, state="stopping")
            owned.stopper = threading.Thread(target=self._stop, args=(owned,), daemon=True)
            owned.stopper.start()
            return job

    def _stop(self, owned: OwnedWorker):
        proc = owned.process
        children = descendants(proc.pid)
        signal_worker(proc, signal.SIGINT)
        try:
            proc.wait(timeout=self.interrupt_grace)
            return  # Normal unwind owns provider cleanup and checkpoint commits.
        except subprocess.TimeoutExpired:
            pass
        children.update(descendants(proc.pid))
        signal_worker(proc, signal.SIGTERM)
        signal_descendants(children, signal.SIGTERM)
        try:
            proc.wait(timeout=self.terminate_grace)
        except subprocess.TimeoutExpired:
            signal_worker(proc, signal.SIGKILL)
            proc.wait()
        finally:
            signal_descendants(children, signal.SIGKILL)
            reap_descendants(children)

    def shutdown(self):
        with self.lock:
            self.closing = True
            workers = list(self.owned.items())
            for job_id, _ in workers:
                self.stop(job_id)
        # All workers receive interrupts concurrently; grace periods do not add up.
        for _, owned in workers:
            if owned.stopper:
                owned.stopper.join()
            if owned.monitor:
                owned.monitor.join()
        self.broker.close()
