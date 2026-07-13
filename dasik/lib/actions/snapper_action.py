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

    def apply(self, changes) -> None:
        target = self._target()
        if target is None:
            return
        by_name = {c["name"]: c["subvolume"] for c in self.configs}
        for change in changes:
            subvol = by_name.get(change.item)
            if subvol is None:
                continue
            Command.execute(
                "snapper",
                ["--no-dbus", "-c", change.item, "create-config", subvol],
                target=target,
            )

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [c["name"] for c in self.configs]}

    def import_state(self, managed=None) -> dict:
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
