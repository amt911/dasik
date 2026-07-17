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


def test_execute_passes_input_to_subprocess(monkeypatch):
    """Command.execute forwards `input=` to subprocess.run (LUKS passphrase over
    stdin)."""
    import dasik.lib.command_worker.command_worker as cw
    captured = {}

    def fake_run(argv, **kw):
        captured.update(kw)
        class R: stdout = b""; stderr = b""; returncode = 0
        return R()

    monkeypatch.setattr(cw, "which", lambda n: "/usr/bin/cryptsetup")
    monkeypatch.setattr(cw.subprocess, "run", fake_run)
    cw.Command.execute("cryptsetup", ["luksFormat", "--key-file", "-"], input=b"pw")
    assert captured.get("input") == b"pw"


def _fake_result(returncode=0, stdout=b"", stderr=b""):
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_execute_records_every_command_to_the_run_logger(monkeypatch, tmp_path):
    """Each Command.execute is logged (argv + output + exit) to the run log file."""
    import dasik.lib.command_worker.command_worker as cw
    import dasik.lib.logging.run_logger as rl

    monkeypatch.setattr(
        cw.subprocess, "run",
        lambda argv, **kw: _fake_result(0, b"resolving\n", b""),
    )
    log = tmp_path / "run.log"
    rl.reset()
    rl.configure(log_path=log, verbose=False, color=False)
    try:
        cw.Command.execute("pacman", ["-Q"], target=Target(root="/"))
    finally:
        rl.get().close()
        rl.reset()

    text = log.read_text()
    assert "pacman -Q" in text
    assert "resolving" in text


def test_execute_check_true_raises_on_nonzero(monkeypatch):
    """check=True turns a failed command into a CommandExecutionError so a silent
    pacman failure can no longer masquerade as success."""
    import dasik.lib.command_worker.command_worker as cw
    from dasik.lib.exceptions.exceptions import CommandExecutionError

    monkeypatch.setattr(
        cw.subprocess, "run",
        lambda argv, **kw: _fake_result(1, b"", b"error: target not found: antigravity\n"),
    )
    import pytest
    with pytest.raises(CommandExecutionError) as exc:
        cw.Command.execute("pacman", ["-S", "antigravity"],
                           target=Target(root="/"), check=True)
    assert "antigravity" in str(exc.value)


def test_execute_check_false_does_not_raise_on_nonzero(monkeypatch):
    """Default (check=False) preserves the existing 'return the result, caller
    inspects returncode' contract — benign probes (pacman -Qi missing pkg) rely
    on this."""
    import dasik.lib.command_worker.command_worker as cw

    monkeypatch.setattr(
        cw.subprocess, "run",
        lambda argv, **kw: _fake_result(1, b"", b"not installed\n"),
    )
    result = cw.Command.execute("pacman", ["-Qi", "firefox"], target=Target(root="/"))
    assert result.returncode == 1
