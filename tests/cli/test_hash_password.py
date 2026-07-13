"""`dasik hash-password` — prompt for a password, print its sha512crypt hash.

Lets users generate the `hashed_password` value a UserModel expects without
leaving the tool. The password is fed to `openssl passwd -6` over **stdin**,
never on argv (argv is world-readable via /proc). Referenced by CLAUDE.md's
agentic-verification smoke list.
"""
from unittest.mock import MagicMock, patch

from dasik.__main__ import _cmd_hash_password, main


def test_hash_password_prints_hash(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["secret", "secret"]), \
         patch("dasik.__main__.subprocess.run",
               return_value=MagicMock(returncode=0, stdout=b"$6$abc$def\n", stderr=b"")):
        rc = _cmd_hash_password()
    assert rc == 0
    assert "$6$abc$def" in capsys.readouterr().out


def test_hash_password_feeds_via_stdin_not_argv():
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return MagicMock(returncode=0, stdout=b"$6$x$y\n", stderr=b"")

    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]), \
         patch("dasik.__main__.subprocess.run", side_effect=fake_run):
        _cmd_hash_password()
    assert captured["argv"] == ["openssl", "passwd", "-6", "-stdin"]
    assert captured["input"] == b"pw"          # password via stdin, never in argv


def test_hash_password_mismatch(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["a", "b"]):
        rc = _cmd_hash_password()
    assert rc == 1
    assert "match" in capsys.readouterr().err.lower()


def test_hash_password_empty(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["", ""]):
        rc = _cmd_hash_password()
    assert rc == 1


def test_hash_password_verb_routes_through_main():
    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]), \
         patch("dasik.__main__.subprocess.run",
               return_value=MagicMock(returncode=0, stdout=b"$6$r$s\n", stderr=b"")):
        assert main(["hash-password"]) == 0


def test_hash_password_openssl_failure_returns_1(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]), \
         patch("dasik.__main__.subprocess.run",
               return_value=MagicMock(returncode=1, stdout=b"", stderr=b"boom")):
        rc = _cmd_hash_password()
    assert rc == 1
