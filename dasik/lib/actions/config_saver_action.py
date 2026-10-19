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

``restic_stores`` (domain ``config_saver_restic``) creates config-saver's
deduplicated restic store per user with ``config-saver --restic-init``. Present
means the repository AND its age-encrypted password file exist. Un-declaring
never deletes a store — it holds the backups. A missing prerequisite (restic,
age, the key, config-saver < 3.5.0) is a warning, not a failed apply: restic is
optional, and on a fresh install the key arrives after the first boot. Moving
old archives in (``--migrate --delete-migrated``) stays a manual step: an
``apply`` that deletes backups is not "the same JSON changes nothing".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..logging import run_logger
from ..models.config_saver_model import ConfigSaverResticStore
from ..login_shell import MARKED, strip_login_noise
from ..state.change import Change, Op

_DOMAIN = "config_saver_restore"
_CONFIG_DIR = "/etc/config-saver/configs"
_BIN = "/usr/bin/config-saver"
_MARKER_DIR = ".local/state/dasik/config-saver"
_TIMER = "config-saver@{user}.timer"

# config-saver's restic store (3.5.0+), relative to the user's home.
_RESTIC_DOMAIN = "config_saver_restic"
_SAVES = ".config/config-saver"
_STORE_REPO = f"{_SAVES}/restic"
_STORE_PASSWORD = f"{_SAVES}/restic-password.age"
_STORE_SETTINGS = f"{_SAVES}/restic-store.json"
_DEFAULT_IDENTITY = ConfigSaverResticStore.model_fields["identity"].default
_RESTIC_MIN = (3, 5, 0)
_VERSION = re.compile(r"config-saver (\d+)\.(\d+)\.(\d+)")


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

    # -- restic store state ------------------------------------------------ #

    @staticmethod
    def _store_fields(entry: Any) -> Tuple[str, str]:
        if isinstance(entry, dict):
            return entry["user"], entry.get("identity") or _DEFAULT_IDENTITY
        return entry.user, entry.identity

    def _stores(self) -> List[Tuple[str, str]]:
        return [self._store_fields(e) for e in self._block.get("restic_stores") or []]

    def _home(self, user: str) -> Optional[str]:
        entry = self._passwd().get(user)
        return entry[0].rstrip("/") if entry else None

    def _store_parts(self, home: str) -> Tuple[bool, bool]:
        # Read as root inside the user's home: a link is not their store.
        repo = self._p(f"{home}/{_STORE_REPO}")
        password = self._p(f"{home}/{_STORE_PASSWORD}")
        return (os.path.isdir(repo) and not os.path.islink(repo),
                os.path.isfile(password) and not os.path.islink(password))

    def _has_store(self, user: str) -> bool:
        home = self._home(user)
        return home is not None and all(self._store_parts(home))

    def _store_actual(self) -> set:
        if self._target() is None:
            return set()
        return {user for user, _identity in self._stores() if self._has_store(user)}

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
        stores = self._discover_stores()
        # A declared store that does not exist yet (the key arrives after the
        # first boot) is intent, like a restore: keep it.
        for user, identity in self._stores():
            stores.setdefault(user, identity)
        if stores:
            block["restic_stores"] = [{"user": u, "identity": i}
                                      for u, i in sorted(stores.items())]
        return {"config_saver": block}

    def _discover_stores(self) -> Dict[str, str]:
        """Users with a complete store, and the identity it records."""
        found: Dict[str, str] = {}
        for user, (home, uid, _gid) in sorted(self._passwd().items()):
            if not 1000 <= uid < 65534 or not self._has_store(user):
                continue
            identity = _DEFAULT_IDENTITY
            settings = self._p(f"{home.rstrip('/')}/{_STORE_SETTINGS}")
            recorded = None
            if not os.path.islink(settings):     # root never follows a planted link
                try:
                    with open(settings, encoding="utf-8") as f:
                        recorded = json.load(f).get("identity")
                except (OSError, ValueError, AttributeError):
                    recorded = None
            if isinstance(recorded, str) and recorded.startswith("/"):
                prefix = home.rstrip("/") + "/"
                identity = ("~/" + recorded[len(prefix):]
                            if recorded.startswith(prefix) else recorded)
            found[user] = identity
        return found

    # -- legacy executor shims -------------------------------------------------- #


    def verify(self) -> bool:
        return not self.plan(managed=[])


class ConfigSaverResticAction(ConfigSaverAction):
    """``config_saver.restic_stores``: config-saver's restic store, per user.

    Its own action because the reconciler gives each action one domain. It
    plans and applies only; the ``config_saver`` block — stores included — is
    captured by ``ConfigSaverAction`` alone, since ``sync`` splices fragments by
    top-level key and two writers of one key would drop each other's half.
    Registered right after it: a restore may be what brings the key home.
    """

    @property
    def name(self) -> str:
        return "config-saver restic store"

    def actual(self) -> set:
        return self._store_actual()

    def plan(self, managed):
        if self._target() is None:
            return []
        present = self._store_actual()
        # The store holds the backups: un-declaring it plans no removal.
        return [Change(_RESTIC_DOMAIN, Op.CREATE, user)
                for user, _identity in self._stores() if user not in present]

    def managed_keys(self) -> dict:
        return {_RESTIC_DOMAIN: sorted(u for u, _i in self._stores())}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        wanted = {c.item for c in changes if c.op is Op.CREATE}
        for user, identity in self._stores():
            if user in wanted:
                self._init_store(user, identity)

    def import_state(self, managed=None) -> dict:
        return {}

    def _warn(self, user: str, message: str, detail: str) -> None:
        run_logger.get().warning(
            f"config_saver.restic_stores: {message} — the store for {user} was "
            "not created; config-saver keeps writing .tar.gz archives.",
            detail=detail + " Apply again once it is fixed; `plan` keeps "
                            "naming the store until it exists.")

    def _config_saver_version(self) -> Optional[Tuple[int, int, int]]:
        res = strip_login_noise(Command.execute(
            "config-saver", ["--version"], target=self._target()))
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        match = _VERSION.search(str(out))
        return tuple(int(g) for g in match.groups()) if match else None  # type: ignore[return-value]

    def _resolve_identity(self, home: str, identity: str) -> str:
        return home + identity[1:] if identity.startswith("~/") else identity

    def _init_store(self, user: str, identity: str) -> None:
        home = self._home(user)
        if home is None:
            raise CommandExecutionError(
                f"config_saver.restic_stores: the target has no user {user!r}.")
        repo, password = self._store_parts(home)
        if repo or password:
            missing = _STORE_PASSWORD if repo else _STORE_REPO
            raise CommandExecutionError(
                f"config_saver.restic_stores: {user}'s restic store is incomplete "
                f"({home}/{missing} is missing). Without the password file the "
                "repository cannot be read; dasik will not initialise over it. "
                "Restore the missing piece, or move the other one aside if the "
                "store is truly lost, then apply again.")
        for binary, package in ((_BIN, "config-saver"), ("/usr/bin/restic", "restic"),
                                ("/usr/bin/age", "age")):
            if not os.path.exists(self._p(binary)):
                self._warn(user, f"{package} is not installed",
                           f"config-saver's store needs `{package}` (`pacman -S "
                           f"{package}`, or declare it in `packages`).")
                return
        version = self._config_saver_version()
        if version is None or version < _RESTIC_MIN:
            found = ".".join(map(str, version)) if version else "unknown"
            self._warn(user, f"config-saver {found} is older than 3.5.0",
                       "The restic store arrived in config-saver 3.5.0: upgrade "
                       "it (`pacman -Syu config-saver`, or bump "
                       "`config_saver.source.ref`).")
            return
        key = self._resolve_identity(home, identity)
        if not os.path.isfile(self._p(key)):
            self._warn(user, f"the age key {key} is not on the target yet",
                       "The store password is encrypted to it, so it has to be "
                       "there first — on a fresh install, restore it from where "
                       "you keep it (it is the one secret that must not live "
                       "only on this machine).")
            return
        derived = strip_login_noise(Command.execute(
            "su", ["-", user, "-c", MARKED('age-keygen -y "$1"'), "--",
                   "age-keygen", key], target=self._target()))
        out = getattr(derived, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        recipients = [line.strip() for line in str(out).splitlines() if line.strip()]
        if getattr(derived, "returncode", 1) != 0 or not recipients:
            err = getattr(derived, "stderr", b"") or b""
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            raise CommandExecutionError(
                f"config_saver.restic_stores: `age-keygen -y {key}` failed for "
                f"{user}: {str(err).strip() or 'no public key'}")
        # Values are positional parameters, never spliced into the shell string.
        Command.execute(
            "su", ["-", user, "-c", 'config-saver "$@"', "--", "config-saver",
                   "--restic-init", "--encrypt-to", recipients[0], "--identity", key],
            target=self._target(), check=True, stream=True)
