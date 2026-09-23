"""Internal one-operation entrypoint; stdout is exclusively structured JSONL."""
import contextlib
import json
import signal
import sys
from pathlib import Path

from ..application.commands import AnalyzeCommand, TranslateCommand, PublishCommand, ImportBookCommand
from ..bootstrap import create_application
from .models import JobSpec, ImportJobSpec, parse_spec
from .protocol import JsonlProgressSink, MAX_FRAME


def execute(spec: JobSpec | ImportJobSpec, sink: JsonlProgressSink, application_factory=create_application) -> int:
    try:
        app = application_factory(sink)
        project = Path(spec.project)
        if spec.operation == "import":
            from ..application.imports import confined_source, workspace_destination, validate_source_tree
            source = confined_source(Path(spec.import_root), spec.source_id)
            validate_source_tree(source)
            # A configured draft is already reserved; legacy imports reserve a new directory.
            if spec.reprepare:
                from ..application.source_preflight import source_signature
                from ..application.workspace_setup import read_workspace_setup
                setup = read_workspace_setup(project)
                if source_signature(Path(spec.import_root), spec.source_id) != setup['source_fingerprint']:
                    raise ValueError('Source changed since workspace setup.')
            elif project.exists():
                from ..application.workspace_setup import validate_draft_destination
                from ..application.source_preflight import source_signature
                setup = validate_draft_destination(project, spec.source_id)
                if source_signature(Path(spec.import_root), spec.source_id) != setup['source_fingerprint']:
                    raise ValueError('Source changed since workspace setup.')
            else:
                project.mkdir(exist_ok=False)
            command = ImportBookCommand(
                project, source,
                local_token_estimate=True,
                previous_volume=workspace_destination(Path(spec.workspace_root), spec.previous_volume) if spec.previous_volume else None,
                opf=confined_source(source, spec.opf) if spec.opf else None,
                input_encoding=spec.input_encoding, chapter_mode=spec.chapter_mode,
                chapter_selector=spec.chapter_selector, include_glob=spec.include_glob,
                sidecar_txt=spec.sidecar_txt, whole_section_limit=spec.whole_section_limit,
                profile=spec.profile, pass_profiles={int(k): v for k, v in (spec.pass_profiles or {}).items()},
                model=spec.model, context_size=spec.context_size, thinking=spec.thinking,
            )
            if spec.reprepare:
                app.projects.reprepare(command, spec.expected_revision)
            else:
                app.projects.import_book(command)
        elif spec.operation == "analyze":
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
        spec = parse_spec(json.loads(sys.stdin.readline(MAX_FRAME + 1)))
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
