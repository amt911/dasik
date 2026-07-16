"""Shared partition predicates (used by the bootloader + kernel-cmdline actions)."""
from typing import Any, Dict


def mounts_root(part: Dict[str, Any]) -> bool:
    """True if this partition provides ``/``: either the partition itself mounts
    ``/``, or (btrfs) one of its subvolumes does. A synced btrfs root often has
    ``mountpoint: null`` with the ``/`` living on the ``@`` subvolume — the entry
    derivation must still treat it as the root, or the LUKS never opens and boot
    hangs on ``/dev/disk/by-label/root``."""
    if part.get("mountpoint") == "/":
        return True
    return any(s.get("mountpoint") == "/"
               for s in part.get("btrfs_subvolumes", []) or [])
