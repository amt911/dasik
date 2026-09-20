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
from .config_access import field as _field
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

# uv records the interpreter it built a tool's environment with in the venv's
# own `pyvenv.cfg`: `version_info = 3.13.7`. Read from the target's filesystem,
# so the check costs no process and works against /mnt during an install.
_VERSION_INFO_RE = re.compile(r"^\s*version_info\s*=\s*(\d+\.\d+)")


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
    def _tools(self) -> List[Any]:
        """The declarations verbatim: a string, or {name, python}."""
        return _field(self._block, "tools", []) or []

    @staticmethod
    def _split(declaration: Any) -> Tuple[str, Optional[str]]:
        """(what uv is handed, the pinned interpreter or None)."""
        if isinstance(declaration, str):
            return declaration, None
        name = _field(declaration, "name") or ""
        return name, _field(declaration, "python")

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

        The name is also NORMALISED the way uv normalises it (PEP 503: lower
        case, every run of `-`, `_` or `.` collapsed to a single `-`). Measured
        on the tower: `uv tool install inkscape_mcp` creates the directory
        `inkscape-mcp`, so comparing the declaration verbatim would never match
        and the tool would be proposed for installation on every run.
        """
        match = _DIST_RE.match(declaration)
        name = match.group(0) if match else declaration
        return re.sub(r"[-_.]+", "-", name).lower()

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

    def _desired(self) -> Dict[str, Tuple[str, Optional[str]]]:
        """item -> (the declaration to hand `uv` verbatim, the pin or None)."""
        desired: Dict[str, Tuple[str, Optional[str]]] = {}
        for user in self._users():
            for declaration in self._tools:
                spec, python = self._split(declaration)
                desired[self._item(user, self.distribution(spec))] = (spec, python)
        return desired

    def _venv_python(self, user: str, distribution: str) -> Optional[str]:
        """`major.minor` uv built this tool's environment with, or None."""
        path = os.path.join(self._abs(self._tool_dir(user)), distribution,
                            "pyvenv.cfg")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return None
        for line in lines:
            match = _VERSION_INFO_RE.match(line)
            if match:
                return match.group(1)
        return None

    def _default_python(self) -> Optional[str]:
        """`major.minor` of the target's own `python3` — what uv would pick.

        Used only by the capture: a tool sitting on the default needs no pin
        recorded, and one sitting off it does, or re-applying the capture puts
        it back on the interpreter it cannot live on.
        """
        try:
            link = os.readlink(self._abs("/usr/bin/python3"))
        except OSError:
            return None
        match = re.search(r"(\d+\.\d+)", os.path.basename(link))
        return match.group(1) if match else None

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

        desired = self._desired()
        actual = self.actual()
        changes, _drift = compute_changes(
            _DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=actual,
        )
        # A pin is desired state, not install-time decoration: a tool uv built
        # on the wrong interpreter is installed and broken, and a silent plan
        # would make the declaration a comment.
        for item in sorted(set(desired) & actual):
            _spec, python = desired[item]
            if not python:
                continue          # no pin declared, no opinion
            user, _, distribution = item.partition(":")
            built_with = self._venv_python(user, distribution)
            if built_with is not None and built_with != python:
                changes.append(Change(
                    _DOMAIN, Op.MODIFY, item,
                    reason=f"built with python {built_with}, declared {python}"))
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
            if change.op in (Op.INSTALL, Op.MODIFY):
                # The DECLARATION goes to uv, not the directory name: the pin
                # and the extras are the whole point of writing them.
                spec, python = desired.get(change.item, (distribution, None))
                if python and change.op is Op.MODIFY:
                    # An environment already exists on the wrong interpreter;
                    # uv leaves it alone unless told to replace it.
                    self._run(user,
                              'uv tool install --force --python "$1" "$2"',
                              (python, spec), change.item)
                elif python:
                    self._run(user, 'uv tool install --python "$1" "$2"',
                              (python, spec), change.item)
                else:
                    self._run(user, 'uv tool install "$1"', (spec,), change.item)
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

        declared: Dict[str, Any] = {}
        for declaration in self._tools:
            spec, _python = self._split(declaration)
            declared[self.distribution(spec)] = declaration
        default = self._default_python()
        tools = sorted({d for names in found.values() for d in names})
        block: Dict[str, Any] = {
            "users": sorted(found),
            # Keep the declaration (pin, extras) for a tool that is declared;
            # a discovered one can only be named — except for the interpreter,
            # which the venv records and which a re-apply would otherwise get
            # wrong.
            "tools": [self._captured(d, declared, found, default) for d in tools],
        }
        policy = _field(self._block, "failure_policy")
        if policy and policy != "warn-and-continue":
            block["failure_policy"] = policy
        return {_DOMAIN: block}

    def _captured(self, distribution: str, declared: Dict[str, Any],
                  found: Dict[str, List[str]], default: Optional[str]) -> Any:
        """How one installed tool is written back.

        A tool uv built on the machine's own `python3` needs no pin: the same
        uv would pick it again. One built on anything else keeps its version,
        whether or not the config it came from said so — that is the difference
        between a capture that re-applies and one that quietly moves the tool
        back to the interpreter it cannot live on.
        """
        entry = declared.get(distribution, distribution)
        for user, names in sorted(found.items()):
            if distribution not in names:
                continue
            built_with = self._venv_python(user, distribution)
            if built_with and built_with != default:
                name, _python = self._split(entry)
                return {"name": name or distribution, "python": built_with}
            break
        return entry

    def verify(self) -> bool:
        return not self.plan(managed=[])
