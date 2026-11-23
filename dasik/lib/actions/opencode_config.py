"""Read and write opencode's global config (``~/.config/opencode``).

opencode loads BOTH ``opencode.json`` and ``opencode.jsonc`` (json first, jsonc
second, so jsonc wins a clash), accepts MCP servers flat (``mcp.<name>``) or
nested (``mcp.servers.<name>``), and plugins under the legacy ``plugin`` key or
the new ``plugins`` one. This module is the single reader/writer of that
config; it is pure file IO, with no subprocess.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

JSON_REL = ".config/opencode/opencode.json"
JSONC_REL = ".config/opencode/opencode.jsonc"
SCHEMA_URL = "https://opencode.ai/config.json"

_READ_ORDER = (JSON_REL, JSONC_REL)  # later wins, as in opencode


class OpencodeConfigError(Exception):
    """opencode's config cannot be safely read or written."""


@dataclass(frozen=True)
class Found:
    rel: str
    nested: bool
    entry: Any


def _strip_comments(text: str) -> str:
    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated /* comment")
            i = end + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    out: List[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """Parse JSON with comments and trailing commas; ValueError if unparseable."""
    return json.loads(_strip_trailing_commas(_strip_comments(text)))


def _load(home: str, rel: str) -> Any:
    """The parsed file, or None when missing or unparseable."""
    try:
        with open(os.path.join(home, rel), encoding="utf-8") as f:
            return parse_jsonc(f.read())
    except (OSError, ValueError):
        return None


def read_mcp(home: str) -> Dict[str, Found]:
    found: Dict[str, Found] = {}
    for rel in _READ_ORDER:
        data = _load(home, rel)
        mcp = data.get("mcp") if isinstance(data, dict) else None
        if not isinstance(mcp, dict):
            continue
        for name, entry in mcp.items():
            if name == "servers" and isinstance(entry, dict):
                continue
            found[name] = Found(rel, False, entry)
        servers = mcp.get("servers")
        if isinstance(servers, dict):
            for name, entry in servers.items():
                found[name] = Found(rel, True, entry)
    return found


def read_plugins(home: str) -> Dict[str, Found]:
    found: Dict[str, Found] = {}
    for rel in _READ_ORDER:
        data = _load(home, rel)
        if not isinstance(data, dict):
            continue
        for key, nested in (("plugin", False), ("plugins", True)):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    found[item] = Found(rel, nested, item)
    return found


def _components(home: str, rel: str) -> List[str]:
    """Every path from the first directory under home down to the file."""
    parts = rel.split("/")
    return [os.path.join(home, *parts[: i + 1]) for i in range(len(parts))]


def _check_chain(home: str, rel: str) -> List[str]:
    """Refuse links and non-directories on the way; return missing dirs."""
    chain = _components(home, rel)
    missing: List[str] = []
    for path in chain[:-1]:
        if os.path.islink(path):
            raise OpencodeConfigError(
                f"cannot edit {rel}: {path} is a symlink, and dasik will not "
                "write into a directory somebody redirected."
            )
        if os.path.exists(path):
            if not os.path.isdir(path):
                raise OpencodeConfigError(
                    f"cannot edit {rel}: {path} exists and is not a directory."
                )
        else:
            missing.append(path)
    if os.path.islink(chain[-1]):
        raise OpencodeConfigError(
            f"cannot edit {rel}: it is a symlink, and dasik will not write "
            "through it."
        )
    return missing


def edit(
    home: str, rel: str, mutate: Callable[[dict], None], uid: int, gid: int
) -> None:
    """Load ``home/rel``, apply ``mutate`` and write it back atomically.

    Missing directories and a new file are created owned by (uid, gid); a new
    file starts as ``{"$schema": ...}`` at 0644, an existing one keeps its mode.
    """
    path = os.path.join(home, rel)
    missing = _check_chain(home, rel)

    mode = 0o644
    data: Any
    if os.path.exists(path):
        mode = os.stat(path).st_mode & 0o7777
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            data = parse_jsonc(text) if text.strip() else {}
        except (OSError, ValueError) as exc:
            raise OpencodeConfigError(f"cannot parse {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise OpencodeConfigError(f"{path}: top level is not an object")
    else:
        data = {"$schema": SCHEMA_URL}

    mutate(data)

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    for created in missing:
        os.chown(created, uid, gid)

    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".opencode-", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.chown(path, uid, gid)
