"""Which libxcrypt `passwords` binds to.

The soname is distribution-specific: Arch (and the install ISO) ship libxcrypt
as ``libcrypt.so.2``, Debian/Ubuntu as ``libcrypt.so.1``. Pinning one of them
made every yescrypt test fail on a machine that has libxcrypt under the other
name — which is what happens on the CI runner, an Ubuntu box.

Not every ``libcrypt.so.1`` is libxcrypt, though: glibc's own libcrypt carried
that soname and has no ``crypt_gensalt`` at all, so a candidate that lacks the
libxcrypt API has to be skipped rather than returned half-bound.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import ctypes

from dasik.lib import passwords


def _fake_libxcrypt():
    """An object that answers the two symbols `_libcrypt` binds."""
    return SimpleNamespace(crypt_gensalt=MagicMock(), crypt=MagicMock())


def test_the_arch_soname_is_tried_first(monkeypatch):
    """On the ISO/target `libcrypt.so.2` is the one shadow itself uses."""
    tried = []
    lib = _fake_libxcrypt()

    def fake_cdll(name, **kwargs):
        tried.append(name)
        return lib

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    assert passwords._libcrypt() is lib
    assert tried == ["libcrypt.so.2"]


def test_falls_back_to_the_debian_soname(monkeypatch):
    """Ubuntu/Debian ship libxcrypt as `libcrypt.so.1` — yescrypt included."""
    tried = []
    lib = _fake_libxcrypt()

    def fake_cdll(name, **kwargs):
        tried.append(name)
        if name != "libcrypt.so.1":
            raise OSError(f"{name}: cannot open shared object file")
        return lib

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    assert passwords._libcrypt() is lib
    assert tried == ["libcrypt.so.2", "libcrypt.so.1"]


def test_a_libcrypt_without_the_libxcrypt_api_is_skipped(monkeypatch):
    """glibc's libcrypt has crypt() but no crypt_gensalt(): it cannot generate a
    yescrypt setting, so binding it would fail later with a confusing error."""
    lib = _fake_libxcrypt()

    def fake_cdll(name, **kwargs):
        if name == "libcrypt.so.2":
            return SimpleNamespace(crypt=MagicMock())  # no crypt_gensalt
        return lib

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    assert passwords._libcrypt() is lib


def test_no_libxcrypt_at_all_is_none(monkeypatch):
    def fake_cdll(name, **kwargs):
        raise OSError(f"{name}: cannot open shared object file")

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)

    assert passwords._libcrypt() is None


def test_the_error_names_every_soname_it_looked_for(monkeypatch):
    """A user on a third distribution has to know what to install."""
    monkeypatch.setattr(passwords, "_libcrypt", lambda: None)

    try:
        passwords.hash_password("hunter2")
    except Exception as exc:  # PasswordHashError
        for soname in passwords._LIBCRYPT_SONAMES:
            assert soname in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("hash_password did not raise without libxcrypt")
