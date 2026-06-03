"""Action: pacstrap the base system into the target (v3 domain "base").

Idempotent: a no-op once the base system is pacstrapped (marker:
``<target>/usr/bin/pacman``). Install-only. Target-aware. The destructive
pacstrap/genfstab lives in ``_install()`` (mocked in tests).
"""
from __future__ import annotations
import os
import sys
from typing import Any, Dict, List
from colorama import Fore, Style, init
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_PACMAN_CACHE = "/var/cache/pacman/pkg"

_MARKER = "/usr/bin/pacman"
_DOMAIN = "base"


class BaseInstallAction(AbstractAction):
    """Install the Arch base system (base, linux, firmware, microcode)."""

    _DOMAIN = _DOMAIN

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

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _target_root(self) -> str:
        t = self._target()
        return t.root if t is not None else "/mnt"

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

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

    def _installed(self) -> bool:
        return os.path.exists(self._p(_MARKER))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"base"} if self._installed() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if not self._installed():
            return [Change(self._DOMAIN, Op.INSTALL, "base", reason="pacstrap")]
        return []

    def apply(self, changes) -> None:
        # Re-check at APPLY time, not just from the (possibly stale) plan: on a
        # re-run from a fresh live, the plan is computed before disks mounts
        # /mnt, so it may say INSTALL even though base is already there. Don't
        # re-pacstrap an existing system.
        if not self._installed():
            self._install()

    def import_state(self, managed=None) -> dict:
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return not self._installed()

    def execute(self) -> None:
        self._install()

    def verify(self) -> bool:
        return self._installed()

    # --- the destructive bit (mocked in tests) ------------------------ #

    @staticmethod
    def _available_ram_kb() -> int:
        """Available RAM in KiB from /proc/meminfo (0 if unreadable)."""
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1])
        except (OSError, ValueError):
            pass
        return 0

    def _cache_to_ram(self) -> None:
        """Give pacman's download cache its own RAM-backed tmpfs, sized to most
        of the available memory.

        On a live ISO the writable root (airootfs) overlay is often capped small
        (e.g. 256 MiB); a large pacstrap fills it and fails with "no space".
        Mounting a fresh tmpfs over the host cache lets the downloads use real
        RAM instead of the capped overlay. **Volatile only** — never touches the
        target disk. Only when installing into a chroot target (root != "/").
        Best-effort: a failure here falls back to the default cache.
        """
        t = self._target()
        if t is None or not t.is_chroot:
            return
        if os.path.ismount(_PACMAN_CACHE):
            return
        # leave headroom for pacman/pacstrap themselves
        size_kb = (self._available_ram_kb() * 3) // 4
        if size_kb <= 0:
            return
        try:
            Command.execute_checked(
                "mount",
                ["-t", "tmpfs", "-o", f"size={size_kb}k", "tmpfs", _PACMAN_CACHE],
            )
        except CommandExecutionError:
            print("  Warning: could not mount a RAM cache for pacman; "
                  "using the default cache.", file=sys.stderr)

    def _install(self) -> None:
        root = self._target_root()
        Command.execute_checked("pacman", ["--noconfirm", "-Sy", "archlinux-keyring"])
        self._cache_to_ram()
        Command.execute_checked("pacstrap", ["-K", root] + self.packages)
        fstab = Command.execute_checked("genfstab", ["-U", root], capture=True).stdout.decode()
        with open(self._p("/etc/fstab"), "a") as f:
            f.write(fstab)
