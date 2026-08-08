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

import re
import sys
from pathlib import Path
from typing import IO, List, Optional

from colorama import Fore, Style, init as _colorama_init

_colorama_init(autoreset=True)


_REDACTED = "<redacted>"

# Commands that take a password hash on argv (`usermod -p <hash> <name>`), which
# would otherwise be recorded verbatim: the install log is the artifact users
# paste into bug reports.
_PASSWORD_COMMANDS = {"usermod", "useradd", "chpasswd"}
_PASSWORD_FLAGS = {"-p", "--password"}

# crypt(3) hashes as they appear inside a message that already joined an argv
# ($y$ yescrypt, $6$ sha512, $2b$ bcrypt, $argon2id$ …).
_HASH_RE = re.compile(r"\$(?:y|gy|7|6|5|1|2[abxy]?|argon2[a-z]*)\$\S+")


def redact_argv(argv: List[str]) -> List[str]:
    """Copy of *argv* with any password-hash argument replaced.

    Positional (not pattern) matching: only the value right after ``-p`` of a
    password command is touched, so ``mkdir -p`` and ``pacman -Qp`` keep reading
    as they ran.
    """
    if not any(part in _PASSWORD_COMMANDS for part in argv):
        return list(argv)
    out = list(argv)
    for i, part in enumerate(out[:-1]):
        if part in _PASSWORD_FLAGS:
            out[i + 1] = _REDACTED
    return out


def redact_text(text: str) -> str:
    """Blank out crypt hashes anywhere in free text (a message that embedded an
    argv, or a command echoing its own arguments)."""
    if "$" not in text:
        return text
    return _HASH_RE.sub(_REDACTED, text)


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

    def _yellow(self, s: str) -> str:
        return f"{Fore.YELLOW}{s}{Style.RESET_ALL}" if self.color else s

    def _dim(self, s: str) -> str:
        return f"{Style.DIM}{s}{Style.RESET_ALL}" if self.color else s

    # -- API ----------------------------------------------------------------

    def record(
        self,
        argv: List[str],
        returncode: int,
        stdout: "bytes | str | None" = b"",
        stderr: "bytes | str | None" = b"",
        echoed: bool = False,
    ) -> None:
        """Record one command run: to the file always; to the console when
        verbose. Never raises on a non-zero exit — surfacing a failure as an
        error is the caller's decision (``Command.execute(check=True)``), because
        many non-zero exits are benign probes.

        ``echoed=True`` means the output already went to the console live (a
        ``stream=True`` run echoed each line via :meth:`stream_line`): the file
        still gets the full block, but the console echo is suppressed here to
        avoid printing everything twice."""
        cmd = " ".join(redact_argv(argv))
        out = redact_text(_to_text(stdout))
        err = redact_text(_to_text(stderr))

        self._write_file(f"$ {cmd}\n")
        if out:
            self._write_file(out if out.endswith("\n") else out + "\n")
        if err:
            self._write_file(err if err.endswith("\n") else err + "\n")
        self._write_file(f"[exit {returncode}]\n\n")

        if self.verbose and not echoed:
            self._console(self._dim(f"$ {cmd}"))
            if out:
                self._console(out.rstrip("\n"))
            if err:
                self._console(err.rstrip("\n"))

    def stream_start(self, argv: List[str]) -> None:
        """Announce a streaming command on the console (verbose only).

        Console-only: the file record is written once, at the end, by
        :meth:`record` — ``stream_start``/``stream_line`` never touch the file."""
        if self.verbose:
            self._console(self._dim(f"$ {' '.join(redact_argv(argv))}"))

    def stream_line(self, line: str) -> None:
        """Echo one live output line on the console (verbose only). Never writes
        to the file (see :meth:`stream_start`)."""
        if self.verbose:
            self._console(line)

    def error(self, message: str, detail: str = "") -> None:
        """Print an error in red on the console (always) and plainly to the file.

        Used for command failures and any other hard error worth putting in front
        of the user."""
        message, detail = redact_text(message), redact_text(detail)
        self._write_file(f"[ERROR] {message}\n")
        if detail:
            self._write_file(detail if detail.endswith("\n") else detail + "\n")

        self._console(self._red(f"error: {message}"))
        if detail:
            self._console(self._red(detail.rstrip("\n")))

    def warning(self, message: str, detail: str = "") -> None:
        """Print a warning in yellow on the console (always, even without
        ``--verbose``) and plainly to the file with a ``[WARNING]`` prefix.

        Used for non-fatal notices the user must still see — e.g. a declared
        package skipped because no source was found."""
        message, detail = redact_text(message), redact_text(detail)
        self._write_file(f"[WARNING] {message}\n")
        if detail:
            self._write_file(detail if detail.endswith("\n") else detail + "\n")

        self._console(self._yellow(f"warning: {message}"))
        if detail:
            self._console(self._yellow(detail.rstrip("\n")))

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
