"""A failed streamed command must report the real cause (F-26).

The 2026-07-19 install ended with:

    su failed (exit 1): — file dialogs, screen sharing [installed]

The wrapper's name instead of the logical command, and an arbitrary 2000-char
tail that had scrolled past the actual summary — while the log did contain
`Packages failed to build: sunshine epson-inkjet-printer-escpr epsonscan2`.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.command_worker.command_worker import Command, _failure_excerpt
from dasik.lib.exceptions.exceptions import CommandExecutionError


def _fake_popen(output: str, rc: int = 1):
    proc = MagicMock()
    proc.stdout = iter([(line + "\n").encode() for line in output.splitlines()])
    proc.wait.return_value = rc
    return proc


_YAY_OUTPUT = "\n".join(
    ["==> Making package: sunshine 2025.1-1"]
    + ["  -> building…"] * 50
    + ["==> ERROR: A failure occurred in build()."]
    + ["curl: (22) The requested URL returned error: 403"]
    + ["Packages failed to build: sunshine epson-inkjet-printer-escpr epsonscan2"]
    + ["  — file dialogs, screen sharing [installed]"] * 200
)


def test_error_keeps_the_logical_command_not_the_wrapper():
    with patch("dasik.lib.command_worker.command_worker.subprocess.Popen",
               return_value=_fake_popen(_YAY_OUTPUT)), \
         patch("dasik.lib.command_worker.command_worker.Command._locate_binary",
               return_value="/usr/bin/su"):
        with pytest.raises(CommandExecutionError) as exc:
            Command.execute("su", ["-", "builder"], check=True, stream=True,
                            label="yay -S")
    assert str(exc.value).startswith("yay -S failed (exit 1)")


def test_error_surfaces_the_failure_summary_not_just_the_tail():
    with patch("dasik.lib.command_worker.command_worker.subprocess.Popen",
               return_value=_fake_popen(_YAY_OUTPUT)), \
         patch("dasik.lib.command_worker.command_worker.Command._locate_binary",
               return_value="/usr/bin/su"):
        with pytest.raises(CommandExecutionError) as exc:
            Command.execute("su", [], check=True, stream=True, label="yay -S")
    message = str(exc.value)
    assert "Packages failed to build: sunshine" in message
    assert "==> ERROR: A failure occurred in build()." in message
    assert "403" in message


def test_error_points_at_the_full_log(tmp_path):
    from dasik.lib.logging import run_logger
    log = tmp_path / "dasik.log"
    run_logger.configure(log_path=log, verbose=False, color=False)
    try:
        with patch("dasik.lib.command_worker.command_worker.subprocess.Popen",
                   return_value=_fake_popen(_YAY_OUTPUT)), \
             patch("dasik.lib.command_worker.command_worker.Command._locate_binary",
                   return_value="/usr/bin/su"):
            with pytest.raises(CommandExecutionError) as exc:
                Command.execute("su", [], check=True, stream=True, label="yay -S")
        assert str(log) in str(exc.value)
    finally:
        run_logger.reset()


# --- the excerpt itself ---------------------------------------------------- #

def test_excerpt_collects_error_lines_in_order():
    excerpt = _failure_excerpt(_YAY_OUTPUT)
    assert excerpt.index("==> ERROR") < excerpt.index("Packages failed to build")


def test_excerpt_falls_back_to_the_tail_when_nothing_matches():
    output = "\n".join(f"line {i}" for i in range(100))
    excerpt = _failure_excerpt(output)
    assert "line 99" in excerpt


def test_excerpt_is_bounded():
    output = "\n".join(["error: something broke"] * 5000)
    assert len(_failure_excerpt(output)) <= 4000
