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

# Where a skill actually lands, per agent. Pinned against the `skills` CLI's own
# registry and install logic (vercel-labs/skills, src/agents.ts and
# src/installer.ts `getAgentBaseDir`), and confirmed in a guest:
#
#   an agent whose `skillsDir` is `.agents/skills` is UNIVERSAL — it reads the
#   canonical directory directly and gets NO directory of its own,
#
# which is true of codex, cursor and opencode. Only claude-code among the agents
# dasik knows has a directory of its own. Reading `~/.codex/skills` for codex is
# how the first version of this domain never converged: `npx skills add -a codex`
# reported success, wrote only `~/.agents/skills/<n>`, and the next plan asked
# for the same skill again, forever.
CANONICAL_SKILL_DIR = ".agents/skills"
LOCK_REL = ".agents/.skill-lock.json"

UNIVERSAL_AGENTS = frozenset({"codex", "cursor", "opencode"})

# Agent id -> the skills directory that agent reads on its own. A universal
# agent reads the canonical directory AS WELL, which is where `npx skills add`
# puts things — but not the only place: codex's own skill-installer and
# `graphify install --platform codex` write ~/.codex/skills/<n>, and codex reads
# that too. Checking only one of the two misses half the installers.
AGENT_SKILL_DIRS: Dict[str, str] = {
    "claude-code": ".claude/skills",
    "codex": ".codex/skills",
    "cursor": ".cursor/skills",
    "opencode": ".config/opencode/skills",
}

# How to tell that an agent exists on this machine at all, the same way the
# `skills` CLI does (its `detectInstalled`): the agent's own home directory.
# Used only by `sync`, to avoid reporting that codex carries a skill on a
# machine where codex is not installed.
AGENT_HOME_MARKERS: Dict[str, str] = {
    "claude-code": ".claude",
    "codex": ".codex",
    "cursor": ".cursor",
    "opencode": ".config/opencode",
}

# Codex ships these preinstalled under ~/.codex/skills/.system. Nobody installed
# them, and nothing can reinstall them, so they are not part of any domain.
_IGNORED_SKILL_DIRS = {".system"}

_SKILL_FILE = "SKILL.md"

_TOML_SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
_TOML_PLUGIN_RE = re.compile(r'^plugins\."(?P<id>[^"]+)"$')
# What `codex plugin marketplace add` really writes, measured in a guest:
# [marketplaces.<name>] with source_type/source. `plugin_marketplaces` is
# accepted too, for a codex that ever used that spelling.
_TOML_MARKET_RE = re.compile(
    r'^(?:plugin_)?marketplaces\.(?P<name>[A-Za-z0-9._-]+)$')
_TOML_KV_RE = re.compile(r'^\s*(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<value>.+?)\s*$')


def _read_json(path: str):
    """Parsed JSON, or ``None`` when the file is missing or not JSON."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


_CLAUDE_PLUGINS = ".claude/plugins"


def _payload_present(home: str, plugin_id: str, record: object) -> bool:
    """Are a plugin installation's files actually on the machine?

    The registry outlives them. `installed_plugins.json` and
    `known_marketplaces.json` are backed up (they are small and describe an
    intent); `plugins/cache/` and `plugins/marketplaces/` usually are not, being
    re-downloadable — so a restored ``$HOME`` claims plugins nobody downloaded.
    Claude Code itself only half-notices: with the marketplace clone missing it
    says ``failed to load: cache-miss``, and with the clone present but the
    plugin cache gone it reports ``enabled`` and loads nothing at all.

    A record that names no path is trusted, because there is nothing to check
    against: a redundant install costs one command, while inventing an absence
    would make dasik fight an installation that is really there. A record that
    DOES name one is verified — but against the home this reader was handed, not
    against the recorded string: ``installPath`` is absolute *inside the target*
    (``/home/andres/...``) while an install inspects ``/mnt/home/andres/...``.
    """
    if not isinstance(record, dict):
        return True
    if not isinstance(record.get("installPath"), str):
        return True
    plugin, _, marketplace = plugin_id.partition("@")
    if not marketplace:
        return True
    base = os.path.join(home, _CLAUDE_PLUGINS, "cache", marketplace, plugin)
    version = record.get("version")
    if isinstance(version, str) and version:
        return os.path.isdir(os.path.join(base, version))
    # No version to look for: any version directory means something is there.
    try:
        return any(os.path.isdir(os.path.join(base, name))
                   for name in os.listdir(base))
    except OSError:
        return False


# Why the marketplaces are NOT checked the same way, measured on the real CLI:
# `claude plugin marketplace add <repo>` answers "Marketplace 'x' already on
# disk — declared in user settings" and exits 0 without cloning anything, so a
# missing clone is a change dasik would plan and could not apply — forever.
# Only `remove` + `add` restores it, and `remove` drops that marketplace's
# plugins from the registry along the way. It does not need to: reinstalling
# the PLUGIN re-clones its marketplace, which is what the restored-$HOME case
# (both directories absent) actually needs.


def claude_state(home: str) -> Tuple[Set[str], Dict[str, str]]:
    """(installed ``plugin@marketplace`` ids, ``{marketplace: source}``).

    *home* is an absolute path on the machine being inspected (already resolved
    through ``Target.path``). An entry whose files are gone does not count as
    installed — see ``_payload_present``.
    """
    plugins: Set[str] = set()
    installed = _read_json(os.path.join(home, ".claude/plugins/installed_plugins.json"))
    if isinstance(installed, dict):
        entries = installed.get("plugins")
        if isinstance(entries, dict):
            for key, installations in entries.items():
                # The key survives an uninstall with an empty list behind it.
                if not isinstance(installations, list) or not installations:
                    continue
                if any(_payload_present(home, key, record)
                       for record in installations):
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
        markets = {}
        for section in ("marketplaces", "plugin_marketplaces"):
            for name, value in (data.get(section) or {}).items():
                if isinstance(value, dict) and isinstance(value.get("source"), str):
                    markets[name] = value["source"]
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


def skills_state(home: str) -> Tuple[Set[str], Dict[str, Set[str]], Dict[str, str]]:
    """(canonical skills, ``{non-universal agent: skills}``, ``{skill: source}``).

    The canonical set is what every universal agent reads; the per-agent map is
    for the agents that keep a directory of their own (a symlink to the
    canonical copy, or an independent directory when the CLI's copy method was
    used — both read back the same).
    """
    canonical = {name for name, _full in _skill_dirs(os.path.join(home, CANONICAL_SKILL_DIR))}

    per_agent: Dict[str, Set[str]] = {}
    for agent, relative in AGENT_SKILL_DIRS.items():
        names = {name for name, _full in _skill_dirs(os.path.join(home, relative))}
        if names:
            per_agent[agent] = names

    known = canonical | {n for names in per_agent.values() for n in names}
    sources: Dict[str, str] = {}
    lock = _read_json(os.path.join(home, LOCK_REL))
    if isinstance(lock, dict):
        for name, entry in (lock.get("skills") or {}).items():
            if not isinstance(entry, dict):
                continue
            source = entry.get("source") or entry.get("sourceUrl")
            # Only for a skill that is actually there: a lock entry alone is the
            # record of an install that may since have been removed.
            if isinstance(source, str) and source and name in known:
                sources[name] = source
    return canonical, per_agent, sources


def installed_agents(home: str) -> Set[str]:
    """The agents this machine actually has, by the CLI's own detection rule."""
    return {agent for agent, marker in AGENT_HOME_MARKERS.items()
            if os.path.isdir(os.path.join(home, marker))}


def carries_skill(agent: str, name: str, canonical: Set[str],
                  per_agent: Dict[str, Set[str]]) -> bool:
    """Whether *agent* would find skill *name* on a machine in this state.

    A universal agent reads the canonical directory, so the canonical copy IS
    the installation. An agent dasik does not know is assumed universal, which
    is the CLI's default shape; `check` warns about it separately.
    """
    if name in per_agent.get(agent, set()):
        return True
    # A universal agent reads the canonical directory too. An agent dasik does
    # not know is assumed universal, which is the CLI's default shape; `check`
    # warns about it separately.
    universal = agent in UNIVERSAL_AGENTS or agent not in AGENT_SKILL_DIRS
    return universal and name in canonical
