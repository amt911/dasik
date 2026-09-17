"""Read which MCP servers each agent is registered against.

Three programs, three formats, none of them dasik's:

* Claude Code keeps the user-scope servers in ``~/.claude.json`` under the ROOT
  key ``mcpServers``. The same file also holds ``projects.<path>.mcpServers`` —
  the ``local`` scope, which belongs to a working directory and not to the
  machine — and dasik ignores it: declaring a machine cannot mean registering
  somebody's repository config system-wide.
* Codex keeps ``[mcp_servers.<name>]`` in ``~/.codex/config.toml``.
* Antigravity keeps ``mcpServers`` in ``~/.gemini/config/mcp_config.json``, the
  user-level file its IDE and its CLI (``agy``) share.

dasik reads these and writes none: ``claude mcp add`` / ``codex mcp add`` /
``agy mcp add`` stay the only writers, because these files are the programs' own mutable state
(account material, per-project history, hook hashes) and owning them as files
would delete it.

All readers normalize to the same dict::

    {"transport": "stdio" | "http",
     "command": str | None, "args": [str], "env": {str: str}, "url": str | None,
     "headers": {str: str}, "bearer_token_env_var": str | None}

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
_ANTIGRAVITY_REL = ".gemini/config/mcp_config.json"


def _spec(command: Any, args: Any, env: Any, url: Any,
          headers: Any = None, bearer: Any = None) -> Dict[str, Any]:
    """One registration, in the shape every reader and the config share.

    The auth fields are read too, not only the transport: a header the config
    declares and the reader ignores is a change nothing can detect — `plan`
    would stay silent while the server kept authenticating with the old one.
    Each agent stores only the one it supports, so the other is empty here.
    """
    clean_args = [str(a) for a in args] if isinstance(args, list) else []
    clean_env = {str(k): str(v) for k, v in env.items()} \
        if isinstance(env, dict) else {}
    clean_headers = {str(k): str(v) for k, v in headers.items()} \
        if isinstance(headers, dict) else {}
    return {
        "transport": "http" if isinstance(url, str) and url else "stdio",
        "command": command if isinstance(command, str) and command else None,
        "args": clean_args,
        "env": clean_env,
        "url": url if isinstance(url, str) and url else None,
        "headers": clean_headers,
        "bearer_token_env_var": bearer if isinstance(bearer, str) and bearer
        else None,
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
                                 entry.get("env"), entry.get("url"),
                                 headers=entry.get("headers"))
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
                                 entry.get("env"), entry.get("url"),
                                 bearer=entry.get("bearer_token_env_var"))
    return found


def antigravity_mcp(home: str) -> Dict[str, Dict[str, Any]]:
    """``{name: spec}`` for Antigravity (``agy``) under *home*.

    ``agy mcp add`` writes ``~/.gemini/config/mcp_config.json``: stdio servers
    as ``command``/``args``/``env``, http ones as ``serverUrl``/``headers``,
    each with a ``disabled`` flag (FACT-AGY-1). A disabled server is reported as
    absent: the agent does not use it, so a declaration asking for it is not
    converged — and ``agy mcp add`` re-enables it, since a re-add replaces the
    whole registration.
    """
    text = _read_text(os.path.join(home, _ANTIGRAVITY_REL))
    if not text:
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
        if not isinstance(entry, dict) or entry.get("disabled") is True:
            continue
        found[str(name)] = _spec(entry.get("command"), entry.get("args"),
                                 entry.get("env"), entry.get("serverUrl"),
                                 headers=entry.get("headers"))
    return found
