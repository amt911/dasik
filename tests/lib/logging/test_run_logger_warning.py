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
