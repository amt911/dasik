"""The disks as they really are, for a human about to choose one.

Deliberately not ``DiskPartitionAction._discover_disks``: that answers "what can
dasik represent?" and drops everything it cannot — ntfs, unformatted space, a
locked LUKS container. Those are exactly what a partitioning assistant must
show, because "this one has Windows on it and is mounted right now" is the most
important sentence on the screen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Only these are things you install onto. loop devices, CD-ROMs and the ISO's
# own squashfs are not offered.
_DISK_TYPE = "disk"
# …and neither are these, which lsblk still calls disks. QEMU hands every guest
# a 4 KiB /dev/fd0 that sorts FIRST, so the menu opened with a floppy selected
# and the wizard would have composed an ESP for it (seen on a VM run). A card
# reader with nothing in it reports size 0. The floor is low on purpose: it is
# for pseudo-devices, not for people with small disks.
_MIN_TARGET_BYTES = 1024 ** 3
_NOT_A_TARGET = ("fd",)

_UNITS = (("T", 1000 ** 4), ("G", 1000 ** 3), ("M", 1000 ** 2), ("K", 1000))


def human_size(size: int) -> str:
    """``1000204886016`` -> ``931.5G``, the way lsblk prints it.

    Base 1000, one decimal, trailing ``.0`` dropped — so a 512 MiB ESP reads
    ``512M`` rather than ``536.9M``... which it does not, because 536870912
    bytes IS 536.9 MB. lsblk itself uses base 1024 for the suffix-less form, so
    that is what this matches.
    """
    if not size:
        return "?"
    for suffix, factor in ((("T"), 1024 ** 4), (("G"), 1024 ** 3),
                           (("M"), 1024 ** 2), (("K"), 1024)):
        if size >= factor:
            value = size / factor
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{size}B"


@dataclass(frozen=True)
class PartitionInfo:
    """One partition, as lsblk sees it."""

    path: str
    size: int
    fstype: str
    label: str
    mountpoint: str

    @property
    def size_human(self) -> str:
        return human_size(self.size)

    def describe(self) -> str:
        bits = [self.path, self.size_human, self.fstype or "unformatted"]
        if self.label:
            bits.append(f"“{self.label}”")
        if self.mountpoint:
            bits.append(f"MOUNTED at {self.mountpoint}")
        return "  ".join(bits)


@dataclass(frozen=True)
class DiskInfo:
    """One whole disk, and what is currently on it."""

    path: str
    size: int
    pttype: str
    partitions: Tuple[PartitionInfo, ...] = ()

    @property
    def size_human(self) -> str:
        return human_size(self.size)

    @property
    def is_empty(self) -> bool:
        """No partition table and no partitions — safe to take."""
        return not self.partitions and not self.pttype

    @property
    def is_mounted(self) -> bool:
        """Anything on it is mounted right now. Never the disk to erase."""
        return any(p.mountpoint for p in self.partitions)

    def describe(self) -> str:
        if self.is_empty:
            state = "empty"
        else:
            kinds = [p.fstype for p in self.partitions if p.fstype]
            state = ", ".join(dict.fromkeys(kinds)) or f"{len(self.partitions)} partitions"
        line = f"{self.path}  {self.size_human}  {state}"
        return f"{line}  MOUNTED" if self.is_mounted else line


def _as_bytes(raw: Any) -> int:
    """lsblk's SIZE, which is an int with ``-b`` and ``931.5G`` without it.

    An un-parsed human size becomes 0 (unknown) rather than an exception: a
    payload someone pasted from their terminal should still list the disks.
    """
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def _partition(node: Dict[str, Any]) -> PartitionInfo:
    return PartitionInfo(
        path=node.get("path") or f"/dev/{node.get('name', '')}",
        size=_as_bytes(node.get("size")),
        fstype=node.get("fstype") or "",
        label=node.get("label") or "",
        mountpoint=node.get("mountpoint") or "",
    )


def _mountpoint_of(node: Dict[str, Any]) -> str:
    """A partition's mountpoint, or that of whatever it holds.

    A LUKS partition is never mounted itself; the mapper inside it is, and a
    disk whose only mount lives one level down is still very much in use.
    """
    if node.get("mountpoint"):
        return str(node["mountpoint"])
    for child in node.get("children") or []:
        found = _mountpoint_of(child)
        if found:
            return found
    return ""


def parse_lsblk(data: Any) -> List[DiskInfo]:
    """``lsblk -J`` output -> the disks, in the order lsblk listed them."""
    if not isinstance(data, dict):
        return []
    devices = data.get("blockdevices")
    if not isinstance(devices, list):
        return []

    disks: List[DiskInfo] = []
    for node in devices:
        if not isinstance(node, dict) or node.get("type") != _DISK_TYPE:
            continue
        name = str(node.get("name") or "")
        raw_size = node.get("size")
        size = _as_bytes(raw_size)
        # "unknown" and "zero" are different answers. A dump taken WITHOUT -b
        # reports `931.5G`, which this cannot parse — dropping those would make
        # `--from-lsblk` silently lose every disk in the file. Only a size lsblk
        # really reported as a number is allowed to disqualify a device.
        known = isinstance(raw_size, int) and not isinstance(raw_size, bool) or (
            isinstance(raw_size, str) and raw_size.isdigit())
        if name.startswith(_NOT_A_TARGET):
            continue
        if known and size < _MIN_TARGET_BYTES:
            continue
        partitions = []
        for child in node.get("children") or []:
            if not isinstance(child, dict) or child.get("type") != "part":
                continue
            info = _partition(child)
            mount = _mountpoint_of(child)
            if mount and not info.mountpoint:
                info = PartitionInfo(info.path, info.size, info.fstype,
                                     info.label, mount)
            partitions.append(info)
        disks.append(DiskInfo(
            path=node.get("path") or f"/dev/{name}",
            size=size,
            pttype=node.get("pttype") or "",
            partitions=tuple(partitions),
        ))
    return disks


def read_inventory(runner: Optional[Any] = None) -> List[DiskInfo]:
    """The live inventory, via ``lsblk -J``. Empty list when it cannot be read.

    *runner* is the callable that runs the command (defaults to
    ``Command.execute``), so the wizard can be driven with a recorded payload.
    """
    if runner is None:
        from ..command_worker.command_worker import Command
        runner = Command.execute
    try:
        result = runner("lsblk", ["-J", "-b", "-o",
                                  "NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,PTTYPE"])
    except Exception:      # nosec B110 - no lsblk means no inventory to show
        return []
    out = getattr(result, "stdout", b"") or b""
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    try:
        return parse_lsblk(json.loads(out or "{}"))
    except json.JSONDecodeError:
        return []
