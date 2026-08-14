"""Action: restore config-saver archives into `$HOME`, and capture the block.

The declarative half of `config_saver` rides existing domains through the expand
toggle: the package (with the Git source that builds it, since it is not in the
AUR), one JSON file per configuration under ``/etc/config-saver/configs``, and
``config-saver@<user>.timer`` per declared user.

What has no other owner is the **restore**: unpacking, on a fresh machine, the
archive the old one produced. That is the last piece of "dotfiles de $HOME" —
themes, browser profiles, keyboard layouts, the things a config file cannot
carry.

Idempotency: each restore leaves a marker named after the archive's **content**
hash under ``~/.local/state/dasik/config-saver/``. Re-running restores nothing;
replacing the archive with a newer capture restores again, which is what you
want from a file whose whole purpose is to change.

Un-declaring a restore removes nothing. Unpacking cannot be undone — the files
are the user's now — so the domain plans no removal at all.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_DOMAIN = "config_saver_restore"
_CONFIG_DIR = "/etc/config-saver/configs"
_BIN = "/usr/bin/config-saver"
_MARKER_DIR = ".local/state/dasik/config-saver"
_TIMER = "config-saver@{user}.timer"


class ConfigSaverAction(AbstractAction):
    """Restore config-saver archives; capture the `config_saver` block."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._block: Dict[str, Any] = cfg.get("config_saver") or {}

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "config-saver"

    @property
    def is_optional(self) -> bool:
        return True

    # -- paths --------------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _passwd(self) -> Dict[str, Tuple[str, int, int]]:
        out: Dict[str, Tuple[str, int, int]] = {}
        try:
            with open(self._p("/etc/passwd"), "r", encoding="utf-8") as f:
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

    # -- restore state -------------------------------------------------------- #

    @staticmethod
    def _fields(entry: Any) -> Tuple[str, str]:
        if isinstance(entry, dict):
            return entry["user"], entry["archive"]
        return entry.user, entry.archive

    def _restores(self) -> List[Tuple[str, str]]:
        return [self._fields(e) for e in self._block.get("restore") or []]

    @staticmethod
    def _item(user: str, archive: str) -> str:
        return f"{user}:{archive}"

    def _archive_digest(self, archive: str) -> Optional[str]:
        """sha256 of the archive as it is on the target, or None when absent."""
        try:
            with open(self._p(archive), "rb") as f:
                digest = hashlib.sha256()
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
                return digest.hexdigest()
        except OSError:
            return None

    def _marker(self, user: str, digest: str) -> Optional[str]:
        home = self._passwd().get(user, (f"/home/{user}", 0, 0))[0]
        return f"{home.rstrip('/')}/{_MARKER_DIR}/{digest}"

    def _restored(self, user: str, archive: str) -> bool:
        digest = self._archive_digest(archive)
        if digest is None:
            # No archive to compare against: report it as NOT restored. Silence
            # here would be indistinguishable from "already done", and apply is
            # where the missing path gets named.
            return False
        marker = self._marker(user, digest)
        return marker is not None and os.path.exists(self._p(marker))

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {self._item(u, a) for u, a in self._restores()
                if self._restored(u, a)}

    # -- v3 contract ---------------------------------------------------------- #

    def plan(self, managed):
        if self._target() is None:
            return []
        actual = self.actual()
        # No set-math removal block on purpose: un-declaring a restore cannot
        # un-restore a home directory, so the only honest op here is CREATE.
        return [Change(_DOMAIN, Op.CREATE, self._item(user, archive))
                for user, archive in self._restores()
                if self._item(user, archive) not in actual]

    def managed_keys(self) -> dict:
        return {_DOMAIN: sorted(self._item(u, a) for u, a in self._restores())}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        wanted = {c.item for c in changes if c.op is Op.CREATE}
        for user, archive in self._restores():
            if self._item(user, archive) in wanted:
                self._restore_one(user, archive)

    def _restore_one(self, user: str, archive: str) -> None:
        digest = self._archive_digest(archive)
        if digest is None:
            raise CommandExecutionError(
                f"config_saver.restore: {archive} does not exist on the target. "
                "Mount the medium that carries it (or drop the entry) — dasik "
                "will not pretend a home directory was restored."
            )
        owner = self._passwd().get(user)
        if owner is None:
            raise CommandExecutionError(
                f"config_saver.restore: the target has no user {user!r}.")
        _home, uid, gid = owner

        # The archive path is a positional parameter, never spliced into the
        # shell string — same rule as the AUR build path.
        Command.execute(
            "su",
            ["-", user, "-c", 'config-saver --decompress --input "$1"',
             "--", "sh", archive],
            target=self._target(), check=True, stream=True,
        )
        self._write_marker(user, digest, archive, uid, gid)

    def _write_marker(self, user: str, digest: str, archive: str,
                      uid: int, gid: int) -> None:
        marker = self._marker(user, digest)
        if marker is None:
            return
        path = self._p(marker)
        directory = os.path.dirname(path)
        created: List[str] = []
        probe = directory
        while probe and not os.path.exists(probe):
            created.append(probe)
            probe = os.path.dirname(probe)
        os.makedirs(directory, exist_ok=True)
        for made in reversed(created):
            os.chown(made, uid, gid)
        with open(path, "w", encoding="utf-8") as f:
            f.write(archive + "\n")
        os.chown(path, uid, gid)

    # -- capture --------------------------------------------------------------- #

    def _installed(self) -> bool:
        return os.path.exists(self._p(_BIN))

    def _pkg_owned(self, canonical: str) -> bool:
        try:
            res = Command.execute("pacman", ["-Qo", canonical], target=self._target())
            return getattr(res, "returncode", 1) == 0
        except Exception:      # nosec B110 - a failed probe just means "unknown"
            return False

    def _discover_configs(self) -> Dict[str, Any]:
        """The JSON configurations under /etc/config-saver/configs that no
        package owns. The YAML examples the package ships are pacman's, and
        re-encoding them would put the distro's defaults in the config file."""
        base = self._p(_CONFIG_DIR)
        out: Dict[str, Any] = {}
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            full = os.path.join(base, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            if self._pkg_owned(f"{_CONFIG_DIR}/{name}"):
                continue
            try:
                with open(full, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict):
                out[name[: -len(".json")]] = doc
        return out

    def _timer_users(self) -> List[str]:
        users = []
        for user, (_home, uid, _gid) in sorted(self._passwd().items()):
            if not 1000 <= uid < 65534:
                continue
            if self._unit_enabled(_TIMER.format(user=user)):
                users.append(user)
        return users

    def _unit_enabled(self, unit: str) -> bool:
        try:
            res = Command.execute("systemctl", ["is-enabled", unit],
                                  target=self._target())
        except Exception:      # nosec B110 - no systemctl means "not enabled"
            return False
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return out.strip() in ("enabled", "enabled-runtime")

    def import_state(self, managed=None) -> dict:
        """The `config_saver` block this machine is running, or nothing.

        `restore` and `source` come back from the config, not the machine: a
        marker names a content hash and the built package names no repository,
        so neither can be reconstructed from the target. They are intent, like
        a package's `optional` flag, and losing them would make the next apply
        rebuild nothing and restore nothing.
        """
        if self._target() is None or not self._installed():
            return {}
        block: Dict[str, Any] = {
            "configs": self._discover_configs(),
            "timer_users": self._timer_users(),
        }
        if self._block.get("source"):
            block["source"] = dict(self._block["source"])
        restores = self._restores()
        if restores:
            block["restore"] = [{"user": u, "archive": a} for u, a in restores]
        return {"config_saver": block}

    # -- legacy executor shims -------------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

    def verify(self) -> bool:
        return not self.plan(managed=[])
