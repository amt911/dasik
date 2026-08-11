"""`dasik hash-password` — prompt for a password, print the hash Arch would write.

Arch's login.defs sets ENCRYPT_METHOD YESCRYPT, so the default output is
`$y$j9T$…`, matching both /etc/shadow and what `dasik sync` captures from it.
`--method sha512` keeps the old `$6$…` for a target that wants it. The password
never reaches argv (world-readable via /proc). Referenced by CLAUDE.md's
agentic-verification smoke list.
"""
from unittest.mock import patch

from dasik.__main__ import _cmd_hash_password, main
from dasik.lib.exceptions.exceptions import PasswordHashError


def test_hash_password_prints_an_arch_native_yescrypt_hash(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["secret", "secret"]):
        rc = _cmd_hash_password()

    assert rc == 0
    assert capsys.readouterr().out.strip().startswith("$y$")


def test_hash_password_honors_the_sha512_method(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["secret", "secret"]):
        rc = _cmd_hash_password(method="sha512")

    assert rc == 0
    assert capsys.readouterr().out.strip().startswith("$6$")


def test_the_printed_hash_verifies_the_password(capsys):
    from dasik.lib.passwords import verify_password

    with patch("dasik.__main__.getpass.getpass", side_effect=["secret", "secret"]):
        _cmd_hash_password()

    assert verify_password("secret", capsys.readouterr().out.strip()) is True


def test_hash_password_mismatch(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["a", "b"]):
        rc = _cmd_hash_password()
    assert rc == 1
    assert "match" in capsys.readouterr().err.lower()


def test_hash_password_empty(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["", ""]):
        rc = _cmd_hash_password()
    assert rc == 1


def test_hash_password_verb_routes_through_main(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]):
        assert main(["hash-password"]) == 0
    assert capsys.readouterr().out.strip().startswith("$y$")


def test_hash_password_method_flag_routes_through_main(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]):
        assert main(["hash-password", "--method", "sha512"]) == 0
    assert capsys.readouterr().out.strip().startswith("$6$")


def test_hash_password_reports_a_hashing_failure(capsys):
    with patch("dasik.__main__.getpass.getpass", side_effect=["pw", "pw"]), \
         patch("dasik.__main__.hash_password",
               side_effect=PasswordHashError("libcrypt.so.2 not found")):
        rc = _cmd_hash_password()

    assert rc == 1
    assert "libcrypt.so.2 not found" in capsys.readouterr().err
