"""Owned process signalling, including detached provider children on Linux."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def descendants(pid: int) -> dict[int, str]:
    """Capture start times as well as PIDs to avoid signalling reused PIDs."""
    records = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            records[int(path.parent.name)] = (int(fields[1]), fields[19])
        except (OSError, ValueError, IndexError):
            continue
    owned = {pid}
    while True:
        expanded = owned | {child for child, (parent, _) in records.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    return {child: records[child][1] for child in owned - {pid} if child in records}


def signal_descendants(owned: dict[int, str], sig: int):
    for pid, started in owned.items():
        try:
            current = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
            if current == started:
                os.kill(pid, sig)
        except (OSError, IndexError):
            pass


def signal_worker(proc: subprocess.Popen, sig: int):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass


def enable_child_reaping():
    """Adopt orphaned provider grandchildren on Linux, without changing Codex."""
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Unable to enable owned child reaping")


def reap_descendants(owned: dict[int, str], timeout: float = 2):
    """Reap only captured children, never another worker or its process handles."""
    pending = set(owned)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for pid in list(pending):
            try:
                current = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
                if current != owned[pid]:
                    pending.remove(pid)
                    continue
                reaped, _ = os.waitpid(pid, os.WNOHANG)
                if reaped:
                    pending.remove(pid)
            except (ChildProcessError, FileNotFoundError, ProcessLookupError):
                pending.remove(pid)  # Already reaped by its actual parent.
        if pending:
            time.sleep(0.01)
