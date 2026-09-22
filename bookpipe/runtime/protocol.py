"""JSONL worker protocol. Only progress metadata crosses this boundary."""
from dataclasses import asdict
import json
import re
import threading
from typing import TextIO

from ..progress import ProgressEvent

MAX_FRAME = 64 * 1024

# Deliberately metadata-only. New event fields need an explicit privacy review
# before they become part of server-global history and HTTP responses.
PROGRESS_FIELDS = frozenset("""
analysis_unit_id answer_chars attempt_number cache_write_input_tokens cached_input_tokens
chapter_id chapter_number chapter_total chunk_id chunk_index chunk_total completed_units
cumulative error filename fixed_chunk_boundaries input_method input_quality input_tokens
input_unit input_value model_called output_tokens part parts pass_no profile
prose_translation_generated provider publication_fingerprint reasoning_chars
reasoning_output_tokens recalculate_request_tokens repair_count
replace_successful_outputs requested_model run_current run_total section_number
source status target_language task_key total_tokens unit_id unit_index
""".split())


# Older registries may still contain these fields. Scrub both replay and job
# snapshots as well as excluding them from newly emitted progress metadata.
PATH_FIELDS = frozenset({"project", "output_path", "recovery_path", "review_path"})


def public_envelope(envelope: dict) -> dict:
    event = envelope["event"]
    return {**envelope, "event": {**event, "values": {
        key: value for key, value in event.get("values", {}).items() if key not in PATH_FIELDS
    }}}


def progress_value(event: ProgressEvent) -> dict:
    value = asdict(event)
    value["values"] = {key: item for key, item in event.values.items()
                       if key in PROGRESS_FIELDS and (item is None or type(item) in {str, int, float, bool})}
    if "error" in value["values"]:
        value["values"]["error"] = "Operation failed; inspect project evidence locally."
    value["message"] = ""  # Free-form human text can contain provider responses.
    return value


def encode(value: dict) -> str:
    wire = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
    if len(wire) > MAX_FRAME:
        raise ValueError("Worker frame exceeds limit.")
    return wire


def decode(line: str) -> dict:
    if len(line) > MAX_FRAME or not line.endswith("\n"):
        raise ValueError("Invalid worker frame size.")
    value = json.loads(line)
    if not isinstance(value, dict) or value.get("type") not in {"progress", "result", "failure", "cancelled"}:
        raise ValueError("Invalid worker frame.")
    if value["type"] == "progress":
        event = ProgressEvent(**value["event"])
        if not isinstance(event.kind, str) or not isinstance(event.values, dict):
            raise ValueError("Invalid progress event.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", event.kind):
            raise ValueError("Invalid event kind.")
        if any(item is not None and type(item) is not int for item in (event.current, event.total)):
            raise ValueError("Invalid progress counters.")
        value["event"] = progress_value(event)
    elif value["type"] == "result":
        if value.get("status") != "succeeded":
            raise ValueError("Invalid terminal result.")
        value = {"type": "result", "status": "succeeded"}
    elif value["type"] == "failure":
        error = value.get("error")
        if not isinstance(error, dict):
            raise ValueError("Invalid worker error.")
        error_type = error.get("type", "WorkerError")
        if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", error_type):
            raise ValueError("Invalid error type.")
        value = {"type": "failure", "error": {"type": error_type,
                 "message": "Operation failed; inspect project configuration and attempt evidence locally."}}
    else:
        value = {"type": "cancelled"}
    return value


class JsonlProgressSink:
    def __init__(self, stream: TextIO):
        self.stream = stream
        self.lock = threading.Lock()

    def send(self, value: dict):
        with self.lock:
            self.stream.write(encode(value))
            self.stream.flush()

    def emit(self, event: ProgressEvent):
        self.send({"type": "progress", "event": progress_value(event)})
