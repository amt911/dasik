"""Assemble one config out of several files.

A real config is dominated by two things: a long package list and a handful of
verbatim file bodies (udev rules, PAM snippets, ini fragments) that have to be
JSON-escaped into one line. Both are unreadable inline and both are naturally
separate files, so three directives are enough:

``{"$include": "path.json"}``
    Replaced by the parsed JSON of that file — any value: object, list, string.

``{"$include_text": "path.conf"}``
    Replaced by that file's contents as a string, unparsed. This is what makes
    ``files[].content`` readable: the PAM snippet lives in a real file that an
    editor highlights, not as ``"#%PAM-1.0\\nauth sufficient …"``.

``{"$concat": [ ... ]}``
    The lists inside it, flattened into one. Lets `packages` be split by theme
    (base + desktop + dev) instead of one 172-entry block.

Every path is relative to the file that names it, so a directory of fragments
can be moved as a unit. A directive must be the only key in its object; paths
must be relative and free of ``..``; cycles are reported. Each of those rules
exists because the alternative is a config that loads something its reader
cannot see.

Secrets follow from this for free: ``"hashed_password": {"$include_text":
"secrets/andres.hash"}`` keeps the hash out of the committed config.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Tuple

INCLUDE = "$include"
INCLUDE_TEXT = "$include_text"
CONCAT = "$concat"
_DIRECTIVES = (INCLUDE, INCLUDE_TEXT, CONCAT)


class ConfigIncludeError(Exception):
    """A directive could not be resolved (bad path, missing file, cycle…)."""


def uses_includes(data: Any) -> bool:
    """True when *data* contains any directive, at any depth.

    ``sync`` rewrites the config file it is given; flattening an assembled
    config into one file would silently undo the split, so the caller checks
    this first.
    """
    if isinstance(data, dict):
        if any(k in data for k in _DIRECTIVES):
            return True
        return any(uses_includes(v) for v in data.values())
    if isinstance(data, list):
        return any(uses_includes(v) for v in data)
    return False


def _resolve_path(raw: Any, base_dir: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigIncludeError(
            f"include path must be a non-empty string, got {raw!r}")
    if os.path.isabs(raw):
        raise ConfigIncludeError(
            f"include path {raw!r} must be relative to the config that names it")
    parts = Path(raw).parts
    if ".." in parts:
        raise ConfigIncludeError(
            f"include path {raw!r} must not contain '..' — a config may only "
            "pull in files at or below its own directory")
    return (base_dir / raw)


def _read(path: Path, raw: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigIncludeError(f"included file not found: {raw} ({path})") from None
    except OSError as exc:
        raise ConfigIncludeError(f"cannot read included file {raw}: {exc}") from None


def _resolve(node: Any, base_dir: Path, chain: Tuple[Path, ...]) -> Any:
    if isinstance(node, list):
        return [_resolve(item, base_dir, chain) for item in node]
    if not isinstance(node, dict):
        return node

    present = [d for d in _DIRECTIVES if d in node]
    if not present:
        return {k: _resolve(v, base_dir, chain) for k, v in node.items()}
    if len(present) > 1 or len(node) > 1:
        raise ConfigIncludeError(
            f"{present[0]} must be the only key in its object; found "
            f"{sorted(node)}")

    directive = present[0]
    value = node[directive]

    if directive == CONCAT:
        if not isinstance(value, list):
            raise ConfigIncludeError(f"{CONCAT} takes a list of lists, got {type(value).__name__}")
        out: List[Any] = []
        for member in value:
            resolved = _resolve(member, base_dir, chain)
            if not isinstance(resolved, list):
                raise ConfigIncludeError(
                    f"{CONCAT} members must each be a list; got "
                    f"{type(resolved).__name__}")
            out.extend(resolved)
        return out

    path = _resolve_path(value, base_dir)
    text = _read(path, value)

    if directive == INCLUDE_TEXT:
        return text

    resolved_path = path.resolve()
    if resolved_path in chain:
        loop = " -> ".join(p.name for p in chain + (resolved_path,))
        raise ConfigIncludeError(f"include cycle: {loop}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigIncludeError(f"included file {value} is not valid JSON: {exc}") from None
    # Nested directives resolve against the INCLUDED file's directory, so a
    # fragment plus its own fragments can be moved together.
    return _resolve(data, path.parent, chain + (resolved_path,))


def resolve_includes(data: Any, base_dir: "str | Path") -> Any:
    """Return *data* with every directive replaced. Pure: no I/O beyond reads."""
    return _resolve(data, Path(base_dir), ())
