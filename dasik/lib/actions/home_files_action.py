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


# Home directories whose entire contents are declarative policy, and therefore
# safe to capture. `configs.d` holds config-saver documents: a short list of what
# to back up, in a directory nothing else writes to. Adding to this list means
# claiming the same about another directory — a home is otherwise off limits.
_SCANNED_HOME_DIRS = (".config/config-saver/configs.d",)


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
        if os.path.islink(self._abs(canonical)):
            # $HOME is the user's; dasik writes there as root. Following a link
            # planted at a managed path would put root's write wherever it
            # points, and leave the path a link, so this never converges.
            return "replaced by a symlink"
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
        self._check_parent(parent, canonical)
        os.makedirs(parent, exist_ok=True)
        for directory in reversed(created):
            os.chown(directory, uid, gid)

        # Replace a symlink rather than write through it (see _drift_reason).
        if os.path.islink(path):
            os.remove(path)
        mode = int(spec["mode"], 8) if spec["mode"] else None
        if mode is None:
            with open(path, "w") as f:
                f.write(spec["content"])
        else:
            # A declared mode means the content is a secret — an ssh config, a
            # token, a .netrc. Writing it and chmod'ing after left it readable
            # by every user for the length of the write, and for good if the
            # apply died in between. The mode goes on the descriptor first:
            # O_CREAT covers a new file, fchmod one that already existed (a
            # dotfile left at 0644 by an older dasik is the case that matters).
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w") as f:
                f.write(spec["content"])
        os.chown(path, uid, gid)

    @staticmethod
    def _check_parent(parent: str, canonical: str) -> None:
        """Refuse a parent path that is not a directory dasik may write into.

        A regular file in the way aborted the whole apply with a bare
        `[Errno 17] File exists` naming nothing; a SYMLINKED directory
        (`~/.config -> /etc`) would have sent every home file somewhere else
        entirely, as root.
        """
        if os.path.islink(parent):
            raise CommandExecutionError(
                f"cannot write {canonical}: {parent} is a symlink, and dasik "
                "will not write into a directory somebody redirected. Remove "
                "it (or point the declaration elsewhere) and apply again."
            )
        if os.path.exists(parent) and not os.path.isdir(parent):
            raise CommandExecutionError(
                f"cannot write {canonical}: {parent} exists and is not a "
                "directory. Remove it and apply again."
            )

    # -- sync -------------------------------------------------------------- #

    def import_state(self, managed=None) -> dict:
        """Report the declared entries, what the manifest owns, and the
        config-saver documents a user wrote by hand.

        A home is **not** scanned: it holds ssh keys, browser profiles and
        gigabytes of state, none of which belongs in a config file. The one
        exception is a directory that holds nothing else —
        ``.config/config-saver/configs.d``, which is pure declarative policy
        (what to back up) and short. Capturing it is what lets you keep the
        documents in one place and still have them on a machine dasik installs
        BEFORE anyone logs in, instead of only after an archive is restored.
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

        out.extend(self._discover_scanned(passwd, seen))
        return {_DOMAIN: out}

    def _discover_scanned(self, passwd: Dict[str, Tuple[str, int, int]],
                          seen: set) -> List[Dict[str, Any]]:
        """Files in the few home directories that hold only policy."""
        found: List[Dict[str, Any]] = []
        for user, (home, uid, _gid) in sorted(passwd.items()):
            # Somebody who logs in and writes backup documents; a system
            # account's home is not a place to go looking.
            if not 1000 <= uid < 65534:
                continue
            for relative_dir in _SCANNED_HOME_DIRS:
                directory = f"{home.rstrip('/')}/{relative_dir}"
                for name in self._list_dir(directory):
                    canonical = f"{directory}/{name}"
                    if canonical in seen or self._is_symlink(canonical):
                        continue
                    content = self._read_text(canonical)
                    if content is None:      # binary, unreadable, or a directory
                        continue
                    found.append({"user": user,
                                  "path": f"{relative_dir}/{name}",
                                  "content": content})
                    seen.add(canonical)
        return found

    def _list_dir(self, canonical: str) -> List[str]:
        try:
            return sorted(os.listdir(self._abs(canonical)))
        except OSError:
            return []

    def _is_symlink(self, canonical: str) -> bool:
        return os.path.islink(self._abs(canonical))

    def _read_text(self, canonical: str) -> Optional[str]:
        path = self._abs(canonical)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError):
            return None

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


    def verify(self) -> bool:
        return not self.plan(managed=[])
