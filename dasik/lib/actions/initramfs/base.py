"""Initramfs generator backend interface + shared disk-config detection."""
from __future__ import annotations
from typing import Any, Dict, Optional

from ..partition_utils import mounts_root


def detect_encryption(cfg: Dict[str, Any]) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("encrypt", False):
                    return True
    return False


def detect_root_fs(cfg: Dict[str, Any]) -> Optional[str]:
    """Filesystem of the partition that provides ``/``.

    Uses the shared ``mounts_root`` predicate so a synced btrfs root — with
    ``mountpoint: null`` and ``/`` living on the ``@`` subvolume — is recognized;
    otherwise the backend never forces btrfs into the initramfs and the encrypted
    root fails to mount."""
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if mounts_root(part):
                    return part.get("filesystem")
    return None


def _any_partition_flag(cfg: Dict[str, Any], flag: str) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get(flag, False):
                    return True
    return False


def detect_fido2(cfg: Dict[str, Any]) -> bool:
    return _any_partition_flag(cfg, "unlock_fido2")


def detect_tpm2(cfg: Dict[str, Any]) -> bool:
    return _any_partition_flag(cfg, "unlock_tpm2")


def detect_hibernation(cfg: Dict[str, Any]) -> bool:
    """True when the config asks for hibernation.

    Either a swap partition is declared, or the kernel cmdline names a resume
    device (a synced config can carry ``resume=`` with the swap described
    elsewhere). The initramfs needs the resume module in both cases: without it
    the kernel never restores the image and simply boots fresh — the hibernation
    write succeeds and the session is silently lost.
    """
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("filesystem") == "swap":
                    return True
    for token in cfg.get("kernel_cmdline", []) or []:
        for word in str(token).split():
            if word.startswith("resume="):
                return True
    return False


def detect_bluetooth_in_initramfs(cfg: Dict[str, Any]) -> bool:
    bt = cfg.get("bluetooth")
    return bool(isinstance(bt, dict) and bt.get("in_initramfs"))


class InitramfsBackend:
    """Compute + apply the initramfs configuration for one generator."""

    def __init__(self, config: Dict[str, Any], target=None):
        self.config = config if isinstance(config, dict) else {}
        self.target = target
        self.has_encryption = detect_encryption(self.config)
        self.root_fs = detect_root_fs(self.config)
        self.has_fido2 = detect_fido2(self.config)
        self.has_tpm2 = detect_tpm2(self.config)
        self.bluetooth_in_initramfs = detect_bluetooth_in_initramfs(self.config)
        self.has_hibernation = detect_hibernation(self.config)

    def _path(self, canonical: str) -> str:
        if self.target is not None:
            return self.target.path(canonical)
        return "/mnt" + canonical

    def desired_value(self) -> str:
        raise NotImplementedError

    def actual_value(self) -> Optional[str]:
        raise NotImplementedError

    def apply(self) -> None:
        raise NotImplementedError
