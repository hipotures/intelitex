from __future__ import annotations

import sys
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, Task
from rich.text import Text


class DeterminateBarColumn(BarColumn):
    def render(self, task: Task):
        if task.total is None:
            return Text("")
        return super().render(task)


class DeterminateMofNColumn(MofNCompleteColumn):
    def render(self, task: Task):
        if task.total is None:
            return Text("")
        return super().render(task)


class Display:
    """Two progress levels plus a live activity line. Deliberately no ETA/timer."""
    def __init__(self, quiet: bool = False):
        self.console = Console(stderr=True)
        self.quiet = quiet
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", markup=False),
            DeterminateBarColumn(), DeterminateMofNColumn(),
            console=self.console, disable=quiet or not sys.stderr.isatty(),
            refresh_per_second=4, transient=True,
        )
        self.top = self.progress.add_task("Book", total=1)
        self.lower = self.progress.add_task("Chapter", total=1)
        self.activity = self.progress.add_task("Waiting", total=None)
        self._phase = ""

    def __enter__(self):
        self.progress.start()
        return self

    def __exit__(self, *args):
        self.progress.stop()

    def emit(self, event):
        """Render a presentation-neutral application progress event."""
        values = event.values
        if event.kind == "message":
            self.message(event.message)
        elif event.kind == "phase":
            self.phase(event.message)
        elif event.kind == "import_files_progress":
            self.overall("Import | HTML files", event.current, event.total)
        elif event.kind == "import_file_started":
            self.phase(f"Import: {values['filename']}")
        elif event.kind == "import_plan_progress":
            self.chapter(
                f"Plan | section {values['section_number']}/{event.total}", event.current, event.total,
            )
        elif event.kind == "analysis_progress":
            self.overall("P1/5 | analysis sections", event.current, event.total)
        elif event.kind == "analysis_unit_progress":
            self.chapter(
                f"Chapter/section {values['chapter_number']}/{values['chapter_total']} | analysis part",
                event.current, event.total,
            )
        elif event.kind == "analysis_completed":
            self.phase("Analysis complete; human terminology review required. No prose translation generated.")
            self.message(f"Review data: {values['review_path']}\nNext: review --project {values['project']}")
        elif event.kind == "translation_progress":
            self.overall(
                f"Translation | book units | this run {values['run_current']}/{values['run_total']}",
                event.current, event.total,
            )
        elif event.kind == "translation_unit_progress":
            self.chapter(
                f"Chapter {values['chapter_number']}/{values['chapter_total']} | "
                f"unit {values['chunk_index']}/{values['chunk_total']} | passes P2-P5",
                event.current, event.total,
            )
        elif event.kind == "provider_waiting":
            amount = int(values["input_value"])
            measurement = (f"input upper bound {amount:,} UTF-8 bytes"
                           if values["input_unit"] == "utf8_bytes" else f"input {amount:,} tokens")
            self.phase(
                f"P{values['pass_no']}/5 | {values['task_key']} | waiting for model; {measurement}"
            )
        elif event.kind == "generation_progress":
            self.received(int(values["answer_chars"]), int(values["reasoning_chars"]))
        elif event.kind == "pass_recovered":
            self.phase(
                f"P{values['pass_no']}/5 | {values['task_key']} | "
                "recovered completed response; no model call"
            )
        elif event.kind == "recovery_repaired":
            self.message(
                f"Recovered {values['task_key']}; applied {values['repair_count']} conservative "
                f"validation repair(s). See {values['recovery_path']}"
            )
        elif event.kind == "model_changed":
            self.message(
                "WARNING: different model. Fixed chunk boundaries remain; per-request token counts are "
                "recalculated. Old successful outputs are not replaced."
            )
        elif event.kind == "translation_stopped":
            self.phase(
                f"Stopped after {values['completed_units']} completed unit(s). "
                "Rerun translate --continue N to proceed."
            )

    def overall(self, label: str, done: int, total: int):
        self.progress.update(self.top, description=label, completed=done, total=max(total, 1))

    def chapter(self, label: str, done: int, total: int):
        self.progress.update(self.lower, description=label, completed=done, total=max(total, 1))

    def phase(self, label: str):
        self._phase = label
        self.progress.update(self.activity, description=label, completed=0, total=None)
        if not self.quiet and not sys.stderr.isatty():
            self.console.print(label, markup=False)

    def received(self, answer_chars: int, reasoning_chars: int):
        self.progress.update(self.activity, description=(
            f"{self._phase.replace('waiting for model;', 'receiving;')} | received {answer_chars:,} answer chars; "
            f"{reasoning_chars:,} reasoning chars"
        ))

    def stop_progress(self):
        """Switch long-lived interactive commands to plain terminal output."""
        self.progress.stop()

    def message(self, text: str):
        if not self.quiet:
            self.console.print(text, markup=False)
