"""Internal one-operation entrypoint; stdout is exclusively structured JSONL."""
import contextlib
import json
import signal
import sys
from pathlib import Path

from ..application.commands import AnalyzeCommand, TranslateCommand, PublishCommand
from ..bootstrap import create_application
from .models import JobSpec
from .protocol import JsonlProgressSink, MAX_FRAME


def execute(spec: JobSpec, sink: JsonlProgressSink, application_factory=create_application) -> int:
    try:
        app = application_factory(sink)
        project = Path(spec.project)
        if spec.operation == "analyze":
            app.pipeline.analyze(AnalyzeCommand(project, profile=spec.profile))
        elif spec.operation == "translate":
            app.pipeline.translate(TranslateCommand(project, profile=spec.profile, chunk_limit=spec.chunk_limit))
        elif spec.operation == "publish":
            app.publishing.publish(PublishCommand(project, spec.target_language))
        sink.send({"type": "result", "status": "succeeded"})
        return 0
    except KeyboardInterrupt:
        sink.send({"type": "cancelled"})
        return 130
    except Exception as exc:
        sink.send({"type": "failure", "error": {
            "type": type(exc).__name__,
            "message": "Operation failed; inspect project configuration and attempt evidence locally.",
        }})
        return 1


def main() -> int:
    # Ignore repeated interrupts during stack unwinding/provider cleanup. SIGTERM
    # remains the supervisor's bounded hard fallback.
    def interrupt(signum, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    sink = JsonlProgressSink(sys.stdout)
    try:
        spec = JobSpec(**json.loads(sys.stdin.readline(MAX_FRAME + 1)))
        with contextlib.redirect_stdout(sys.stderr):
            return execute(spec, sink)
    except KeyboardInterrupt:
        sink.send({"type": "cancelled"})
        return 130
    except Exception as exc:
        sink.send({"type": "failure", "error": {"type": type(exc).__name__, "message": "Invalid worker specification."}})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
