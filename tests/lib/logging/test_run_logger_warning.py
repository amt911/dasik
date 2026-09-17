"""RunLogger.warning — visible yellow console + [WARNING] in the log (PLAN v3 §7)."""
from __future__ import annotations

import io
import re

import dasik.lib.logging.run_logger as rl


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_warning_prints_yellow_to_console_and_plain_to_file(tmp_path):
    log = tmp_path / "dasik.log"
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=log, verbose=False, color=True, stream=stream)

    logger.warning("packages skipped because no source was found: foo, bar",
                   detail="They were not installed; dasik will retry them.")
    logger.close()

    console = stream.getvalue()
    assert "\x1b[33m" in console  # yellow (Fore.YELLOW)
    assert "packages skipped because no source was found: foo, bar" in _plain(console)
    assert "retry" in _plain(console)

    file_text = log.read_text()
    assert "\x1b[" not in file_text          # no ANSI leaks into the file
    assert "[WARNING]" in file_text
    assert "foo, bar" in file_text
    assert "retry" in file_text


def test_warning_is_visible_without_verbose(tmp_path):
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=False, color=False, stream=stream)
    logger.warning("skipped: x")
    assert "skipped: x" in stream.getvalue()


def test_warning_without_detail_ok():
    logger = rl.get()
    rl.reset()
    logger = rl.get()
    logger.warning("no detail")  # must not raise


def test_warning_on_a_closed_stream_does_not_raise(tmp_path):
    """A process-wide `RunLogger` singleton is not reset between every test
    (only tests that opt in do it, see `_fresh_run_logger`-style fixtures),
    and pytest's own capture machinery routinely swaps/closes the
    `sys.stderr`-derived stream objects it hands out across test/module
    boundaries. A caller emitting a warning (a nice-to-have console notice —
    the file log, when configured, is the authoritative record) must never
    crash because THAT stream went away — found via mutation testing's own
    "clean" baseline run hitting exactly this on an unrelated, correctly
    mocked test the moment `pacman_repositories_action` started calling
    `warning()` from a path many tests exercise incidentally."""
    log = tmp_path / "dasik.log"
    stream = io.StringIO()
    stream.close()
    logger = rl.RunLogger(log_path=log, verbose=False, color=False, stream=stream)

    logger.warning("keyring could not be read")  # must not raise
    logger.error("a hard failure too")            # same guarantee for error()

    # the file record still went through — only the console echo is best-effort
    file_text = log.read_text()
    assert "[WARNING] keyring could not be read" in file_text
    assert "[ERROR] a hard failure too" in file_text
