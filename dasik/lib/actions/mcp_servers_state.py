"""Read which MCP servers each agent is registered against.

Two programs, two formats, neither of them dasik's:

* Claude Code keeps the user-scope servers in ``~/.claude.json`` under the ROOT
  key ``mcpServers``. The same file also holds ``projects.<path>.mcpServers`` —
  the ``local`` scope, which belongs to a working directory and not to the
  machine — and dasik ignores it: declaring a machine cannot mean registering
  somebody's repository config system-wide.
* Codex keeps ``[mcp_servers.<name>]`` in ``~/.codex/config.toml``.

dasik reads these and writes neither: ``claude mcp add`` / ``codex mcp add``
stay the only writers, because both files are the programs' own mutable state
(account material, per-project history, hook hashes) and owning them as files
would delete it.

Both readers normalize to the same dict::

    {"transport": "stdio" | "http",
     "command": str | None, "args": [str], "env": {str: str}, "url": str | None}

so ``plan`` can compare a claude registration, a codex one and a declaration
without caring which file they came from. An absent, truncated or unexpected
file reads as "nothing registered": a redundant registration costs one command,
while inventing a presence would leave ``plan`` silent about a server that was
never there.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .toml_reader import load_toml

_CLAUDE_REL = ".claude.json"
_CODEX_REL = ".codex/config.toml"


def _spec(command: Any, args: Any, env: Any, url: Any) -> Dict[str, Any]:
    """One registration, in the shape every reader and the config share."""
    clean_args = [str(a) for a in args] if isinstance(args, list) else []
    clean_env = {str(k): str(v) for k, v in env.items()} \
        if isinstance(env, dict) else {}
    return {
        "transport": "http" if isinstance(url, str) and url else "stdio",
        "command": command if isinstance(command, str) and command else None,
        "args": clean_args,
        "env": clean_env,
        "url": url if isinstance(url, str) and url else None,
    }


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def claude_mcp(home: str) -> Dict[str, Dict[str, Any]]:
    """``{name: spec}`` for Claude Code's USER scope under *home*."""
    text = _read_text(os.path.join(home, _CLAUDE_REL))
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    found: Dict[str, Dict[str, Any]] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        found[str(name)] = _spec(entry.get("command"), entry.get("args"),
                                 entry.get("env"), entry.get("url"))
    return found


def codex_mcp(home: str) -> Dict[str, Dict[str, Any]]:
    """``{name: spec}`` for codex under *home*."""
    text = _read_text(os.path.join(home, _CODEX_REL))
    if text is None:
        return {}
    servers = load_toml(text).get("mcp_servers")
    if not isinstance(servers, dict):
        return {}
    found: Dict[str, Dict[str, Any]] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        found[str(name)] = _spec(entry.get("command"), entry.get("args"),
                                 entry.get("env"), entry.get("url"))
    return found
