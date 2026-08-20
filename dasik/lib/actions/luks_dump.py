"""Reading a LUKS2 header as a PROGRAM rather than as a person.

``cryptsetup luksDump`` does not print the Tokens section by itself. For every
token whose type has an EXTERNAL PLUGIN installed — systemd ships
``libcryptsetup-token-systemd-fido2.so`` and its tpm2 sibling — it hands the
rendering to that plugin, and the plugin writes to **stderr**.

A terminal merges the two streams, so a human sees a complete dump. A program
reading ``result.stdout`` sees this, and only this::

    Tokens:
      0: systemd-fido2

No second token, no ``Keyslot:`` lines. Measured on cryptsetup 2.8.7 against a
header carrying two ``systemd-fido2`` tokens.

That silent truncation reached three decisions, all of which were wrong on a
real laptop with two keys enrolled:

* the token COUNT — one instead of two, so `dasik plan` asked for a second key
  that was already there, for ever;
* the keyslot NUMBERS a removal names. Empty, so the wipe fell back to
  ``--wipe-slot=fido2``, which takes EVERY fido2 keyslot: going from two keys to
  one would have taken both;
* the "is there a keyslot no token owns?" guard, which is the thing standing
  between a removal and an unopenable disk.

``--disable-external-tokens`` keeps cryptsetup's own generic rendering — type
and keyslot for every token, on stdout, where a program can read it. It is the
only flag here that is about correctness rather than taste.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

_NO_PLUGINS = "--disable-external-tokens"


def _text(raw: Any) -> str:
    """Bytes or str to str; anything else (an unset mock attribute) to ''."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else ""


def read_dump(execute: Callable[..., Any], device: str, **kwargs) -> Optional[str]:
    """``cryptsetup luksDump`` for *device*, complete, or ``None``.

    *execute* is the caller's ``Command.execute`` — passed in rather than
    imported so the call is still made from the caller's module, and so the
    per-module patching every test in this repository does keeps working.

    ``None`` means the header could not be read AT ALL, which callers must keep
    apart from "read it, there are no tokens": one is an answer, the other is
    the absence of one, and clearing a declared flag on the second is how a
    config silently disarms itself.
    """
    for args in (["luksDump", _NO_PLUGINS, device], ["luksDump", device]):
        try:
            result = execute("cryptsetup", list(args), **kwargs)
        # The first attempt probes a flag an older cryptsetup does not have, so
        # falling through to the plain form is the whole point; a second failure
        # returns None, which callers read as "unreadable header" and never as
        # "no tokens".
        except Exception:      # nosec B112 - fall through to the plain luksDump
            continue
        code = getattr(result, "returncode", 0)
        if isinstance(code, int) and code != 0:
            continue
        text = _text(getattr(result, "stdout", ""))
        if _NO_PLUGINS not in args:
            # The fallback ran, so plugins were free to write: whatever they put
            # on stderr is the other half of the section. Ugly, and better than
            # counting one token where the header holds two.
            text += _text(getattr(result, "stderr", ""))
        return text
    return None
