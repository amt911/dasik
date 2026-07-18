"""Command.execute(stream=True) — live line-by-line output for long commands.

Long installers (pacman, pacstrap, makepkg) capture into a PIPE and only surface
at the end. stream=True runs them under Popen, echoing each line as it arrives
(console, verbose only) while still recording the full output to the log file
exactly once. stderr is merged into stdout so the temporal order is preserved.
"""
from unittest.mock import MagicMock

import io
import subprocess

import pytest

import dasik.lib.command_worker.command_worker as cw
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.target.target import Target
from dasik.lib.exceptions.exceptions import CommandExecutionError


class _FakeProc:
    """A Popen stand-in: `stdout` yields byte lines, `wait()` returns the rc."""

    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self):
        return self._rc


def _fake_result(returncode=0, stdout=b"", stderr=b""):
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _patch_popen(lines, rc):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProc(lines, rc)

    return captured, fake_popen


def test_stream_uses_popen_and_records_once(monkeypatch):
    captured, fake_popen = _patch_popen([b"a\n", b"b\n"], 0)
    logger = MagicMock()
    monkeypatch.setattr(cw.run_logger, "get", lambda: logger)
    monkeypatch.setattr(cw.subprocess, "Popen", fake_popen)

    def boom_run(*a, **k):
        raise AssertionError("subprocess.run must not run on the stream path")
    monkeypatch.setattr(cw.subprocess, "run", boom_run)

    result = Command.execute("pacman", ["-S", "x"], target=Target(root="/"), stream=True)

    logger.record.assert_called_once()
    args, kwargs = logger.record.call_args
    assert kwargs.get("echoed") is True
    assert args[2] == b"a\nb\n"          # full stdout recorded once
    assert result.returncode == 0
    assert result.stdout == b"a\nb\n"    # return contract intact


def test_stream_popen_merges_stderr(monkeypatch):
    captured, fake_popen = _patch_popen([b"x\n"], 0)
    monkeypatch.setattr(cw.run_logger, "get", lambda: MagicMock())
    monkeypatch.setattr(cw.subprocess, "Popen", fake_popen)

    Command.execute("ls", [], stream=True)

    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    assert captured["kwargs"]["stderr"] is subprocess.STDOUT


def test_stream_echoes_lines_only_when_verbose(monkeypatch):
    import dasik.lib.logging.run_logger as rl

    # verbose -> lines appear live on the console stream
    stream = io.StringIO()
    logger = rl.RunLogger(log_path=None, verbose=True, color=False, stream=stream)
    monkeypatch.setattr(cw.run_logger, "get", lambda: logger)
    _, fake_popen = _patch_popen([b"line-one\n", b"line-two\n"], 0)
    monkeypatch.setattr(cw.subprocess, "Popen", fake_popen)
    Command.execute("pacman", ["-S", "x"], stream=True)
    out = stream.getvalue()
    assert "line-one" in out and "line-two" in out

    # not verbose -> console stays silent
    stream2 = io.StringIO()
    logger2 = rl.RunLogger(log_path=None, verbose=False, color=False, stream=stream2)
    monkeypatch.setattr(cw.run_logger, "get", lambda: logger2)
    _, fake_popen2 = _patch_popen([b"line-one\n"], 0)
    monkeypatch.setattr(cw.subprocess, "Popen", fake_popen2)
    Command.execute("pacman", ["-S", "x"], stream=True)
    assert stream2.getvalue() == ""


def test_stream_check_true_raises_with_output_tail(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(cw.run_logger, "get", lambda: logger)
    _, fake_popen = _patch_popen([b"building...\n", b"fatal error 42\n"], 8)
    monkeypatch.setattr(cw.subprocess, "Popen", fake_popen)

    with pytest.raises(CommandExecutionError) as exc:
        Command.execute("makepkg", ["-si"], stream=True, check=True)

    assert "fatal error 42" in str(exc.value)   # tail of merged output
    logger.error.assert_called_once()


def test_stream_rejects_input():
    with pytest.raises(ValueError):
        Command.execute("cat", [], stream=True, input=b"x")


def test_default_nonstream_path_unchanged(monkeypatch):
    called = {"run": False}

    def fake_run(argv, **kw):
        called["run"] = True
        return _fake_result(0, b"ok\n", b"")

    def boom_popen(argv, **kw):
        raise AssertionError("Popen must not run on the default (non-stream) path")

    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    monkeypatch.setattr(cw.subprocess, "Popen", boom_popen)
    monkeypatch.setattr(cw.run_logger, "get", lambda: MagicMock())

    Command.execute("pacman", ["-Q"], target=Target(root="/"))
    assert called["run"] is True
