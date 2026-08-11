"""Validation for the ``/etc/systemd/*.conf`` blocks (oomd, system, user).

These mappings are written verbatim into a file systemd parses, so the boundary
has to reject anything that could forge structure: a newline in a value adds an
arbitrary directive (or a whole section), and a key is a bare directive name —
never an expression.
"""
import re
from typing import Any, Dict, Optional

# systemd directive names are CamelCase words, occasionally with digits
# (`IPv6`), never spaces, '=' or brackets.
_DIRECTIVE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def validate_ini_section(value: Any) -> Optional[Dict[str, str]]:
    """Normalize a `{directive: value}` mapping, rejecting unsafe content."""
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("must be a mapping of systemd directive -> value")
    result: Dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not _DIRECTIVE.fullmatch(key):
            raise ValueError(
                f"{key!r} is not a systemd directive name (letters and digits, "
                "starting with a letter — no spaces, '=' or brackets)"
            )
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            raise ValueError(
                f"value of {key!r} must be a string or a number, got "
                f"{type(raw).__name__}"
            )
        text = str(raw)
        if "\n" in text or "\r" in text:
            raise ValueError(
                f"value of {key!r} may not contain a line break — it would add "
                "a directive nobody declared"
            )
        result[key] = text
    return result
