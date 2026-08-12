"""Action: write declarative files inside a user's ``$HOME``.

v3 domain ``home_files``. The /etc counterpart is :class:`DropFilesAction`; two
things make a home file different:

* **The machine says where the home is.** The config declares
  ``{user, path-relative-to-home}`` and the absolute path comes from the
  target's own ``/etc/passwd``. On a fresh install the user does not exist yet
  (the whole plan is computed before anything is applied), so the plan falls
  back to ``/home/<user>`` — what ``useradd`` would choose — and apply resolves
  it for real.
* **Ownership is part of the desired state.** A file root writes into ``$HOME``
  stays ``root:root``, and the desktop application that has to rewrite it
  cannot. So a file whose content is right but whose owner is wrong is a
  MODIFY, and every directory apply had to create is chowned too.

``sync`` never scans a home directory — that would capture gigabytes and every
secret in it. It reports only what the config declares or the manifest owns.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_DOMAIN = "home_files"


class HomeFilesAction(AbstractAction):
    """Manage files under users' home directories."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._entries: List[Any] = cfg.get("home_files", []) or []

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "Home Files"

    @property
    def is_optional(self) -> bool:
        return True

    # -- target / passwd ------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _passwd(self) -> Dict[str, Tuple[str, int, int]]:
        """``{username: (home, uid, gid)}`` from the TARGET's /etc/passwd."""
        out: Dict[str, Tuple[str, int, int]] = {}
        try:
            with open(self._abs("/etc/passwd"), "r") as f:
                lines = f.readlines()
        except OSError:
            return out
        for line in lines:
            parts = line.rstrip("\n").split(":")
            if len(parts) < 6:
                continue
            try:
                out[parts[0]] = (parts[5], int(parts[2]), int(parts[3]))
            except ValueError:
                continue
        return out

    def _home_of(self, user: str, passwd: Dict[str, Tuple[str, int, int]]) -> str:
        """Where the machine says *user* lives, or where useradd would put them.

        The fallback is what makes this domain plannable on a fresh install:
        `plan` runs before UsersAction has created anybody.
        """
        entry = passwd.get(user)
        return entry[0] if entry else f"/home/{user}"

    # -- desired state ---------------------------------------------------- #

    @staticmethod
    def _fields(entry: Any) -> Tuple[str, str, str, Optional[str]]:
        if isinstance(entry, dict):
            return (entry["user"], entry["path"], entry["content"], entry.get("mode"))
        return (entry.user, entry.path, entry.content, getattr(entry, "mode", None))

    def _desired(self) -> Dict[str, Dict[str, Any]]:
        """Canonical absolute path -> {user, rel, content, mode}."""
        passwd = self._passwd()
        desired: Dict[str, Dict[str, Any]] = {}
        for entry in self._entries:
            user, rel, content, mode = self._fields(entry)
            canonical = f"{self._home_of(user, passwd).rstrip('/')}/{rel}"
            desired[canonical] = {"user": user, "rel": rel,
                                  "content": content, "mode": mode}
        return desired

    def _read(self, canonical: str) -> str:
        with open(self._abs(canonical), "r") as f:
            return f.read()

    def _exists(self, canonical: str) -> bool:
        return os.path.exists(self._abs(canonical))

    def actual(self) -> set:
        """Declared paths that exist on disk (no home-directory scan)."""
        if self._target() is None:
            return set()
        return {p for p in self._desired() if self._exists(p)}

    # -- v3 contract ------------------------------------------------------ #

    def plan(self, managed):
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
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        passwd = self._passwd()
        for path in sorted(set(desired) & actual):
            reason = self._drift_reason(path, desired[path], passwd)
            if reason:
                changes.append(Change(_DOMAIN, Op.MODIFY, path, reason=reason))
        return changes

    def _drift_reason(self, canonical: str, spec: Dict[str, Any],
                      passwd: Dict[str, Tuple[str, int, int]]) -> Optional[str]:
        if self._read(canonical) != spec["content"]:
            return "content drift"
        st = os.stat(self._abs(canonical))
        owner = passwd.get(spec["user"])
        if owner and (st.st_uid, st.st_gid) != (owner[1], owner[2]):
            # The quiet failure this exists for: right content, wrong owner, and
            # the application that has to rewrite the file cannot.
            return f"owner is not {spec['user']}"
        if spec["mode"] and (st.st_mode & 0o777) != int(spec["mode"], 8):
            return "mode drift"
        return None

    def managed_keys(self) -> dict:
        return {_DOMAIN: sorted(self._desired().keys())}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        passwd = self._passwd()

        for change in changes:
            if change.op in (Op.CREATE, Op.MODIFY):
                self._write(change.item, desired[change.item], passwd)
            elif change.op is Op.DELETE:
                path = self._abs(change.item)
                if os.path.exists(path):
                    os.remove(path)

    def _write(self, canonical: str, spec: Dict[str, Any],
               passwd: Dict[str, Tuple[str, int, int]]) -> None:
        owner = passwd.get(spec["user"])
        if owner is None:
            raise CommandExecutionError(
                f"cannot write {canonical}: the target has no user "
                f"{spec['user']!r}. Declare it under `users`, or drop the "
                "home_files entry — writing it anyway would leave a root-owned "
                "directory the user cannot use."
            )
        _home, uid, gid = owner
        path = self._abs(canonical)

        # Create the missing directories and hand each one to the user: a
        # `.config` owned by root is a directory the desktop cannot add to.
        created: List[str] = []
        parent = os.path.dirname(path)
        probe = parent
        while probe and not os.path.exists(probe):
            created.append(probe)
            probe = os.path.dirname(probe)
        os.makedirs(parent, exist_ok=True)
        for directory in reversed(created):
            os.chown(directory, uid, gid)

        with open(path, "w") as f:
            f.write(spec["content"])
        if spec["mode"]:
            os.chmod(path, int(spec["mode"], 8))
        os.chown(path, uid, gid)

    # -- sync -------------------------------------------------------------- #

    def import_state(self, managed=None) -> dict:
        """Report the declared entries plus the paths the manifest owns.

        Deliberately not a directory scan: a home holds ssh keys, browser
        profiles and gigabytes of state, none of which belongs in a config file.
        What dasik put there is exactly what it can honestly report.
        """
        if self._target() is None:
            return {_DOMAIN: []}

        passwd = self._passwd()
        out: List[Dict[str, Any]] = []
        seen: set = set()

        for canonical, spec in self._desired().items():
            if not self._exists(canonical):
                continue
            entry: Dict[str, Any] = {"user": spec["user"], "path": spec["rel"],
                                     "content": self._read(canonical)}
            if spec["mode"]:
                entry["mode"] = spec["mode"]
            out.append(entry)
            seen.add(canonical)

        for canonical in sorted(managed or []):
            if canonical in seen or not self._exists(canonical):
                continue
            owned = self._owner_of(canonical, passwd)
            if owned is None:
                continue
            user, rel = owned
            out.append({"user": user, "path": rel, "content": self._read(canonical)})
            seen.add(canonical)

        return {_DOMAIN: out}

    @staticmethod
    def _owner_of(canonical: str, passwd: Dict[str, Tuple[str, int, int]]
                  ) -> Optional[Tuple[str, str]]:
        """(user, path-relative-to-home) for an absolute path under some home.

        The longest matching home wins, so `/home` as somebody's home never
        swallows `/home/andres/...`.
        """
        best: Optional[Tuple[str, str]] = None
        best_len = -1
        for user, (home, _uid, _gid) in passwd.items():
            prefix = home.rstrip("/") + "/"
            if canonical.startswith(prefix) and len(prefix) > best_len:
                best, best_len = (user, canonical[len(prefix):]), len(prefix)
        return best

    # -- legacy executor shims --------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

    def verify(self) -> bool:
        return not self.plan(managed=[])
