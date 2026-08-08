"""The run log must never carry a password hash.

``UsersAction`` sets passwords with ``usermod -p <hash> <name>``, and RunLogger
records argv verbatim — so ``dasik-apply-<date>.log`` contained the yescrypt
hashes of every declared user (seen in the 2026-08-08 log the user pasted for
analysis). The log is the artifact people attach to bug reports; the secret is
redacted at the only place every command funnels through.
"""
from dasik.lib.logging.run_logger import RunLogger


def _log(tmp_path):
    return tmp_path / "run.log"


def test_usermod_password_hash_is_redacted(tmp_path):
    path = _log(tmp_path)
    logger = RunLogger(log_path=path)
    logger.record(["/usr/bin/arch-chroot", "/mnt", "usermod", "-p",
                   "$y$j9T$SECRETHASH$rest", "andres"], 0)
    text = path.read_text()
    assert "SECRETHASH" not in text
    assert "usermod -p <redacted> andres" in text


def test_useradd_password_hash_is_redacted(tmp_path):
    path = _log(tmp_path)
    RunLogger(log_path=path).record(
        ["useradd", "-m", "-p", "$6$SECRET", "test"], 0)
    text = path.read_text()
    assert "$6$SECRET" not in text
    assert "<redacted>" in text


def test_other_dash_p_options_are_untouched(tmp_path):
    """`-p` means something else almost everywhere (mkdir -p, pacman -Qp); only
    the password commands are rewritten."""
    path = _log(tmp_path)
    RunLogger(log_path=path).record(["mkdir", "-p", "/mnt/etc/pacman.d"], 0)
    assert "mkdir -p /mnt/etc/pacman.d" in path.read_text()


def test_redaction_survives_a_missing_value(tmp_path):
    """A malformed argv must not crash the logger."""
    path = _log(tmp_path)
    RunLogger(log_path=path).record(["usermod", "-p"], 1)
    assert "usermod -p" in path.read_text()


def test_streamed_command_is_redacted_too(tmp_path):
    path = _log(tmp_path)
    logger = RunLogger(log_path=path)
    logger.stream_start(["usermod", "-p", "$y$SECRET", "andres"])
    logger.record(["usermod", "-p", "$y$SECRET", "andres"], 0, echoed=True)
    assert "SECRET" not in path.read_text()


def test_error_line_is_redacted(tmp_path, capsys):
    """The console error prints the argv too."""
    path = _log(tmp_path)
    logger = RunLogger(log_path=path, color=False)
    logger.error("command failed (exit 1): " + " ".join(
        ["usermod", "-p", "$y$SECRET", "andres"]))
    assert "SECRET" not in path.read_text()
