"""Action: pacstrap the base system into /mnt.

Ported from the legacy ``do_action`` form to the AbstractAction contract
(name/is_needed/execute/verify) so the v2 ActionExecutor can drive it
(issue #66). Reads root-level config (registered with config_key='__root__').

Idempotent: skips when the base system is already pacstrapped into /mnt.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List
from colorama import Fore, Style, init
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command


class BaseInstallAction(AbstractAction):
    """Install the Arch base system (base, linux, firmware, microcode)."""

    # Marker that pacstrap has populated the target.
    _INSTALLED_MARKER = "/mnt/usr/bin/pacman"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.enable_microcode: bool = cfg.get("enable_microcode", False)
        self.packages: List[str] = ["base", "linux", "linux-firmware"]

        init(autoreset=True)
        if self.enable_microcode:
            self.packages += [self._detect_microcode()]

    @property
    def name(self) -> str:
        return "Base Installation"

    @staticmethod
    def _detect_microcode() -> str:
        with open("/proc/cpuinfo", "r") as cpuinfo:
            content = cpuinfo.read()
        if "AuthenticAMD" in content:
            return "amd-ucode"
        if "GenuineIntel" in content:
            return "intel-ucode"
        print(Fore.RED + "Unknown CPU Vendor. Exiting..." + Style.RESET_ALL)
        raise SystemExit(1)

    def is_needed(self) -> bool:
        # If pacman exists inside the target, the base system is in place.
        return not os.path.exists(self._INSTALLED_MARKER)

    def execute(self) -> None:  # pragma: no cover - destructive: pacstrap/genfstab
        Command.execute("pacman", ["--noconfirm", "-Sy", "archlinux-keyring"])
        Command.execute("pacstrap", ["-K", "/mnt"] + self.packages)
        fstab_content_str = Command.execute("genfstab", ["-U", "/mnt"]).stdout.decode()
        with open("/mnt/etc/fstab", "a") as fstab:
            fstab.write(fstab_content_str)

    def verify(self) -> bool:
        return os.path.exists(self._INSTALLED_MARKER)
