"""Action: enable systemd units declaratively.

Idempotent: only enables units that are not already enabled.
"""
from typing import Any, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Op
import subprocess


class SystemdAction(AbstractAction):
    """Enable systemd services / sockets / timers inside chroot."""

    _SYSTEMD_DOMAIN = "systemd"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg = config if isinstance(config, dict) else {}
        self.units: List[str] = cfg.get("enable_units", [])
        self.sockets: List[str] = cfg.get("enable_sockets", [])
        self.disable_units: List[str] = cfg.get("disable_units", [])

    def _d_on(self) -> List[str]:
        return self.units + self.sockets

    def _d_off(self) -> List[str]:
        return self.disable_units

    def actual(self) -> set:
        """Set of all enabled unit files on the target (A = all enabled)."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return set()
        result = Command.execute(
            "systemctl", ["list-unit-files", "--state=enabled", "--no-legend"],
            target=target,
        )
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return {line.split()[0] for line in stdout.splitlines() if line.split()}

    @property
    def name(self) -> str:
        return "Systemd Units"

    @property
    def is_optional(self) -> bool:
        return True

    # helpers ---------------------------------------------------------------

    @staticmethod
    def _is_enabled(unit: str) -> bool:
        result = subprocess.run(
            ["arch-chroot", "/mnt", "systemctl", "is-enabled", unit],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        return result.stdout.decode().strip() == "enabled"

    def _all_units(self) -> List[str]:
        return self.units + self.sockets

    def _pending(self) -> List[str]:
        return [u for u in self._all_units() if not self._is_enabled(u)]

    # v3 contract -----------------------------------------------------------

    def plan(self, managed):
        from ..state.set_math import compute_changes
        changes, _drift = compute_changes(
            self._SYSTEMD_DOMAIN,
            desired=self._d_on(),
            managed=managed,
            actual=self.actual(),
            op_install=Op.ENABLE,
            op_remove=Op.DISABLE,
            forced=self._d_off(),
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._SYSTEMD_DOMAIN: self._d_on()}

    # idempotency -----------------------------------------------------------

    def is_needed(self) -> bool:
        return bool(self._pending())

    def execute(self) -> None:
        for unit in self._pending():
            print(f"  Enabling {unit} …")
            subprocess.run(
                ["arch-chroot", "/mnt", "systemctl", "enable", unit],
                check=True,
            )

    def verify(self) -> bool:
        return not self._pending()
