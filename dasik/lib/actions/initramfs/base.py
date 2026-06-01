"""Initramfs generator backend interface + shared disk-config detection."""
from __future__ import annotations
from typing import Any, Dict, Optional


def detect_encryption(cfg: Dict[str, Any]) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("encrypt", False):
                    return True
    return False


def detect_root_fs(cfg: Dict[str, Any]) -> Optional[str]:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") == "/":
                    return part.get("filesystem")
    return None


class InitramfsBackend:
    """Compute + apply the initramfs configuration for one generator."""

    def __init__(self, config: Dict[str, Any], target=None):
        self.config = config if isinstance(config, dict) else {}
        self.target = target
        self.has_encryption = detect_encryption(self.config)
        self.root_fs = detect_root_fs(self.config)

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
