"""Action: Python programs installed per user with ``uv tool``.

v3 domain ``uv_tools``. It exists because some upstreams ship a Python program
and tell you, in their own documentation, to install it into an isolated
per-user environment — graphify recommends ``uv tool install graphifyy`` and
does not package for Arch, and the AUR build of it pulls 26 tree-sitter
grammars that are in no official repository.

Two details decide how this is written:

* **Presence is read from uv's own directory**, ``~/.local/share/uv/tools/<dist>``
  — not from a command on ``PATH``. A stock Arch ``/etc/profile`` puts only
  ``/usr/local/bin`` on the path of a login shell, so asking "is `graphify`
  there?" answers no on a machine that has it perfectly well.
* **The directory is named after the distribution**, so a declaration carrying
  extras or a version pin (``semgrep[all]``, ``graphifyy==0.9.53``) has to be
  reduced to that name before it can be compared — while the declaration itself
  reaches ``uv`` verbatim.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_DOMAIN = "uv_tools"
_ROOT = "root"

# Where `uv tool install` puts things, one directory per distribution. uv honours
# XDG_DATA_HOME/UV_TOOL_DIR, but dasik reads the target's filesystem, where the
# default is what a fresh machine has.
_UV_TOOL_DIR = ".local/share/uv/tools"

# `semgrep[all]==1.2.3` -> `semgrep`: the name uv gives the directory.
_DIST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*")


def _field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


class UvToolsAction(AbstractAction):
    """Converge the uv-installed programs each user has."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._block: Any = cfg.get("uv_tools") or {}
        self._config_users: List[Any] = cfg.get("users") or []
        # Tools whose install failed under `warn-and-continue`; excluded from
        # managed_keys so the manifest never claims one dasik could not install.
        self.failed_tools: List[str] = []

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "uv tools"

    @property
    def is_optional(self) -> bool:
        return True

    # -- config ------------------------------------------------------------- #

    @property
    def _tools(self) -> List[str]:
        return _field(self._block, "tools", []) or []

    @property
    def failure_policy(self) -> str:
        return _field(self._block, "failure_policy", "warn-and-continue") \
            or "warn-and-continue"

    def _users(self) -> List[str]:
        named = _field(self._block, "users", []) or []
        if named:
            return sorted(named)
        return sorted({_field(u, "username") for u in self._config_users
                       if _field(u, "username") and _field(u, "username") != _ROOT})

    @staticmethod
    def distribution(declaration: str) -> str:
        """The name uv gives the directory for *declaration*.

        ``semgrep[all]==1.2.3`` and ``semgrep`` are the same installed tool; only
        the former says anything about which version.
        """
        match = _DIST_RE.match(declaration)
        return match.group(0) if match else declaration

    # -- target ------------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else "/mnt" + canonical

    def _passwd_entries(self) -> Dict[str, Tuple[str, int]]:
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

    def _home_of(self, user: str) -> str:
        entry = self._passwd_entries().get(user)
        return entry[0] if entry else f"/home/{user}"

    def _tool_dir(self, user: str) -> str:
        return f"{self._home_of(user).rstrip('/')}/{_UV_TOOL_DIR}"

    # -- state -------------------------------------------------------------- #

    @staticmethod
    def _item(user: str, distribution: str) -> str:
        return f"{user}:{distribution}"

    def _installed_for(self, user: str) -> List[str]:
        try:
            return sorted(name for name in os.listdir(self._abs(self._tool_dir(user)))
                          if os.path.isdir(os.path.join(
                              self._abs(self._tool_dir(user)), name)))
        except OSError:
            return []

    def _desired(self) -> Dict[str, str]:
        """item -> the declaration to hand `uv`, verbatim."""
        desired: Dict[str, str] = {}
        for user in self._users():
            for declaration in self._tools:
                desired[self._item(user, self.distribution(declaration))] = \
                    declaration
        return desired

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {self._item(user, dist) for user in self._users()
                for dist in self._installed_for(user)}

    # -- v3 contract -------------------------------------------------------- #

    def plan(self, managed) -> List[Change]:
        if self._target() is None:
            return []
        from ..state.set_math import compute_changes

        changes, _drift = compute_changes(
            _DOMAIN,
            desired=list(self._desired().keys()),
            managed=managed,
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        failed = set(self.failed_tools)
        return {_DOMAIN: [i for i in sorted(self._desired()) if i not in failed]}

    # -- apply -------------------------------------------------------------- #

    @staticmethod
    def _su_argv(user: str, script: str, *args: str) -> List[str]:
        """``su - <user> -c <script> -- sh <args>``; values are $1.., never
        interpolated into *script*."""
        return ["-", user, "-c", script, "--", "sh", *args]

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        for change in changes:
            user, _, distribution = change.item.partition(":")
            if change.op is Op.INSTALL:
                # The DECLARATION goes to uv, not the directory name: the pin
                # and the extras are the whole point of writing them.
                self._run(user, 'uv tool install "$1"',
                          (desired.get(change.item, distribution),), change.item)
            elif change.op is Op.REMOVE:
                self._run(user, 'uv tool uninstall "$1"', (distribution,),
                          change.item)

    def _run(self, user: str, script: str, args: Tuple[str, ...],
             item: str) -> None:
        result = Command.execute(
            "su", self._su_argv(user, script, *args),
            target=self._target(), check=False, stream=True,
            label=f"uv_tools: {item}")
        if getattr(result, "returncode", 1) == 0:
            return
        detail = (getattr(result, "stderr", "") or "").strip()
        message = (f"uv_tools: {item} failed. Command: su - {user} -c "
                   f"{script!r} -- sh {' '.join(args)}"
                   + (f"\n{detail}" if detail else ""))
        if self.failure_policy == "abort":
            raise CommandExecutionError(message)
        print(f"\033[31m{message}\033[0m")
        if item not in self.failed_tools:
            self.failed_tools.append(item)

    # -- sync --------------------------------------------------------------- #

    def _sync_users(self) -> List[str]:
        declared = self._users()
        if declared:
            return declared
        return sorted(user for user, (_home, uid) in self._passwd_entries().items()
                      if 1000 <= uid < 65534)

    def import_state(self, managed=None) -> Dict[str, Any]:
        """Report the tools uv actually has, per user.

        The declaration's extras and pin cannot be recovered from the directory —
        uv names it after the distribution alone — so a captured config carries
        the plain names. That still re-plans to nothing, because the plan
        compares distributions.
        """
        if self._target() is None:
            return {_DOMAIN: {}}

        users = self._sync_users()
        found: Dict[str, List[str]] = {}
        for user in users:
            installed = self._installed_for(user)
            if installed:
                found[user] = installed
        if not found:
            return {_DOMAIN: {}}

        declared = {self.distribution(t): t for t in self._tools}
        tools = sorted({d for names in found.values() for d in names})
        block: Dict[str, Any] = {
            "users": sorted(found),
            # Keep the declaration (pin, extras) for a tool that is declared;
            # a discovered one can only be named.
            "tools": [declared.get(d, d) for d in tools],
        }
        policy = _field(self._block, "failure_policy")
        if policy and policy != "warn-and-continue":
            block["failure_policy"] = policy
        return {_DOMAIN: block}

    def verify(self) -> bool:
        return not self.plan(managed=[])
