"""Action: create snapper (btrfs snapshot) configs declaratively.

The snapper package + the timeline/cleanup timers are contributed by the
`snapper` expand toggle (packages + systemd). This action does the imperative
bit those can't: ``snapper -c <name> create-config <subvolume>``. It is
idempotent — a create-config is planned only for a config that does not already
exist under /etc/snapper/configs, so a converged system re-plans to nothing.
"""
import os
from typing import Any, List

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_CONFIGS_DIR = "/etc/snapper/configs"


class SnapperAction(AbstractAction):
    """Create snapper configs for the declared btrfs subvolumes."""

    _DOMAIN = "snapper"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg = config if isinstance(config, dict) else {}
        self.enable: bool = cfg.get("enable", False)
        raw = cfg.get("configs") or ([{"name": "root", "subvolume": "/"}]
                                     if self.enable else [])
        # accept dicts or model-like objects
        self.configs: List[dict] = [
            c if isinstance(c, dict) else {"name": c.name, "subvolume": c.subvolume}
            for c in raw
        ]

    @property
    def name(self) -> str:
        return "Snapper Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _config_path(self, name: str) -> str:
        t = self._target()
        canonical = f"{_CONFIGS_DIR}/{name}"
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _exists(self, name: str) -> bool:
        return os.path.exists(self._config_path(name))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        if not self.enable:
            return set()
        return {c["name"] for c in self.configs if self._exists(c["name"])}

    def plan(self, managed):
        if not self.enable:
            return []
        changes: List[Change] = []
        for c in self.configs:
            if not self._exists(c["name"]):
                changes.append(Change(self._DOMAIN, Op.CREATE, c["name"],
                                      reason="create-config"))
        return changes

    @staticmethod
    def _snapshots_dir(subvol: str) -> str:
        base = subvol.rstrip("/")
        return f"{base}/.snapshots" if base else "/.snapshots"

    def _is_snapshots_mount(self, snap_dir: str, target) -> bool:
        res = Command.execute("mountpoint", ["-q", snap_dir], target=target)
        return getattr(res, "returncode", 1) == 0

    def apply(self, changes) -> None:
        target = self._target()
        if target is None:
            return
        by_name = {c["name"]: c["subvolume"] for c in self.configs}
        for change in changes:
            subvol = by_name.get(change.item)
            if subvol is None:
                continue
            self._create_config(change.item, subvol, target)

    def _create_config(self, name: str, subvol: str, target) -> None:
        # `snapper create-config` fails if a `.snapshots` subvolume already
        # exists at the config path. Dasik pre-creates & mounts a dedicated
        # @snapshots subvolume there, so follow the Arch wiki: unmount it, let
        # snapper make its own nested .snapshots, delete that, and remount our
        # subvolume (via its existing fstab entry). Without this the create
        # failed silently and re-fired on every apply (non-idempotent).
        snap_dir = self._snapshots_dir(subvol)
        preexist = self._is_snapshots_mount(snap_dir, target)
        if preexist:
            Command.execute("umount", [snap_dir], target=target)
            Command.execute("rmdir", [snap_dir], target=target)

        res = Command.execute(
            "snapper", ["--no-dbus", "-c", name, "create-config", subvol],
            target=target,
        )
        if getattr(res, "returncode", 0) != 0:
            # Surface the failure instead of swallowing it (the bug the VM caught):
            # a swallowed error left the config uncreated and the action re-firing.
            raise CommandExecutionError(
                f"snapper create-config for '{name}' failed "
                f"(rc={getattr(res, 'returncode', '?')})"
            )

        if preexist:
            # Delete snapper's auto-created nested .snapshots and restore our own.
            Command.execute("btrfs", ["subvolume", "delete", snap_dir], target=target)
            Command.execute("mkdir", ["-p", snap_dir], target=target)
            Command.execute("mount", [snap_dir], target=target)

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [c["name"] for c in self.configs]}

    def import_state(self, managed=None) -> dict:
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
