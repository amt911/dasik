"""Action: MCP servers, registered through each agent's own CLI.

v3 domain ``mcp_servers``. It is `ai_skills`' sibling and shares its shape for
the same reasons:

* **dasik does not write either registry.** ``~/.claude.json`` and
  ``~/.codex/config.toml`` are the programs' own mutable state — account
  material, per-project history, hook hashes — so the official
  ``claude mcp add`` / ``codex mcp add`` stays the only writer and dasik only
  reads them to decide.
* **The registrations live in ``$HOME``.** "System-wide" therefore means "for
  every declared human", and each (user, agent, server) triple is its own domain
  item so a plan can say precisely which of them is missing.
* **Presence, never version.** The config names the command or the URL; what
  ``uvx``/``npx`` fetch is theirs to own, like pacman owns a package's version.

Item grammar::

    <user>:<agent>:<name>
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from .mcp_servers_state import claude_mcp, codex_mcp
from ..state.change import Change, Op

_DOMAIN = "mcp_servers"

_READERS = {"claude-code": claude_mcp, "codex": codex_mcp}

_ROOT = "root"


def _field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _spec_of(entry: Any) -> Dict[str, Any]:
    """A declaration in the same normalized shape the state readers produce.

    Absent and empty are the same thing on both sides (``args`` unset vs ``[]``,
    ``env`` unset vs ``{}``): `claude mcp add` writes ``"env": {}`` for a server
    the config never gave an environment, and comparing those as different is
    how a domain plans the same MODIFY forever.
    """
    url = _field(entry, "url")
    return {
        "transport": "http" if url else "stdio",
        "command": _field(entry, "command") or None,
        "args": list(_field(entry, "args", []) or []),
        "env": dict(_field(entry, "env", {}) or {}),
        "url": url or None,
    }


class McpServersAction(AbstractAction):
    """Converge the MCP servers each user's agents are registered against."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._block: Any = cfg.get("mcp_servers") or {}
        self._config_users: List[Any] = cfg.get("users") or []
        # Items whose CLI failed under `warn-and-continue`. Excluded from
        # managed_keys so the manifest never claims dasik registered something
        # it could not — the next plan then asks for it again.
        self.failed_items: List[str] = []

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "MCP Servers"

    @property
    def is_optional(self) -> bool:
        return True

    # -- config ------------------------------------------------------------- #

    @property
    def _entries(self) -> List[Any]:
        return _field(self._block, "entries", []) or []

    @property
    def failure_policy(self) -> str:
        return _field(self._block, "failure_policy", "warn-and-continue") \
            or "warn-and-continue"

    def _users(self) -> List[str]:
        """Whose agents get them: the block's list, else every declared human.

        Not root: an MCP server is a tool a person drives from an interactive
        agent, and /root is nobody's idea of "system-wide".
        """
        named = _field(self._block, "users", []) or []
        if named:
            return sorted(named)
        return sorted({_field(u, "username") for u in self._config_users
                       if _field(u, "username") and _field(u, "username") != _ROOT})

    # -- target / passwd ---------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else "/mnt" + canonical

    def _passwd_entries(self) -> Dict[str, Tuple[str, int]]:
        """``{username: (home, uid)}`` from the TARGET's /etc/passwd."""
        entries: Dict[str, Tuple[str, int]] = {}
        try:
            with open(self._abs("/etc/passwd"), "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return entries
        for line in lines:
            parts = line.rstrip("\n").split(":")
            if len(parts) < 6:
                continue
            try:
                entries[parts[0]] = (parts[5], int(parts[2]))
            except ValueError:
                continue
        return entries

    def _passwd(self) -> Dict[str, str]:
        return {user: home for user, (home, _uid) in self._passwd_entries().items()}

    def _home_of(self, user: str, homes: Dict[str, str]) -> str:
        """Where the machine says *user* lives, or where useradd would put them.

        The fallback is what makes the domain plannable on a fresh install: the
        whole plan is computed before UsersAction has created anybody.
        """
        return homes.get(user) or f"/home/{user}"

    # -- desired state ------------------------------------------------------ #

    @staticmethod
    def _item(user: str, agent: str, name: str) -> str:
        return f"{user}:{agent}:{name}"

    def _desired(self) -> Dict[str, Dict[str, Any]]:
        """item -> spec (normalized), for every declared triple."""
        desired: Dict[str, Dict[str, Any]] = {}
        for user in self._users():
            for entry in self._entries:
                # An entry may narrow the block's users, never widen them: the
                # block's list is the boundary of the domain, and an entry
                # reaching outside it would register for somebody whose servers
                # the domain would then never remove.
                only = _field(entry, "users", []) or []
                if only and user not in only:
                    continue
                for agent in _field(entry, "agents", []) or []:
                    spec = _spec_of(entry)
                    spec.update(user=user, agent=agent,
                                name=_field(entry, "name"),
                                headers=dict(_field(entry, "headers", {}) or {}),
                                bearer_token_env_var=_field(
                                    entry, "bearer_token_env_var"))
                    desired[self._item(user, agent, spec["name"])] = spec
        return desired

    # -- system reality ----------------------------------------------------- #

    def _scan(self) -> Dict[str, Dict[str, Any]]:
        """``{item: spec}`` for every server the managed users' agents carry.

        Only the users this action manages are scanned: an agent belonging to
        somebody the config never mentions is none of dasik's business.
        """
        found: Dict[str, Dict[str, Any]] = {}
        homes = self._passwd()
        for user in self._users():
            home = self._abs(self._home_of(user, homes))
            for agent, reader in _READERS.items():
                for name, spec in reader(home).items():
                    found[self._item(user, agent, name)] = spec
        return found

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return set(self._scan())

    # -- v3 contract -------------------------------------------------------- #

    @staticmethod
    def _same_registration(declared: Dict[str, Any],
                           registered: Dict[str, Any]) -> bool:
        """Whether what is registered is what the config asked for.

        Headers and the bearer variable are deliberately NOT compared: neither
        CLI writes them anywhere dasik can read (claude keeps headers, codex
        keeps the variable name, and neither is exposed the same way), so a
        comparison would either be a MODIFY on every run or a lie.
        """
        keys = ("transport", "command", "args", "env", "url")
        return all(declared.get(k) == registered.get(k) for k in keys)

    def plan(self, managed) -> List[Change]:
        if self._target() is None:
            return []
        from ..state.set_math import compute_changes

        desired = self._desired()
        actual = self._scan()
        changes, _drift = compute_changes(
            _DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=set(actual),
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        creates = sorted((c for c in changes if c.op is Op.CREATE),
                         key=lambda c: c.item)
        deletes = sorted((c for c in changes if c.op is Op.DELETE),
                         key=lambda c: c.item)
        modifies = [
            Change(_DOMAIN, Op.MODIFY, item, reason="registration drift")
            for item in sorted(set(desired) & set(actual))
            if not self._same_registration(desired[item], actual[item])
        ]
        return creates + modifies + deletes

    def managed_keys(self) -> dict:
        """Items this action owns after apply, minus what its CLI could not do."""
        failed = set(self.failed_items)
        return {_DOMAIN: [i for i in sorted(self._desired()) if i not in failed]}

    def verify(self) -> bool:
        return not self.plan(managed=[])

    def import_state(self, managed=None) -> Dict[str, Any]:
        raise NotImplementedError    # Task 5

    def apply(self, changes) -> None:
        raise NotImplementedError    # Task 4
