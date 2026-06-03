from unittest.mock import patch

from dasik.lib.command_worker.command_worker import Command
from dasik.lib.target.target import Target


def _run_argv():
    """Patch subprocess.run + which; return the argv list Command passed."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return "result"

    return captured, fake_run


def test_no_chroot_runs_directly():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
        Command.execute("ls", ["-la"])
    assert captured["argv"] == ["ls", "-la"]


def test_legacy_run_as_chroot_uses_mnt():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run), \
         patch("dasik.lib.command_worker.command_worker.which", return_value="/usr/bin/arch-chroot"):
        Command.execute("pacman", ["-Q"], run_as_chroot=True)
    assert captured["argv"] == ["/usr/bin/arch-chroot", "/mnt", "pacman", "-Q"]


def test_target_mnt_uses_arch_chroot():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run), \
         patch("dasik.lib.command_worker.command_worker.which", return_value="/usr/bin/arch-chroot"):
        Command.execute("pacman", ["-Q"], target=Target(root="/mnt"))
    assert captured["argv"] == ["/usr/bin/arch-chroot", "/mnt", "pacman", "-Q"]


def test_target_host_runs_directly():
    captured, fake_run = _run_argv()
    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
        Command.execute("pacman", ["-Q"], target=Target(root="/"))
    assert captured["argv"] == ["pacman", "-Q"]


# --- execute_checked: surface failures (no silent swallow) ---------------- #
from unittest.mock import MagicMock
import pytest
from dasik.lib.exceptions.exceptions import CommandExecutionError


def _result(rc=0, out=b"", err=b""):
    return MagicMock(returncode=rc, stdout=out, stderr=err)


def test_execute_checked_returns_on_success():
    with patch("dasik.lib.command_worker.command_worker.subprocess.run",
               return_value=_result(rc=0, out=b"UUID=abc")):
        r = Command.execute_checked("genfstab", ["-U", "/mnt"])
    assert r.stdout == b"UUID=abc"


def test_execute_checked_raises_on_nonzero_with_stderr():
    with patch("dasik.lib.command_worker.command_worker.subprocess.run",
               return_value=_result(rc=1, err=b"error: not enough free disk space")):
        with pytest.raises(CommandExecutionError) as e:
            Command.execute_checked("pacstrap", ["-K", "/mnt", "base"])
    msg = str(e.value)
    assert "pacstrap" in msg and "not enough free disk space" in msg


def test_execute_checked_streams_when_verbose():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["kwargs"] = kwargs
        return _result(rc=0)

    Command.verbose = True
    try:
        with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
            Command.execute_checked("pacstrap", ["-K", "/mnt", "base"])
    finally:
        Command.verbose = False
    # verbose => stream live (no PIPE capture)
    assert "stdout" not in captured["kwargs"]
    assert "stderr" not in captured["kwargs"]


def test_execute_checked_capture_pipes_even_when_verbose():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["kwargs"] = kwargs
        return _result(rc=0, out=b"UUID=x")

    Command.verbose = True
    try:
        with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run):
            r = Command.execute_checked("genfstab", ["-U", "/mnt"], capture=True)
    finally:
        Command.verbose = False
    assert captured["kwargs"].get("stdout") is not None   # genfstab must capture
    assert r.stdout == b"UUID=x"


def test_execute_checked_chroot_prefix():
    captured = {}

    def fake_run(argv, **k):
        captured["argv"] = argv
        return _result(rc=0)

    with patch("dasik.lib.command_worker.command_worker.subprocess.run", fake_run), \
         patch("dasik.lib.command_worker.command_worker.which", return_value="/usr/bin/arch-chroot"):
        Command.execute_checked("pacman", ["-S", "grub"], target=Target(root="/mnt"))
    assert captured["argv"] == ["/usr/bin/arch-chroot", "/mnt", "pacman", "-S", "grub"]
