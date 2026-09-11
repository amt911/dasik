"""Read a TOML file the way dasik needs it: as a plain dict, or nothing at all.

Two domains read ``~/.codex/config.toml`` — ``ai_skills`` wants its
``[plugins."<id>"]`` and ``[marketplaces.<n>]`` sections, ``mcp_servers`` wants
``[mcp_servers.<n>]`` — so there is ONE parser for it. A second copy is how
``plan`` and ``sync`` start disagreeing about the same machine.

``tomllib`` does the work on 3.11+ (which is every Arch install and every
install ISO). The hand parser below is the 3.10 fallback, and it is deliberately
partial: it understands the value shapes those agents actually write (strings,
booleans, integers, single-line arrays and single-line inline tables) and gives
up on the WHOLE document the moment it meets anything else — exactly what
``tomllib`` does by raising. Half a config is worse than none: it would describe
a machine carrying fewer servers than it really has, and the next apply would
re-register what is already there.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

try:                                    # pragma: no cover - one branch per version
    import tomllib as _TOMLLIB          # type: ignore[import-not-found]
except ImportError:                     # pragma: no cover - 3.10
    _TOMLLIB = None                     # type: ignore[assignment]

_SECTION_RE = re.compile(r'^\[([^\[\]]+)\]$')
_KV_RE = re.compile(r'^(?P<key>[A-Za-z0-9_"\'.-]+)\s*=\s*(?P<value>.+)$')
_INT_RE = re.compile(r'^[+-]?\d+$')


class _Malformed(Exception):
    """Anything the fallback cannot read, which voids the whole document."""


def load_toml(text: str) -> Dict[str, Any]:
    """The document as nested dicts, or ``{}`` when it cannot be parsed."""
    if _TOMLLIB is not None:
        try:
            return _TOMLLIB.loads(text)
        except Exception:
            return {}
    try:
        return _parse(text)
    except _Malformed:
        return {}


# -- the 3.10 fallback ------------------------------------------------------ #

def _parse(text: str) -> Dict[str, Any]:
    document: Dict[str, Any] = {}
    table = document
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            match = _SECTION_RE.match(line)
            if not match:
                raise _Malformed(line)
            table = _descend(document, _split_path(match.group(1)))
            continue
        pair = _KV_RE.match(line)
        if not pair:
            raise _Malformed(line)
        key = _unquote(pair.group("key").strip())
        table[key] = _value(pair.group("value").strip())
    return document


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment, ignoring ``#`` inside a quoted string."""
    out: List[str] = []
    quote = ""
    escaped = False
    for char in line:
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out)


def _split_path(header: str) -> List[str]:
    """``projects."/home/a"`` -> ``['projects', '/home/a']`` (dots inside quotes stay)."""
    parts: List[str] = []
    current: List[str] = []
    quote = ""
    escaped = False
    for char in header:
        if quote:
            if escaped:
                current.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in "\"'":
            quote = char
            continue
        if char == ".":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if quote:
        raise _Malformed(header)
    parts.append("".join(current).strip())
    if not all(parts):
        raise _Malformed(header)
    return parts


def _descend(document: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
    table = document
    for key in path:
        nxt = table.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            table[key] = nxt
        table = nxt
    return table


def _value(text: str) -> Any:
    value, rest = _read_value(text)
    if rest.strip():
        raise _Malformed(text)
    return value


def _read_value(text: str) -> Tuple[Any, str]:
    text = text.lstrip()
    if not text:
        raise _Malformed(text)
    if text[0] in "\"'":
        return _read_string(text)
    if text[0] == "[":
        return _read_array(text)
    if text[0] == "{":
        return _read_inline_table(text)
    literal, _, rest = text.partition(",")
    literal = literal.strip()
    if literal == "true":
        return True, _rest_after(text, literal)
    if literal == "false":
        return False, _rest_after(text, literal)
    if _INT_RE.match(literal):
        return int(literal), _rest_after(text, literal)
    # Floats, dates, multi-line strings: nothing dasik reads writes them, and
    # guessing would be worse than admitting the document is unreadable here.
    raise _Malformed(text)


def _rest_after(text: str, literal: str) -> str:
    return text[len(text) - len(text.lstrip()) + len(literal):]


def _read_string(text: str) -> Tuple[str, str]:
    quote = text[0]
    out: List[str] = []
    escaped = False
    for index, char in enumerate(text[1:], start=1):
        if escaped:
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(char, char))
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char == quote:
            return "".join(out), text[index + 1:]
        out.append(char)
    raise _Malformed(text)


def _read_array(text: str) -> Tuple[List[Any], str]:
    rest = text[1:].lstrip()
    items: List[Any] = []
    while True:
        if not rest:
            raise _Malformed(text)
        if rest[0] == "]":
            return items, rest[1:]
        item, rest = _read_value(rest)
        items.append(item)
        rest = rest.lstrip()
        if rest[:1] == ",":
            rest = rest[1:].lstrip()
        elif rest[:1] != "]":
            raise _Malformed(text)


def _read_inline_table(text: str) -> Tuple[Dict[str, Any], str]:
    rest = text[1:].lstrip()
    table: Dict[str, Any] = {}
    while True:
        if not rest:
            raise _Malformed(text)
        if rest[0] == "}":
            return table, rest[1:]
        key, _, after = rest.partition("=")
        if not _:
            raise _Malformed(text)
        value, rest = _read_value(after)
        table[_unquote(key.strip())] = value
        rest = rest.lstrip()
        if rest[:1] == ",":
            rest = rest[1:].lstrip()
        elif rest[:1] != "}":
            raise _Malformed(text)


def _unquote(key: str) -> str:
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return key[1:-1]
    return key
