"""Action: AI agent skills and plugins, installed through each agent's own CLI.

v3 domain ``ai_skills``. What makes this domain different from every other one:

* **dasik does not install anything itself.** It runs ``claude plugin install``,
  ``codex plugin add`` or ``npx skills add`` as the user, because those are the
  commands that also know how to *update* what they installed. Unpacking the
  files by hand would work once and then fight ``claude plugin update`` forever.
* **Presence, never version.** The config names artefacts, not versions, exactly
  like ``packages`` names packages and lets pacman own the versions.
* **The artefacts live in ``$HOME``.** "System-wide" therefore means "for every
  declared human", and each (user, agent, artefact) triple is its own domain
  item so a plan can say precisely which of them is missing.

Item grammar::

    <user>:<agent>:marketplace:<name>
    <user>:<agent>:plugin:<plugin>@<marketplace>
    <user>:<agent>:skill:<name>
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from .ai_skills_state import (carries_skill, claude_state, codex_state,
                              installed_agents, skills_state)
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_DOMAIN = "ai_skills"

# Which agent each plugin method installs for.
_METHOD_AGENT = {"claude-plugin": "claude-code", "codex-plugin": "codex"}

# A marketplace has to exist before a plugin can be installed from it, and a
# plugin has to be gone before its marketplace can be removed — so creates run
# in this order and removals in the reverse one.
_KIND_ORDER = {"marketplace": 0, "plugin": 1, "skill": 2}

_ROOT = "root"


def _field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


class AiSkillsAction(AbstractAction):
    """Converge the AI skills/plugins each user's agents carry."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        block = cfg.get("ai_skills") or {}
        self._block: Any = block
        self._config_users: List[Any] = cfg.get("users") or []
        # Items whose installer failed under `warn-and-continue`. Excluded from
        # managed_keys so the manifest never claims dasik installed something it
        # could not — the next plan then asks for it again.
        self.failed_items: List[str] = []

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "AI Skills"

    @property
    def is_optional(self) -> bool:
        return True

    # -- config ------------------------------------------------------------ #

    @property
    def _entries(self) -> List[Any]:
        return _field(self._block, "entries", []) or []

    @property
    def failure_policy(self) -> str:
        return _field(self._block, "failure_policy", "warn-and-continue") \
            or "warn-and-continue"

    def _users(self) -> List[str]:
        """Whose $HOME receives the artefacts.

        The block's own list when it has one; otherwise every declared user that
        is not root — a plugin in /root is nobody's idea of "system-wide", and
        the agents are interactive tools run by people.
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

    def _passwd(self) -> Dict[str, str]:
        """``{username: home}`` from the TARGET's /etc/passwd."""
        return {user: home for user, (home, _uid) in self._passwd_entries().items()}

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

    def _home_of(self, user: str, homes: Dict[str, str]) -> str:
        """Where the machine says *user* lives, or where useradd would put them.

        The fallback is what makes the domain plannable on a fresh install: the
        whole plan is computed before UsersAction has created anybody.
        """
        return homes.get(user) or f"/home/{user}"

    # -- desired state ------------------------------------------------------ #

    @staticmethod
    def _item(user: str, agent: str, kind: str, value: str) -> str:
        return f"{user}:{agent}:{kind}:{value}"

    def _desired(self) -> Dict[str, Dict[str, Any]]:
        """item -> spec, in install order (marketplace, plugin, skill)."""
        desired: Dict[str, Dict[str, Any]] = {}
        managed_users = self._users()
        for user in managed_users:
            for entry in self._entries:
                # An entry may narrow the block's users, never widen them: the
                # block's list is the boundary of the domain, and an entry that
                # reached outside it would install for somebody whose artefacts
                # the domain would then never remove.
                only = _field(entry, "users", []) or []
                if only and user not in only:
                    continue
                method = _field(entry, "method")
                name = _field(entry, "name")
                if method in _METHOD_AGENT:
                    agent = _METHOD_AGENT[method]
                    market = _field(entry, "marketplace") or {}
                    market_name = _field(market, "name")
                    market_source = _field(market, "source")
                    if market_source:
                        # A marketplace the agent ships with (Codex's
                        # openai-curated) has nothing to register and no source
                        # to drift, so it is not an item at all.
                        desired[self._item(user, agent, "marketplace", market_name)] = {
                            "kind": "marketplace", "user": user, "agent": agent,
                            "method": method, "name": market_name,
                            "source": market_source,
                        }
                    plugin = _field(entry, "plugin") or name
                    desired[self._item(user, agent, "plugin",
                                       f"{plugin}@{market_name}")] = {
                        "kind": "plugin", "user": user, "agent": agent,
                        "method": method, "plugin": plugin,
                        "marketplace": market_name,
                    }
                else:
                    for agent in _field(entry, "agents", []) or []:
                        desired[self._item(user, agent, "skill", name)] = {
                            "kind": "skill", "user": user, "agent": agent,
                            "method": method, "name": name,
                            "source": _field(entry, "source"),
                        }
        return desired

    # -- system reality ----------------------------------------------------- #

    def _scan(self) -> Tuple[set, Dict[str, str]]:
        """(items present on the machine, ``{marketplace item: source}``).

        Only the users this action manages are scanned: a home is not a place to
        go looking, and an agent belonging to somebody the config never mentions
        is none of dasik's business.
        """
        items: set = set()
        markets: Dict[str, str] = {}
        homes = self._passwd()
        for user in self._users():
            home = self._abs(self._home_of(user, homes))

            for agent, reader in (("claude-code", claude_state),
                                  ("codex", codex_state)):
                plugins, sources = reader(home)
                for plugin_id in plugins:
                    items.add(self._item(user, agent, "plugin", plugin_id))
                for market_name, source in sources.items():
                    item = self._item(user, agent, "marketplace", market_name)
                    items.add(item)
                    markets[item] = source

            canonical, per_agent, _sources = skills_state(home)
            # Only the agents this user's entries name: a universal agent reads
            # the canonical directory, so every one of them "has" every skill
            # there, and enumerating all of them would flood the domain with
            # items nobody declared.
            for agent in self._agents_of(user):
                for skill in canonical | set(per_agent.get(agent, set())):
                    if carries_skill(agent, skill, canonical, per_agent):
                        items.add(self._item(user, agent, "skill", skill))
        return items, markets

    def _agents_of(self, user: str) -> set:
        """Agents some entry names for *user* (skills methods only)."""
        return {spec["agent"] for item, spec in self._desired().items()
                if spec["kind"] == "skill" and spec["user"] == user}

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return self._scan()[0]

    # -- v3 contract -------------------------------------------------------- #

    @staticmethod
    def _same_source(declared: Optional[str], recorded: Optional[str]) -> bool:
        """Whether two marketplace sources name the same repository.

        Each CLI records the source in its own spelling — Claude keeps
        ``owner/repo``, codex keeps the URL it cloned, with the ``.git`` the
        config never wrote — so a plain string comparison made an eternal
        MODIFY: apply re-registered the marketplace and the next plan asked
        again, forever. Only the shapes that genuinely mean the same repository
        are folded together; a different host or a different owner still counts
        as different.
        """
        if declared is None or recorded is None:
            return declared == recorded
        return AiSkillsAction._canonical_source(declared) == \
            AiSkillsAction._canonical_source(recorded)

    @staticmethod
    def _canonical_source(source: str) -> str:
        value = source.strip().rstrip("/")
        for scheme in ("https://", "http://", "ssh://"):
            if value.startswith(scheme):
                value = value[len(scheme):]
                break
        else:
            if value.startswith("git@"):
                value = value[len("git@"):].replace(":", "/", 1)
        if value.endswith(".git"):
            value = value[: -len(".git")]
        # A bare `owner/repo` is GitHub shorthand — every one of these CLIs
        # expands it there — so it has to compare equal to the URL it expands to.
        if value.count("/") == 1 and "." not in value.split("/")[0]:
            value = f"github.com/{value}"
        return value.lower()

    @staticmethod
    def _kind(item: str) -> str:
        parts = item.split(":")
        return parts[2] if len(parts) > 2 else ""

    def plan(self, managed) -> List[Change]:
        if self._target() is None:
            return []
        from ..state.set_math import compute_changes

        desired = self._desired()
        actual, market_sources = self._scan()
        changes, _drift = compute_changes(
            _DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        creates = sorted((c for c in changes if c.op is Op.CREATE),
                         key=lambda c: (_KIND_ORDER.get(self._kind(c.item), 9),
                                        c.item))
        deletes = sorted((c for c in changes if c.op is Op.DELETE),
                         key=lambda c: (-_KIND_ORDER.get(self._kind(c.item), 9),
                                        c.item))

        modifies: List[Change] = []
        for item in sorted(set(desired) & actual):
            spec = desired[item]
            if spec["kind"] != "marketplace":
                continue
            # A marketplace registered from somewhere else is not the one the
            # config asked for: the plugin would come from another repository
            # under the same name.
            if not self._same_source(spec["source"], market_sources.get(item)):
                modifies.append(Change(_DOMAIN, Op.MODIFY, item,
                                       reason="source drift"))
        return creates + modifies + deletes

    # -- apply -------------------------------------------------------------- #

    @staticmethod
    def _su_argv(user: str, script: str, *args: str) -> List[str]:
        """``su - <user> -c <script> -- sh <args>``.

        ``--`` terminates util-linux ``su``'s own option parsing before the
        shell's positional argv, and every value travels as ``$1``.. so a name
        with shell metacharacters arrives as inert data instead of code.
        """
        return ["-", user, "-c", script, "--", "sh", *args]

    def _command_for(self, change: Change, spec: Dict[str, Any]
                     ) -> List[Tuple[str, Tuple[str, ...]]]:
        """The official command(s) for one change: (script, args) pairs."""
        kind, agent = spec["kind"], spec["agent"]
        if kind == "marketplace":
            cli = "claude" if agent == "claude-code" else "codex"
            add = (f'{cli} plugin marketplace add "$1"', (spec["source"],))
            remove = (f'{cli} plugin marketplace remove "$1"', (spec["name"],))
            if change.op is Op.DELETE:
                return [remove]
            if change.op is Op.MODIFY:
                # Re-register: `marketplace add` on a name that already exists
                # would keep pointing at the other repository.
                return [remove, add]
            return [add]
        if kind == "plugin":
            plugin_id = f"{spec['plugin']}@{spec['marketplace']}"
            if agent == "claude-code":
                if change.op is Op.DELETE:
                    return [('claude plugin uninstall "$1"', (plugin_id,))]
                # -y accepts the marketplace-declared command without a prompt,
                # which is required when stdin is not a TTY (it never is here).
                return [('claude plugin install "$1" -y --scope user',
                         (plugin_id,))]
            if change.op is Op.DELETE:
                # The full selector, not the bare name: `codex plugin remove`
                # takes PLUGIN@MARKETPLACE and is ambiguous without it when two
                # marketplaces carry the same plugin.
                return [('codex plugin remove "$1"', (plugin_id,))]
            return [('codex plugin add "$1"', (plugin_id,))]
        # A plain skill, through the cross-agent `skills` CLI. Named options
        # only: its remove takes variadic agents AND positional skills, so
        # `--agent a name` would be ambiguous.
        if change.op is Op.DELETE:
            return [('npx -y skills remove --skill "$1" --agent "$2" '
                     '--global --yes', (spec["name"], agent))]
        return [('npx -y skills add "$1" --skill "$2" -g -a "$3" -y',
                 (spec["source"], spec["name"], agent))]

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        # A DELETE is not in the config any more, so its spec comes from the
        # item itself: user, agent, kind and value are all the command needs.
        for change in changes:
            spec = desired.get(change.item) or self._spec_from_item(change.item)
            if spec is None:
                continue
            for script, args in self._command_for(change, spec):
                if not self._run(spec["user"], script, args, change.item):
                    break

    @staticmethod
    def _spec_from_item(item: str) -> Optional[Dict[str, Any]]:
        """Rebuild a spec from an item dasik owns but the config dropped."""
        parts = item.split(":", 3)
        if len(parts) != 4:
            return None
        user, agent, kind, value = parts
        spec: Dict[str, Any] = {"kind": kind, "user": user, "agent": agent}
        if kind == "plugin":
            plugin, _, marketplace = value.partition("@")
            spec.update(plugin=plugin, marketplace=marketplace)
        else:
            spec.update(name=value, source=None)
        return spec

    def _run(self, user: str, script: str, args: Tuple[str, ...],
             item: str) -> bool:
        """Run one installer command. False when it failed (and was tolerated)."""
        result = Command.execute(
            "su", self._su_argv(user, script, *args),
            target=self._target(), check=False, stream=True,
            label=f"ai_skills: {item}")
        if getattr(result, "returncode", 1) == 0:
            return True
        detail = (getattr(result, "stderr", "") or "").strip()
        message = (f"ai_skills: {item} failed. Command: su - {user} -c "
                   f"{script!r} -- sh {' '.join(args)}"
                   + (f"\n{detail}" if detail else ""))
        if self.failure_policy == "abort":
            raise CommandExecutionError(message)
        # warn-and-continue: the rest of the apply is worth more than this one
        # artefact, and disowning the item makes the next plan ask again.
        print(f"\033[31m{message}\033[0m")
        if item not in self.failed_items:
            self.failed_items.append(item)
        return False

    # -- sync ---------------------------------------------------------------- #

    def _sync_users(self) -> List[str]:
        """Whose homes `sync` reads.

        The declared users when there are any, and otherwise the machine's own
        humans — a bootstrap sync starts from ``{}``, where nothing is declared
        yet, and a domain that captured nothing there would be invisible until
        somebody wrote the block by hand. System accounts are never read: their
        homes are service state, not somebody's tools.
        """
        declared = self._users()
        if declared:
            return declared
        return sorted(user for user, (_home, uid) in self._passwd_entries().items()
                      if 1000 <= uid < 65534)

    def import_state(self, managed=None) -> Dict[str, Any]:
        """Report the artefacts each user's agents actually carry."""
        if self._target() is None:
            return {_DOMAIN: {}}

        homes = self._passwd()
        users = self._sync_users()
        # key -> {users}, where the key is everything that makes two artefacts
        # the same declaration.
        found: Dict[Tuple[Any, ...], set] = {}
        skipped: List[str] = []

        for user in users:
            home = self._abs(self._home_of(user, homes))
            for method, reader in (("claude-plugin", claude_state),
                                   ("codex-plugin", codex_state)):
                plugins, sources = reader(home)
                for plugin_id in sorted(plugins):
                    plugin, _, market = plugin_id.partition("@")
                    if not market:
                        # No marketplace to install it from again; it cannot be
                        # expressed as a declaration.
                        skipped.append(f"{user}: {plugin_id}")
                        continue
                    plugin_key: Tuple[Any, ...] = (method, plugin, market,
                                                   sources.get(market))
                    found.setdefault(plugin_key, set()).add(user)

            canonical, per_agent, skill_sources = skills_state(home)
            present = installed_agents(home)
            declared = self._agents_of(user)
            for skill in sorted(canonical | {n for names in per_agent.values()
                                             for n in names}):
                source = skill_sources.get(skill)
                if not source:
                    # Nothing records where it came from, so no other machine
                    # could reproduce it. Reported, never invented.
                    skipped.append(f"{user}: {skill}")
                    continue
                agents = {a for a in (present | declared)
                          if carries_skill(a, skill, canonical, per_agent)}
                if not agents:
                    # The canonical copy is there but no agent on this machine
                    # reads it, and nothing declares one either: naming an agent
                    # would be inventing a machine that does not exist.
                    skipped.append(f"{user}: {skill} (no agent reads it)")
                    continue
                skill_key: Tuple[Any, ...] = ("skills", skill,
                                              tuple(sorted(agents)), source)
                found.setdefault(skill_key, set()).add(user)

        if skipped:
            print("  ai_skills: not captured (no known source): "
                  + ", ".join(sorted(skipped)))
        if not found:
            return {_DOMAIN: {}}

        all_owners = sorted({u for owners in found.values() for u in owners})
        entries: List[Dict[str, Any]] = []
        for key in sorted(found, key=lambda k: (str(k[1]), str(k[0]))):
            owners = sorted(found[key])
            entry: Dict[str, Any]
            if key[0] == "skills":
                entry = {"name": key[1], "method": "skills", "source": key[3],
                         "agents": list(key[2])}
            else:
                marketplace: Dict[str, Any] = {"name": key[2]}
                if key[3]:
                    marketplace["source"] = key[3]
                entry = {"name": key[1], "method": key[0],
                         "marketplace": marketplace}
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

    def managed_keys(self) -> dict:
        """Items this action owns after apply.

        Excludes what an installer failed to produce under `warn-and-continue`,
        so the manifest never claims an artefact dasik could not install.
        """
        failed = set(self.failed_items)
        return {_DOMAIN: [i for i in sorted(self._desired()) if i not in failed]}

    def verify(self) -> bool:
        return not self.plan(managed=[])
