"""Action: install the bootloader and create the base boot entry (v3 domain "bootloader").

Installs systemd-boot (`bootctl install`) or GRUB (`grub-install` + `grub-mkconfig`)
and writes the initial loader entry. `KernelCmdlineAction` maintains the entry
params afterward. Idempotent via an install marker. Install-only. Target-aware.
The destructive install lives in `_install()` (mocked in tests).
"""
from __future__ import annotations
import os
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op

_DOMAIN = "bootloader"
_SDBOOT_MARKER = "/boot/EFI/systemd/systemd-bootx64.efi"
_GRUB_MARKER = "/boot/grub/grub.cfg"


class BootloaderAction(AbstractAction):
    """Install the bootloader (systemd-boot or GRUB) declaratively."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self.bootloader: str = cfg.get("bootloader", "grub")
        self.enable_microcode: bool = cfg.get("enable_microcode", False)

    @property
    def name(self) -> str:
        return "Bootloader"

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- config-derived helpers --------------------------------------- #

    def _root_label(self) -> str:
        disks = self._cfg.get("disks") or {}
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") == "/":
                    return part.get("label", "root")
        return "root"

    def _is_sdboot(self) -> bool:
        return self.bootloader in ("sd-boot", "systemd-boot")

    def _installed(self) -> bool:
        marker = _SDBOOT_MARKER if self._is_sdboot() else _GRUB_MARKER
        return os.path.exists(self._p(marker))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {self.bootloader} if self._installed() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if not self._installed():
            return [Change(self._DOMAIN, Op.INSTALL, self.bootloader, reason="install bootloader")]
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

    # --- destructive install (mocked in tests) ------------------------ #

    def _ucode_initrds(self) -> List[str]:
        if not self.enable_microcode:
            return []
        # both are harmless if absent; the present one is used
        return ["/intel-ucode.img", "/amd-ucode.img"]

    def _install(self) -> None:  # pragma: no cover - shells out to bootctl/grub
        t = self._target()
        if self._is_sdboot():
            Command.execute("bootctl", ["install"], target=t)
            loader = self._p("/boot/loader/loader.conf")
            os.makedirs(os.path.dirname(loader), exist_ok=True)
            with open(loader, "w") as f:
                f.write("default arch\ntimeout 3\nconsole-mode max\n")
            entries_dir = self._p("/boot/loader/entries")
            os.makedirs(entries_dir, exist_ok=True)
            lines = ["title Arch Linux", "linux /vmlinuz-linux"]
            for img in self._ucode_initrds():
                lines.append(f"initrd {img}")
            lines.append("initrd /initramfs-linux.img")
            lines.append(f"options root=LABEL={self._root_label()} rw")
            with open(os.path.join(entries_dir, "arch.conf"), "w") as f:
                f.write("\n".join(lines) + "\n")
        else:
            Command.execute("pacman", ["--noconfirm", "--needed", "-S", "grub", "efibootmgr"], target=t)
            Command.execute("grub-install", [
                "--target=x86_64-efi", "--efi-directory=/boot", "--bootloader-id=GRUB",
            ], target=t)
            Command.execute("grub-mkconfig", ["-o", "/boot/grub/grub.cfg"], target=t)
