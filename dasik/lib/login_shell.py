"""Reading a command's output through `su - <user>`, a login shell.

dasik drops privileges with ``su - <user> -c '<script>' -- …`` so the command
gets the user's own environment and credentials. A login shell also runs the
user's profile, and a profile that prints — a greeting, fastfetch, a motd —
writes into the same stdout the caller then parses. A guest showed it:
``git rev-parse --show-toplevel`` came back as
``hello from the profile\\n/home/test/cfg`` and ``save`` refused the repository.

The script prints a marker right before the command; everything up to it is the
profile's and is dropped. Only callers that parse stdout need it.
"""
from __future__ import annotations

_MARK = "\x1edasik\x1e"
# printf turns the octal escapes into the record-separator bytes of _MARK.
_PRINT_MARK = "printf '\\036dasik\\036'; "


def MARKED(script: str) -> str:  # noqa: N802 — reads as a constructor at call sites
    """*script*, preceded by printing the marker."""
    return _PRINT_MARK + script


def strip_login_noise(result):
    """Drop whatever the login profile printed before the marker, in place."""
    out = getattr(result, "stdout", None)
    if isinstance(out, bytes):
        mark = _MARK.encode()
        if mark in out:
            result.stdout = out.split(mark, 1)[1]
    elif isinstance(out, str) and _MARK in out:
        result.stdout = out.split(_MARK, 1)[1]
    return result
