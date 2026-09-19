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
