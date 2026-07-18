"""Tests for the RunLogger — dasik's install-output log + colored error surface.

The logger is the single observability chokepoint: every ``Command.execute``
records here, so a failed pacman/dracut run is (a) written to a debug log file
and (b) shown in red on the console instead of vanishing into a captured pipe.
"""
from __future__ import annotations

import io

import dasik.lib.logging.run_logger as rl


def _plain(text: str) -> str:
    """Strip ANSI color escapes so assertions read the message, not the codes."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_record_writes_command_output_and_exit_to_file(tmp_path):
    log = tmp_path / "dasik.log"
    logger = rl.RunLogger(log_path=log, verbose=False, color=False)

    logger.record(["pacman", "-S", "firefox"], returncode=1,
                  stdout=b"resolving deps\n", stderr=b"error: target not found: firefox\n")
    logger.close()

    text = log.read_text()
    assert "pacman -S firefox" in text
    assert "resolving deps" in text
    assert "error: target not found: firefox" in text
    assert "1" in text  # the exit code is recorded


def test_record_is_quiet_on_console_when_not_verbose(tmp_path):
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=False, color=False, stream=stream)

    logger.record(["pacman", "-Qi", "firefox"], returncode=0, stdout=b"ok\n", stderr=b"")

    assert stream.getvalue() == ""


def test_record_echoes_command_on_console_when_verbose(tmp_path):
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=True, color=False, stream=stream)

    logger.record(["pacman", "-S", "firefox"], returncode=0,
                  stdout=b"installed\n", stderr=b"")

    out = _plain(stream.getvalue())
    assert "pacman -S firefox" in out
    assert "installed" in out


def test_error_prints_red_to_console_and_plain_to_file(tmp_path):
    log = tmp_path / "dasik.log"
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=log, verbose=False, color=True, stream=stream)

    logger.error("command failed (exit 1): pacman -S antigravity",
                 detail="error: target not found: antigravity")
    logger.close()

    console = stream.getvalue()
    assert "\x1b[31m" in console  # red (Fore.RED) was emitted
    assert "target not found: antigravity" in _plain(console)

    file_text = log.read_text()
    assert "\x1b[" not in file_text  # no color codes leak into the log file
    assert "target not found: antigravity" in file_text


def test_configure_and_get_are_a_singleton(tmp_path):
    rl.reset()
    try:
        configured = rl.configure(log_path=tmp_path / "x.log", verbose=True, color=False)
        assert rl.get() is configured
        assert rl.get().verbose is True
    finally:
        rl.reset()


def test_get_without_configure_returns_a_quiet_noop_logger():
    rl.reset()
    logger = rl.get()
    assert logger.verbose is False
    assert logger.log_path is None
    # record on the default logger must not raise even with no file/stream set up
    logger.record(["true"], returncode=0)


# ---------------------------------------------------------------------- #
#  T2: streaming — record(echoed=…) + stream_start/stream_line            #
# ---------------------------------------------------------------------- #


def test_record_echoed_skips_console_but_writes_file(tmp_path):
    # stream=True already echoed the output live; record(echoed=True) must not
    # double it on the console, but the file still gets the full block.
    stream = io.StringIO()
    log = tmp_path / "run.log"
    logger = rl.RunLogger(log_path=log, verbose=True, color=False, stream=stream)

    logger.record(["makepkg", "-si"], returncode=0, stdout=b"done\n", stderr=b"",
                  echoed=True)
    logger.close()

    assert stream.getvalue() == ""          # not echoed again
    text = log.read_text()
    assert "makepkg -si" in text
    assert "done" in text
    assert "[exit 0]" in text


def test_record_not_echoed_still_echoes_when_verbose(tmp_path):
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=True, color=False, stream=stream)

    logger.record(["pacman", "-Q"], returncode=0, stdout=b"ok\n", stderr=b"")

    assert "pacman -Q" in stream.getvalue()   # default echoed=False unchanged


def test_stream_line_silent_without_verbose(tmp_path):
    stream = io.StringIO()
    log = tmp_path / "run.log"
    logger = rl.RunLogger(log_path=log, verbose=False, color=False, stream=stream)

    logger.stream_start(["makepkg", "-si"])
    logger.stream_line("building foo")
    logger.close()

    assert stream.getvalue() == ""            # console silent (not verbose)
    assert log.read_text() == ""              # stream_* never writes the file


def test_stream_line_echoes_on_console_when_verbose(tmp_path):
    stream = io.StringIO()
    log = tmp_path / "run.log"
    logger = rl.RunLogger(log_path=log, verbose=True, color=False, stream=stream)

    logger.stream_start(["makepkg", "-si"])
    logger.stream_line("building foo")
    logger.close()

    out = stream.getvalue()
    assert "makepkg -si" in out
    assert "building foo" in out
    assert log.read_text() == ""              # file untouched by stream_*
