"""fstab text helpers for day-2 btrfs subvolumes.

``without_btrfs_lines`` is pure; ``write_fstab`` is the one IO edge (atomic,
durable, mode-preserving replace). ``BtrfsSubvolumeAction`` owns the reading.
"""
from __future__ import annotations

import os
import stat
from typing import List, Optional


def _names_subvol(options: str, name: str) -> bool:
    return any(o in (f"subvol={name}", f"subvol=/{name}") for o in options.split(","))


def without_btrfs_lines(text: str, mountpoint: str, name: Optional[str] = None,
                        uuid: Optional[str] = None) -> List[str]:
    """The fstab lines minus the btrfs ones for *mountpoint* and, when *name*
    and *uuid* are given, minus every btrfs line of that subvolume on that
    filesystem (so adopting it at a new mountpoint cannot leave it mounted
    twice). Comments and any other line stay."""
    kept = []
    for ln in text.splitlines():
        cols = ln.split()
        if len(cols) >= 3 and not ln.lstrip().startswith("#") and cols[2] == "btrfs":
            if cols[1] == mountpoint:
                continue
            if (name is not None and uuid is not None and len(cols) >= 4
                    and cols[0] == f"UUID={uuid}" and _names_subvol(cols[3], name)):
                continue
        kept.append(ln)
    return kept


def write_fstab(path: str, text: str) -> None:
    """Replace *path* atomically: flushed + fsynced temp file, original mode
    kept, then rename — never an in-place rewrite."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".dasik-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
    except FileNotFoundError:
        os.chmod(tmp, 0o644)
    os.replace(tmp, path)
