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
