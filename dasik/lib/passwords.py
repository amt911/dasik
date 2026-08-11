"""Password hashing in the format the target system itself uses.

Arch ships ``ENCRYPT_METHOD YESCRYPT`` in /etc/login.defs, so ``passwd`` and
``useradd`` write ``$y$j9T$…`` — and that is what ``dasik sync`` reads back out
of /etc/shadow. Hashes are produced through **libxcrypt** (``libcrypt.so.2``),
the very library shadow uses, rather than ``openssl passwd``: openssl knows
nothing about yescrypt and would silently hand back sha512crypt.

Python's own ``crypt`` module was removed in 3.13, hence the ctypes binding.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from typing import Optional

from .exceptions.exceptions import PasswordHashError

YESCRYPT = "yescrypt"
SHA512 = "sha512"

# crypt(3) setting prefixes; the prefix is what selects the algorithm.
_PREFIXES = {YESCRYPT: b"$y$", SHA512: b"$6$"}
_SALT_BYTES = 16
_LIBCRYPT = "libcrypt.so.2"


def _libcrypt() -> Optional[ctypes.CDLL]:
    """libxcrypt with its two entry points bound, or None if unavailable."""
    try:
        lib = ctypes.CDLL(_LIBCRYPT, use_errno=True)
    except OSError:
        return None
    lib.crypt_gensalt.restype = ctypes.c_char_p
    lib.crypt_gensalt.argtypes = [ctypes.c_char_p, ctypes.c_ulong,
                                  ctypes.c_char_p, ctypes.c_int]
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    return lib


def _openssl_sha512(password: str) -> str:
    """sha512crypt without libxcrypt. The password goes over **stdin**, never
    argv, which is world-readable through /proc."""
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=password.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise PasswordHashError(
            f"openssl passwd failed: {result.stderr.decode().strip()}")
    return result.stdout.decode().strip()


def hash_password(password: str, method: str = YESCRYPT) -> str:
    """*password* hashed with *method* (``yescrypt`` or ``sha512``).

    Raises ``PasswordHashError`` rather than falling back across formats: a
    downgrade to sha512crypt behind the user's back is the exact confusion this
    module exists to remove.
    """
    prefix = _PREFIXES.get(method)
    if prefix is None:
        raise PasswordHashError(
            f"unknown hash method {method!r}; use "
            f"{YESCRYPT!r} (the Arch default) or {SHA512!r}")

    lib = _libcrypt()
    if lib is None:
        if method == SHA512:
            return _openssl_sha512(password)
        raise PasswordHashError(
            f"{_LIBCRYPT} not found, and openssl cannot produce yescrypt. "
            f"Install libxcrypt, or ask for --method {SHA512}.")

    salt = lib.crypt_gensalt(prefix, 0, os.urandom(_SALT_BYTES), _SALT_BYTES)
    if not salt:
        raise PasswordHashError(
            f"crypt_gensalt failed for {prefix.decode()}: "
            f"{os.strerror(ctypes.get_errno())}")
    hashed = lib.crypt(password.encode(), salt)
    # crypt() signals failure by returning a "*0"-style string (or NULL), which
    # would otherwise be written into the config as if it were a hash.
    if not hashed or not hashed.startswith(prefix):
        raise PasswordHashError(f"crypt() did not return a {method} hash")
    return hashed.decode()


def verify_password(password: str, hashed: str) -> bool:
    """True when *password* hashes to *hashed* (the hash carries its own salt)."""
    lib = _libcrypt()
    if lib is None:
        raise PasswordHashError(f"{_LIBCRYPT} not found: cannot verify a hash")
    return lib.crypt(password.encode(), hashed.encode()) == hashed.encode()
