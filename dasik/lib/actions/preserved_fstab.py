"""fstab lines for the encrypted volumes a reinstall keeps.

The install writes /etc/fstab from ``genfstab -U <target>``, which only sees
what is mounted under the target. A preserved LUKS volume (``format: false`` on
a disk without ``wipe_disk``) is closed on the live ISO, so none of its
mountpoints reached the installed fstab: the initramfs unlocked it at boot
(``rd.luks.name``) and nothing mounted it.

The lines name ``/dev/mapper/<luks_name>`` — the mapping dasik itself creates
through ``rd.luks.name``/crypttab — so they are written without unlocking the
volume. A mountpoint genfstab already wrote (the volume was opened and mounted
by hand before installing) is left to genfstab.
"""
from typing import Any, Dict, Iterator, List, Set

from .disk_partition_action import DiskPartitionAction
from .partition_utils import merge_mount_options_by_name


def _preserved_volumes(config: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    disks = config.get("disks", {})
    if not isinstance(disks, dict):
        return
    for disk in disks.get("disks", []):
        if disk.get("wipe_disk"):
            continue
        for part in disk.get("partitions", []):
            if part.get("encrypt") and part.get("format") is False and part.get("luks_name"):
                yield part


def _fstab_mountpoints(fstab: str) -> Set[str]:
    out: Set[str] = set()
    for raw in fstab.splitlines():
        fields = raw.split()
        if len(fields) >= 2 and not fields[0].startswith("#"):
            out.add(fields[1])
    return out


def preserved_fstab_lines(config: Dict[str, Any], fstab: str) -> List[str]:
    """The fstab lines genfstab could not write for preserved encrypted volumes."""
    taken = _fstab_mountpoints(fstab)
    lines: List[str] = []
    for part in _preserved_volumes(config):
        device = f"/dev/mapper/{part['luks_name']}"
        fs = str(part.get("filesystem") or "")
        fstype = DiskPartitionAction._FSTYPE_ALIASES.get(fs, fs)
        base = list(part.get("mount_options") or [])
        # `nofail` on the VOLUME (luks_options) makes it optional for the boot;
        # its mounts must be optional too, or a missing disk still ends in the
        # emergency shell.
        tail = ["nofail"] if "nofail" in (part.get("luks_options") or []) else []
        subvols = part.get("btrfs_subvolumes") or []
        if fs == "btrfs" and subvols:
            for sv in subvols:
                mp = sv.get("mountpoint")
                if not mp or mp == "/" or mp in taken:
                    continue
                opts = merge_mount_options_by_name(base, list(sv.get("mount_options") or []))
                opts.append(f"subvol={sv['name']}")
                opts += [t for t in tail if t not in opts]
                lines.append(f"{device} {mp} btrfs {','.join(opts)} 0 0")
            continue
        mp = part.get("mountpoint")
        if not mp or mp == "/" or mp in taken or fstype == "swap":
            continue
        passno = "0" if fs == "btrfs" else "2"
        opts = base + [t for t in tail if t not in base]
        lines.append(f"{device} {mp} {fstype} {','.join(opts) or 'defaults'} 0 {passno}")
    return lines
