"""RunLogger — records every shelled-out command to a debug log file and shows
failures in red on the console.

Design:
  * The **file** gets everything: argv, stdout, stderr and exit code of every
    command, always. This is the ``dasik-<verb>-<date>.log`` install log the user
    inspects after a failed run.
  * The **console** stays quiet by default, echoes the command stream under
    ``--verbose``, and always prints ``error()`` messages in red — that is how a
    failed ``pacman``/``dracut`` stops being a silent captured pipe.

A module-level singleton (``configure`` / ``get`` / ``reset``) lets the deep
``Command.execute`` call-site reach the logger without threading it through every
action. ``get()`` before ``configure()`` returns a quiet no-op logger, so library
code and tests never depend on CLI wiring.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, List, Optional

from colorama import Fore, Style, init as _colorama_init

_colorama_init(autoreset=True)


def _to_text(data: "bytes | str | None") -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


class RunLogger:
    """Records commands to a file and surfaces failures on the console."""

    def __init__(
        self,
        log_path: "str | Path | None" = None,
        verbose: bool = False,
        color: bool = True,
        stream: "IO[str] | None" = None,
    ) -> None:
        self.log_path: Optional[Path] = Path(log_path) if log_path else None
        self.verbose = verbose
        self.color = color
        self._stream: IO[str] = stream if stream is not None else sys.stderr
        self._fh: Optional[IO[str]] = None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.log_path.open("a", encoding="utf-8")

    # -- file ---------------------------------------------------------------

    def _write_file(self, text: str) -> None:
        if self._fh is not None:
            self._fh.write(text)
            self._fh.flush()

    # -- console ------------------------------------------------------------

    def _console(self, text: str) -> None:
        print(text, file=self._stream)

    def _red(self, s: str) -> str:
        return f"{Fore.RED}{s}{Style.RESET_ALL}" if self.color else s

    def _dim(self, s: str) -> str:
        return f"{Style.DIM}{s}{Style.RESET_ALL}" if self.color else s

    # -- API ----------------------------------------------------------------

    def record(
        self,
        argv: List[str],
        returncode: int,
        stdout: "bytes | str | None" = b"",
        stderr: "bytes | str | None" = b"",
    ) -> None:
        """Record one command run: to the file always; to the console when
        verbose. Never raises on a non-zero exit — surfacing a failure as an
        error is the caller's decision (``Command.execute(check=True)``), because
        many non-zero exits are benign probes."""
        cmd = " ".join(argv)
        out = _to_text(stdout)
        err = _to_text(stderr)

        self._write_file(f"$ {cmd}\n")
        if out:
            self._write_file(out if out.endswith("\n") else out + "\n")
        if err:
            self._write_file(err if err.endswith("\n") else err + "\n")
        self._write_file(f"[exit {returncode}]\n\n")

        if self.verbose:
            self._console(self._dim(f"$ {cmd}"))
            if out:
                self._console(out.rstrip("\n"))
            if err:
                self._console(err.rstrip("\n"))

    def error(self, message: str, detail: str = "") -> None:
        """Print an error in red on the console (always) and plainly to the file.

        Used for command failures and any other hard error worth putting in front
        of the user."""
        self._write_file(f"[ERROR] {message}\n")
        if detail:
            self._write_file(detail if detail.endswith("\n") else detail + "\n")

        self._console(self._red(f"error: {message}"))
        if detail:
            self._console(self._red(detail.rstrip("\n")))

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


# -- module-level singleton -------------------------------------------------

_LOGGER: Optional[RunLogger] = None


def configure(
    log_path: "str | Path | None" = None,
    verbose: bool = False,
    color: bool = True,
) -> RunLogger:
    """Install the process-wide logger and return it."""
    global _LOGGER
    _LOGGER = RunLogger(log_path=log_path, verbose=verbose, color=color)
    return _LOGGER


def get() -> RunLogger:
    """The process-wide logger. Before ``configure`` this is a quiet no-op logger
    (no file, not verbose), so library code never depends on CLI wiring."""
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = RunLogger()
    return _LOGGER


def reset() -> None:
    """Drop the current logger (closing its file). For tests and CLI teardown."""
    global _LOGGER
    if _LOGGER is not None:
        _LOGGER.close()
    _LOGGER = None
