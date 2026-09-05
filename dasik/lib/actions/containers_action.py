"""Action: the container runtime's id maps, and capturing the block back.

Most of the `containers` block rides domains that already exist — the expand
toggle contributes the packages, the unit, the `docker` group and
``/etc/docker/daemon.json``. Two things have no other owner:

* **subuid/subgid.** Rootless podman maps container uids into a range reserved
  for the user in ``/etc/subuid`` / ``/etc/subgid``. ``useradd`` writes one for
  users it creates (shadow ≥ 4.11.1-3), but a user created before that, one from
  a captured config, or one on a machine that grew podman later has none — and
  without it every rootless container fails to start.
* **the capture.** Nothing read the runtime back, so a synced machine would come
  back as a bare `podman` in `packages` with no block explaining why.

v3 domain ``subid``: the item is the username, because that is what is owned —
the range itself is allocated from whatever the machine has free.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Op

_DOMAIN = "subid"
_SUBUID = "/etc/subuid"
_SUBGID = "/etc/subgid"
_DAEMON_JSON = "/etc/docker/daemon.json"
_PODMAN_BIN = "/usr/bin/podman"
_DOCKERD_BIN = "/usr/bin/dockerd"
_DOCKER_BIN = "/usr/bin/docker"
_COMPOSE_BINS = {"podman": "/usr/bin/podman-compose",
                 "docker": "/usr/bin/docker-compose"}
_SOCKET_UNITS = {"podman": "podman.socket", "docker": "docker.socket"}

# What useradd hands the first user, and the size the wiki recommends: many base
# images (busybox, alpine) map 65536 ids, and less breaks them.
_FIRST_SUBID = 100000
_SUBID_COUNT = 65536


class ContainersAction(AbstractAction):
    """Converge the rootless id maps; capture the `containers` block."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._containers: Dict[str, Any] = cfg.get("containers") or {}
        self._users: List[Any] = cfg.get("users") or []

    @classmethod
    def empty_config(cls) -> Any:
        return {}

    @property
    def name(self) -> str:
        return "Container Runtime"

    @property
    def is_optional(self) -> bool:
        return True

    # -- paths -------------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # -- desired / actual ---------------------------------------------------- #

    def _rootless_users(self) -> List[str]:
        """Declared users that need an id map: podman, rootless, not root."""
        if self._containers.get("runtime") != "podman":
            return []
        if not self._containers.get("rootless", True):
            return []
        names = []
        for user in self._users:
            name = user.get("username") if isinstance(user, dict) else None
            if name and name != "root":
                names.append(name)
        return names

    def _read_map(self, canonical: str) -> List[str]:
        try:
            with open(self._p(canonical), "r", encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def _mapped_users(self) -> set:
        """Users with an entry in BOTH files — one alone maps nothing."""
        def owners(path: str) -> set:
            return {line.split(":", 1)[0] for line in self._read_map(path)
                    if ":" in line}
        return owners(_SUBUID) & owners(_SUBGID)

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return self._mapped_users()

    # -- v3 contract --------------------------------------------------------- #

    def plan(self, managed):
        if self._target() is None:
            return []
        from ..state.set_math import compute_changes

        changes, _drift = compute_changes(
            _DOMAIN,
            desired=self._rootless_users(),
            managed=managed,
            actual=self.actual(),
            op_install=Op.CREATE,
        )
        return changes

    def managed_keys(self) -> dict:
        return {_DOMAIN: sorted(self._rootless_users())}

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        creates = [c.item for c in changes if c.op is Op.CREATE]
        removes = [c.item for c in changes if c.op is Op.REMOVE]
        if creates:
            self._add_maps(creates)
        if removes:
            self._drop_maps(removes)

    def _add_maps(self, users: List[str]) -> None:
        """Append a range per user, skipping anyone who already has one.

        The re-check is not defensive noise: the plan is computed before
        ``UsersAction`` runs, and ``useradd`` writes the range itself for a user
        it creates — so by apply time the work is usually already done, and
        appending again would give one user two overlapping ranges.
        """
        for path in (_SUBUID, _SUBGID):
            lines = self._read_map(path)
            existing = {ln.split(":", 1)[0] for ln in lines if ":" in ln}
            next_start = self._next_start(lines)
            added = False
            for user in users:
                if user in existing:
                    continue
                lines.append(f"{user}:{next_start}:{_SUBID_COUNT}")
                next_start += _SUBID_COUNT
                added = True
            if added:
                self._write_map(path, lines)

    @staticmethod
    def _next_start(lines: List[str]) -> int:
        """The first id above every range already reserved in the file."""
        highest = _FIRST_SUBID
        for line in lines:
            parts = line.split(":")
            if len(parts) < 3:
                continue
            try:
                end = int(parts[1]) + int(parts[2])
            except ValueError:
                continue
            highest = max(highest, end)
        return highest

    def _drop_maps(self, users: List[str]) -> None:
        for path in (_SUBUID, _SUBGID):
            lines = self._read_map(path)
            kept = [ln for ln in lines
                    if ln.split(":", 1)[0] not in users or ":" not in ln]
            if kept != lines:
                self._write_map(path, kept)

    def _write_map(self, canonical: str, lines: List[str]) -> None:
        path = self._p(canonical)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(f"{line}\n" for line in lines if line.strip()))

    # -- capture -------------------------------------------------------------- #

    def import_state(self, managed=None) -> dict:
        """The `containers` block this machine is running, or nothing.

        The runtime is probed by its binary rather than by the package database:
        it answers for a target that is merely mounted, and it cannot be fooled
        mid-transaction.
        """
        if self._target() is None:
            return {}
        runtime = self._detect_runtime()
        if runtime is None:
            return {}
        block: Dict[str, Any] = {"runtime": runtime}
        if runtime == "podman":
            block["rootless"] = bool(self._mapped_users())
            # podman-docker is the only reason /usr/bin/docker exists on a
            # machine with no dockerd.
            block["docker_compat"] = os.path.exists(self._p(_DOCKER_BIN))
            block.update(self._captured_registries())
        block["compose"] = os.path.exists(self._p(_COMPOSE_BINS[runtime]))
        block["api_socket"] = self._unit_enabled(_SOCKET_UNITS[runtime])
        if runtime == "docker":
            daemon = self._read_daemon_json()
            if daemon is not None:
                block["daemon_json"] = daemon
        return {"containers": block}

    def _captured_registries(self) -> Dict[str, Any]:
        """The search order this machine actually has, if anyone owns one.

        The drop-in belongs to ``ContainerRegistriesAction``, but the CAPTURE
        belongs here: ``ConfigWriter.merge`` splices fragments by top-level key,
        so two actions both returning a `containers` block would overwrite each
        other rather than merge.

        Absent AND undeclared captures nothing, so a bootstrap sync adds no key
        to a machine that never had the file. Absent but DECLARED is cleared to
        `[]` rather than omitted: merge only overwrites a key, so silence would
        leave a stale list standing that this machine does not have.
        """
        from .container_registries_action import ContainerRegistriesAction

        found = ContainerRegistriesAction(
            {"containers": self._containers}, self.context).actual()
        if found:
            return {"search_registries": found}
        if self._containers.get("search_registries") is not None:
            return {"search_registries": []}
        return {}

    def _detect_runtime(self) -> Optional[str]:
        if os.path.exists(self._p(_DOCKERD_BIN)):
            return "docker"
        if os.path.exists(self._p(_PODMAN_BIN)):
            return "podman"
        return None

    def _unit_enabled(self, unit: str) -> bool:
        try:
            res = Command.execute("systemctl", ["is-enabled", unit],
                                  target=self._target())
        except Exception:      # nosec B110 - no systemctl means "not enabled"
            return False
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return out.strip() in ("enabled", "enabled-runtime", "static")

    def _read_daemon_json(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self._p(_DAEMON_JSON), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # -- legacy executor shims ------------------------------------------------ #


    def verify(self) -> bool:
        return not self.plan(managed=[])
