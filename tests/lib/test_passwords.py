"""`dasik hash-password` must produce the hash Arch itself would write.

Arch sets `ENCRYPT_METHOD YESCRYPT` in /etc/login.defs, so `passwd`/`useradd`
store `$y$j9T$…` — which is also what `dasik sync` captures out of /etc/shadow.
The CLI used to shell out to `openssl passwd -6` (sha512crypt, `$6$…`), so a
hand-written config and a synced one disagreed on format for no reason.
"""
import pytest

from dasik.lib import passwords
from dasik.lib.exceptions.exceptions import PasswordHashError


def test_default_is_the_arch_native_yescrypt():
    assert passwords.hash_password("hunter2").startswith("$y$")


def test_sha512_is_still_available_for_older_targets():
    assert passwords.hash_password("hunter2", method="sha512").startswith("$6$")


def test_the_hash_verifies_the_password_it_was_made_from():
    hashed = passwords.hash_password("hunter2")

    assert passwords.verify_password("hunter2", hashed) is True
    assert passwords.verify_password("hunter3", hashed) is False


def test_each_call_salts_independently():
    assert passwords.hash_password("hunter2") != passwords.hash_password("hunter2")


def test_an_unknown_method_is_rejected():
    with pytest.raises(PasswordHashError):
        passwords.hash_password("hunter2", method="rot13")


def test_sha512_falls_back_to_openssl_without_libxcrypt(monkeypatch):
    """The ISO always has libxcrypt, but a dev box may not — sha512crypt is
    reachable with nothing but openssl."""
    monkeypatch.setattr(passwords, "_libcrypt", lambda: None)

    assert passwords.hash_password("hunter2", method="sha512").startswith("$6$")


def test_yescrypt_without_libxcrypt_says_what_to_do(monkeypatch):
    """openssl cannot produce yescrypt, so silently downgrading the format —
    the very thing this change fixes — is not an option."""
    monkeypatch.setattr(passwords, "_libcrypt", lambda: None)

    with pytest.raises(PasswordHashError, match="sha512"):
        passwords.hash_password("hunter2")


def test_the_openssl_fallback_never_puts_the_password_on_argv(monkeypatch):
    """argv is world-readable through /proc, so the password goes over stdin."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")

        class Result:
            returncode = 0
            stdout = b"$6$salt$hash\n"
            stderr = b""

        return Result()

    monkeypatch.setattr(passwords, "_libcrypt", lambda: None)
    monkeypatch.setattr(passwords.subprocess, "run", fake_run)

    assert passwords.hash_password("hunter2", method="sha512") == "$6$salt$hash"
    assert "hunter2" not in " ".join(captured["argv"])
    assert captured["input"] == b"hunter2"
