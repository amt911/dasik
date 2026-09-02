"""Read what each AI agent says about its own skills and plugins.

Three programs, three formats, none of them dasik's:

* Claude Code keeps ``~/.claude/plugins/installed_plugins.json`` (keys are
  ``plugin@marketplace``) and ``known_marketplaces.json``.
* Codex keeps TOML sections ``[plugins."<plugin>@<marketplace>"]`` in
  ``~/.codex/config.toml``.
* The cross-agent ``skills`` CLI (npm ``skills``) installs a canonical copy in
  ``~/.agents/skills/<name>`` and links it from each agent's own skills
  directory, recording provenance in ``~/.agents/.skill-lock.json``.

dasik reads these and writes none of them: the official CLI stays the only
writer, which is what keeps ``claude plugin update`` and ``npx skills update``
working. Every reader therefore treats an absent, truncated or unexpected file
as "nothing installed" — a redundant install command is harmless, whereas
guessing that something IS installed would make ``plan`` silent about a skill
that was never there.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Set, Tuple

# Agent id -> home-relative global skills directory. Pinned against the `skills`
# CLI's own registry (vercel-labs/skills, src/agents.ts `globalSkillsDir`), which
# is what decides where `npx skills add -g -a <agent>` puts a skill.
AGENT_SKILL_DIRS: Dict[str, str] = {
    "claude-code": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
    "opencode": ".config/opencode/skills",
}

# The canonical copy every agent links to (src/constants.ts UNIVERSAL_SKILLS_DIR).
CANONICAL_SKILL_DIR = ".agents/skills"
LOCK_REL = ".agents/.skill-lock.json"

# Codex ships these preinstalled under ~/.codex/skills/.system. Nobody installed
# them, and nothing can reinstall them, so they are not part of any domain.
_IGNORED_SKILL_DIRS = {".system"}

_SKILL_FILE = "SKILL.md"

_TOML_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_TOML_PLUGIN_RE = re.compile(r'^plugins\."(?P<id>[^"]+)"$')
_TOML_MARKET_RE = re.compile(r'^plugin_marketplaces\.(?P<name>[A-Za-z0-9._-]+)$')
_TOML_KV_RE = re.compile(r'^\s*(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<value>.+?)\s*$')


def _read_json(path: str):
    """Parsed JSON, or ``None`` when the file is missing or not JSON."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def claude_state(home: str) -> Tuple[Set[str], Dict[str, str]]:
    """(installed ``plugin@marketplace`` ids, ``{marketplace: source}``).

    *home* is an absolute path on the machine being inspected (already resolved
    through ``Target.path``).
    """
    plugins: Set[str] = set()
    installed = _read_json(os.path.join(home, ".claude/plugins/installed_plugins.json"))
    if isinstance(installed, dict):
        entries = installed.get("plugins")
        if isinstance(entries, dict):
            for key, installations in entries.items():
                # The key survives an uninstall with an empty list behind it.
                if isinstance(installations, list) and installations:
                    plugins.add(key)

    markets: Dict[str, str] = {}
    known = _read_json(os.path.join(home, ".claude/plugins/known_marketplaces.json"))
    if isinstance(known, dict):
        for name, entry in known.items():
            source = entry.get("source") if isinstance(entry, dict) else None
            if isinstance(source, dict):
                # `repo` for a GitHub marketplace, `url` / `path` for the rest.
                value = source.get("repo") or source.get("url") or source.get("path")
                if isinstance(value, str) and value:
                    markets[name] = value
    return plugins, markets


def _parse_codex_toml(text: str) -> Tuple[Set[str], Dict[str, str]]:
    """Sections of interest from a Codex ``config.toml``.

    ``tomllib`` is used when available (3.11+); the hand parser below is the
    fallback for 3.10 and, deliberately, the thing that rejects a malformed file
    the same way tomllib does — by returning nothing.
    """
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            return set(), {}
        plugins = {
            key for key, value in (data.get("plugins") or {}).items()
            if isinstance(value, dict) and value.get("enabled", True)
        }
        markets = {
            name: value["source"]
            for name, value in (data.get("plugin_marketplaces") or {}).items()
            if isinstance(value, dict) and isinstance(value.get("source"), str)
        }
        return plugins, markets

    return _parse_codex_toml_lines(text)


def _parse_codex_toml_lines(text: str) -> Tuple[Set[str], Dict[str, str]]:
    """The 3.10 fallback: only the two section shapes this module needs.

    Not a TOML parser — it recognises ``[plugins."<id>"]`` and
    ``[plugin_marketplaces.<name>]`` and gives up entirely on a line that opens
    a section it cannot parse, so a malformed file reads as empty exactly like
    ``tomllib`` would.
    """
    plugins: Set[str] = set()
    markets: Dict[str, str] = {}
    section = None
    balanced = True
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not _TOML_SECTION_RE.match(line):
            balanced = False
            break
        match = _TOML_SECTION_RE.match(line)
        if match:
            section = match.group(1)
            plugin = _TOML_PLUGIN_RE.match(section)
            if plugin:
                plugins.add(plugin.group("id"))
            continue
        pair = _TOML_KV_RE.match(line)
        if not pair or section is None:
            continue
        key, value = pair.group("key"), pair.group("value").strip()
        plugin = _TOML_PLUGIN_RE.match(section)
        if plugin and key == "enabled" and value == "false":
            plugins.discard(plugin.group("id"))
        market = _TOML_MARKET_RE.match(section)
        if market and key == "source":
            markets[market.group("name")] = value.strip('"')
    if not balanced:
        return set(), {}
    return plugins, markets


def codex_state(home: str) -> Tuple[Set[str], Dict[str, str]]:
    """(installed ``plugin@marketplace`` ids, ``{marketplace: source}``)."""
    try:
        with open(os.path.join(home, ".codex/config.toml"), "r",
                  encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return set(), {}
    return _parse_codex_toml(text)


def _skill_dirs(path: str):
    """``(name, full path)`` for every entry of a skills directory."""
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return
    for name in names:
        if name in _IGNORED_SKILL_DIRS or name.startswith("."):
            continue
        full = os.path.join(path, name)
        if os.path.isfile(os.path.join(full, _SKILL_FILE)):
            yield name, full


def skills_state(home: str) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """(``{skill: {agent ids that carry it}}``, ``{skill: source}``).

    A skill counts for an agent whether the entry is a symlink to the canonical
    copy (the CLI's default) or an independent directory (its copy method), so
    both installation methods read back the same.
    """
    agents: Dict[str, Set[str]] = {}
    for agent, relative in AGENT_SKILL_DIRS.items():
        for name, _full in _skill_dirs(os.path.join(home, relative)):
            agents.setdefault(name, set()).add(agent)

    sources: Dict[str, str] = {}
    lock = _read_json(os.path.join(home, LOCK_REL))
    if isinstance(lock, dict):
        for name, entry in (lock.get("skills") or {}).items():
            if not isinstance(entry, dict):
                continue
            source = entry.get("source") or entry.get("sourceUrl")
            # Only for a skill some agent actually carries: a lock entry alone
            # is a record of an install that may since have been removed.
            if isinstance(source, str) and source and name in agents:
                sources[name] = source
    return agents, sources
