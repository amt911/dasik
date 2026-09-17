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
from .config_access import field as _field
from .mcp_servers_state import claude_mcp, codex_mcp
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_DOMAIN = "mcp_servers"

_READERS = {"claude-code": claude_mcp, "codex": codex_mcp}

_ROOT = "root"


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
        "headers": dict(_field(entry, "headers", {}) or {}),
        "bearer_token_env_var": _field(entry, "bearer_token_env_var") or None,
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
                                name=_field(entry, "name"))
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

        The auth fields count: each agent stores the one it supports (claude
        keeps `headers`, codex keeps `bearer_token_env_var`), and a change
        nothing compares is a change `plan` can never show — the server would
        go on authenticating with the old key while every run reported
        convergence.
        """
        keys = ("transport", "command", "args", "env", "url", "headers",
                "bearer_token_env_var")
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

    # -- apply -------------------------------------------------------------- #

    @staticmethod
    def _su_argv(user: str, script: str, *args: str) -> List[str]:
        """``su - <user> -c <script> -- sh <args>``.

        ``--`` terminates util-linux ``su``'s own option parsing before the
        shell's positional argv, and every value travels as ``$1``.. so a name,
        a URL or an env value arrives as inert data instead of code.
        """
        return ["-", user, "-c", script, "--", "sh", *args]

    def _add_command(self, spec: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
        """The registration command for one (agent, transport) pair.

        `claude mcp add` defaults to the LOCAL scope — the directory it was run
        from — so `-s user` is what makes the registration belong to the machine
        rather than to whatever `su` happened to cd into.
        """
        claude = spec["agent"] == "claude-code"
        cli = "claude mcp add" if claude else "codex mcp add"
        scope = " -s user" if claude else ""
        args: List[str] = [spec["name"]]
        if spec["transport"] == "http":
            script = f'{cli} "$1"{scope}'
            if claude:
                script += ' --transport http "$2"'
            else:
                script += ' --url "$2"'
            args.append(spec["url"])
            index = 3
            for key, value in sorted((spec.get("headers") or {}).items()):
                # Only claude-code takes headers; the model refuses them for any
                # other agent, so no branch is needed here.
                script += f' -H "${index}"'
                args.append(f"{key}: {value}")
                index += 1
            if spec.get("bearer_token_env_var"):
                script += f' --bearer-token-env-var "${index}"'
                args.append(spec["bearer_token_env_var"])
            return script, tuple(args)

        env_flag = "-e" if claude else "--env"
        script = f'{cli} "$1"{scope}'
        index = 2
        for key, value in sorted((spec.get("env") or {}).items()):
            script += f' {env_flag} "${index}"'
            args.append(f"{key}={value}")
            index += 1
        script += f' -- "${index}"'
        args.append(spec["command"])
        index += 1
        for argument in spec.get("args") or []:
            script += f' "${index}"'
            args.append(argument)
            index += 1
        return script, tuple(args)

    def _remove_command(self, spec: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
        if spec["agent"] == "claude-code":
            return 'claude mcp remove "$1" -s user', (spec["name"],)
        return 'codex mcp remove "$1"', (spec["name"],)

    def _command_for(self, change: Change, spec: Dict[str, Any]
                     ) -> List[Tuple[str, Tuple[str, ...]]]:
        """The official command(s) for one change: (script, args) pairs."""
        if change.op is Op.DELETE:
            return [self._remove_command(spec)]
        if change.op is Op.MODIFY:
            # Re-register: `mcp add` on a name that already exists does not
            # rewrite it, so the old registration would survive untouched.
            return [self._remove_command(spec), self._add_command(spec)]
        return [self._add_command(spec)]

    @staticmethod
    def _spec_from_item(item: str) -> Optional[Dict[str, Any]]:
        """Rebuild what a removal needs from an item the config dropped."""
        parts = item.split(":", 2)
        if len(parts) != 3:
            return None
        user, agent, name = parts
        return {"user": user, "agent": agent, "name": name}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        for change in changes:
            spec = desired.get(change.item) or self._spec_from_item(change.item)
            if spec is None:
                continue
            for script, args in self._command_for(change, spec):
                if not self._run(spec["user"], script, args, change.item):
                    break

    def _run(self, user: str, script: str, args: Tuple[str, ...],
             item: str) -> bool:
        """Run one CLI command. False when it failed (and was tolerated)."""
        result = Command.execute(
            "su", self._su_argv(user, script, *args),
            target=self._target(), check=False, stream=True,
            label=f"mcp_servers: {item}")
        if getattr(result, "returncode", 1) == 0:
            return True
        detail = (getattr(result, "stderr", "") or "").strip()
        message = (f"mcp_servers: {item} failed. Command: su - {user} -c "
                   f"{script!r} -- sh {' '.join(args)}"
                   + (f"\n{detail}" if detail else ""))
        if self.failure_policy == "abort":
            raise CommandExecutionError(message)
        # warn-and-continue: the rest of the apply is worth more than one
        # registration, and disowning the item makes the next plan ask again.
        print(f"\033[31m{message}\033[0m")
        if item not in self.failed_items:
            self.failed_items.append(item)
        return False

    # -- sync ---------------------------------------------------------------- #

    def _sync_users(self) -> List[str]:
        """Whose homes `sync` reads.

        The declared users when there are any, and otherwise the machine's own
        humans — a bootstrap sync starts from ``{}``, where nothing is declared
        yet, and a domain that captured nothing there would stay invisible until
        somebody wrote the block by hand. System accounts are never read: their
        homes are service state, not somebody's tools.
        """
        declared = self._users()
        if declared:
            return declared
        return sorted(user for user, (_home, uid) in self._passwd_entries().items()
                      if 1000 <= uid < 65534)

    def import_state(self, managed=None) -> Dict[str, Any]:
        """Report the MCP servers each user's agents are registered against."""
        if self._target() is None:
            return {_DOMAIN: {}}

        homes = self._passwd()
        users = self._sync_users()
        # Per user first: {user: {spec key: {agents}}}. Merging users BEFORE
        # the agents are known per user is the cross-product bug — one person
        # carrying the server on codex would lend that agent to everybody else
        # who has it on claude, and the captured config would then register it
        # for somebody who never had it.
        per_user: Dict[str, Dict[Tuple[Any, ...], set]] = {}
        for user in users:
            home = self._abs(self._home_of(user, homes))
            for agent, reader in _READERS.items():
                for name, spec in reader(home).items():
                    key: Tuple[Any, ...] = (
                        name, spec["transport"], spec["command"],
                        tuple(spec["args"]), tuple(sorted(spec["env"].items())),
                        spec["url"], tuple(sorted(spec["headers"].items())),
                        spec["bearer_token_env_var"])
                    per_user.setdefault(user, {}).setdefault(key, set()).add(agent)

        # Two users share an entry only when the spec AND the agents match.
        found: Dict[Tuple[Any, ...], set] = {}
        for user, specs in per_user.items():
            for key, agents in specs.items():
                found.setdefault(key + (tuple(sorted(agents)),),
                                 set()).add(user)
        if not found:
            return {_DOMAIN: {}}

        all_owners = sorted({user for owners in found.values()
                             for user in owners})
        entries: List[Dict[str, Any]] = []
        for key in sorted(found, key=lambda k: (str(k[0]), str(k[-1]), str(k[1]))):
            name, transport, command, args, env, url, headers, bearer = key[:8]
            entry_agents: List[str] = list(key[8])
            owners = sorted(found[key])
            entry: Dict[str, Any] = {"name": name}
            if transport == "http":
                entry["url"] = url
                # Each agent stores only the option it supports, so these never
                # collide — and a server both agents carry with different auth
                # stays two entries, which is the only shape the model accepts.
                if headers:
                    entry["headers"] = dict(headers)
                if bearer:
                    entry["bearer_token_env_var"] = bearer
            else:
                entry["command"] = command
                if args:
                    entry["args"] = list(args)
                if env:
                    # Verbatim, like WireGuard's PrivateKey: a capture describes
                    # the machine, and half a value is not reproducible. A
                    # captured config holding an API key here is private.
                    entry["env"] = dict(env)
            entry["agents"] = entry_agents
            if owners != all_owners:
                entry["users"] = owners
            entries.append(entry)

        block: Dict[str, Any] = {"users": all_owners, "entries": entries}
        declared_policy = _field(self._block, "failure_policy")
        if declared_policy and declared_policy != "warn-and-continue":
            # Not something a machine can report: policy is carried over from
            # the config that was applied, or it would be lost on every sync.
            block["failure_policy"] = declared_policy
        return {_DOMAIN: block}
