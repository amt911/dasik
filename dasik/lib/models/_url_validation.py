"""Shared control-character guard for model fields holding a URL or a git
source component (host, path, ref, subdir, ...).

``urlsplit`` silently strips ``\\t``, ``\\r`` and ``\\n`` from its *internal*
parsing copy, but a validator that inspects the parsed result and then
returns the ORIGINAL string unchanged lets those characters straight
through. A value like
``"https://good.example/$arch\\nSigLevel = Never\\n[evil]\\nServer = ..."``
parses as a well-formed URL and is then written into a config file (or
passed as a subprocess/git argument) verbatim, injecting an extra directive
or a whole extra section. The same applies to plain ``startswith``/
``endswith``/``split()``-based checks: none of them scan the whole string
for control characters, so a stray trailing ``\\t`` or an embedded ``\\x7f``
(DEL) can slip past a check that only looks at the prefix/suffix.

Every field that ends up in a file dasik writes, or as an argument dasik
shells out with, must be checked against the RAW string — call
``reject_control_chars`` first, before any ``urlsplit``/``startswith``/
``endswith``/``split`` logic that only inspects part of the value.
"""
import re

# ASCII control characters (0x00-0x1F) plus DEL (0x7F).
_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


def reject_control_chars(value: str, what: str) -> None:
    """Raise ``ValueError`` if ``value`` contains an ASCII control character.

    ``what`` names the field in the error message (e.g. ``"pacman key url"``,
    ``"package source url"``) so a failure is traceable to the exact config
    key that produced it.
    """
    if _CONTROL_CHAR.search(value):
        raise ValueError(
            f"{what} must not contain control characters, got {value!r}; "
            "dasik writes this value verbatim into a config file"
        )
