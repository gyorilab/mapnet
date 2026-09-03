"""One log file per run, appended to by mapnet and by the tools it runs."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

LOG_ROOT = Path("logs")


class Log:
    """One run's log file, echoing to the terminal as it appends."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(
        cls, tool: str, source: Path, target: Path, stamp: str, root: Path
    ) -> Log:
        """Name one run's log after its tool, its pair and the stamp."""
        return cls(root / f"run_{tool}_{source.stem}_{target.stem}_{stamp}.log")

    def write(self, text: str) -> None:
        """Append one line, without echoing it."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n") + "\n")

    def say(self, text: str) -> None:
        """Print one line and append it."""
        print(text)
        self.write(text)

    def handle(self) -> TextIO:
        """Open the file for a subprocess to append into."""
        return self.path.open("a", encoding="utf-8")

    def run(self, command: Sequence[str]) -> None:
        """Run a command, echoing its output and appending it to this log."""
        with self.path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                handle.write(line)
            code = process.wait()
        if code != 0:
            raise RuntimeError(f"{command[0]} failed, see {self.path}: {self.tail()}")

    def tail(self) -> str:
        """Read the last non-empty line written."""
        lines = [line.strip() for line in self.path.read_text("utf-8").splitlines()]
        return next((line for line in reversed(lines) if line), "no output")
