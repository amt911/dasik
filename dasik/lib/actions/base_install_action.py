"""Action: pacstrap the base system into the target (v3 domain "base").

Idempotent: a no-op once the base system is pacstrapped (marker:
``<target>/usr/bin/pacman``). Install-only. Target-aware. The destructive
pacstrap/genfstab lives in ``_install()`` (mocked in tests).
"""
from __future__ import annotations
import os
from typing import Any, Dict, List
from colorama import Fore, Style, init
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

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
        if changes:
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

    def _install(self) -> None:
        root = self._target_root()
        Command.execute("pacman", ["--noconfirm", "-Sy", "archlinux-keyring"])
        # A failed pacstrap must abort loudly — continuing to genfstab on a
        # half-installed root silently produces a broken system.
        pacstrap = Command.execute("pacstrap", ["-K", root] + self.packages)
        if getattr(pacstrap, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"pacstrap failed (rc={getattr(pacstrap, 'returncode', '?')}); "
                f"base system not installed into {root}."
            )
        result = Command.execute("genfstab", ["-U", root])
        if getattr(result, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"genfstab failed (rc={getattr(result, 'returncode', '?')})."
            )
        out = result.stdout
        fstab = out.decode() if isinstance(out, bytes) else (out or "")
        # An empty fstab is worse than an error: the installed system would boot
        # with no mounts declared. Refuse to write it.
        if not fstab.strip():
            raise CommandExecutionError(
                "genfstab produced an empty fstab; refusing to write a mountless "
                "/etc/fstab (the installed system would be non-bootable)."
            )
        with open(self._p("/etc/fstab"), "a") as f:
            f.write(fstab)
