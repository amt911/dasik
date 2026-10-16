"""Action: day-2 add / move / remove of btrfs subvolumes on an installed machine.

The install path (``DiskPartitionAction``) creates and mounts the declared
subvolumes once, on a fresh disk. This action is what makes the same
``btrfs_subvolumes`` list keep working afterwards: it probes the HOST (findmnt,
``btrfs subvolume list``) plus the target's ``/etc/fstab`` and lets the pure
``btrfs_subvol_state.diff`` decide what differs. A subvolume is keyed
``"<partition label>:<name>"``.

Only disks ``DiskPartitionAction`` already judges converged are looked at: on a
fresh disk the install path owns the whole layout and this action stays silent.
Removal is gated on ownership (``managed``) like everywhere else, and a dropped
``disks`` block yields no wanted subvolumes and no declared labels, so nothing
is ever removed because the block vanished.

``Change.reason`` is display text only; ``apply`` re-probes per item itself.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional, Set

from .abstract_action import AbstractAction
from .btrfs_subvol_state import (
    Live,
    Wanted,
    diff,
    parse_findmnt,
    parse_fstab_subvols,
    parse_subvolume_list,
)
from .disk_partition_action import DiskPartitionAction
from ..command_worker.command_worker import Command
from ..models.disk_model import FileSystemType
from ..state.change import Change, Op

_FSTAB = "/etc/fstab"


def _text(result: Any) -> str:
    out = getattr(result, "stdout", "") or ""
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)


class BtrfsSubvolumeAction(AbstractAction):
    """Reconcile declared btrfs subvolumes with what is mounted and listed."""

    _DOMAIN = "btrfs_subvolumes"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._disks = DiskPartitionAction(cfg.get("disks"), context)

    @property
    def name(self) -> str:
        return "Btrfs Subvolumes"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    # --- paths ----------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else "/mnt" + canonical

    def _canonical(self, host_path: str) -> Optional[str]:
        """Host path -> path inside the target, or None when outside it."""
        target = self._target()
        root = target.root.rstrip("/") if target is not None else "/mnt"
        if not root:
            return host_path
        if host_path == root:
            return "/"
        if host_path.startswith(root + "/"):
            return host_path[len(root):]
        return None

    # --- the declaration -------------------------------------------------- #

    def _btrfs_partitions(self):
        for disk in self._disks.disks:
            for part in disk.partitions:
                if part.filesystem == FileSystemType.BTRFS and part.btrfs_subvolumes:
                    yield disk, part

    def _wanted(self) -> List[Wanted]:
        return [Wanted(f"{p.label}:{s.name}", p.label, s.name, s.mountpoint)
                for _d, p in self._btrfs_partitions() for s in p.btrfs_subvolumes]

    # --- probing the machine ---------------------------------------------- #

    def _read_fstab(self) -> str:
        try:
            with open(self._p(_FSTAB), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _host(self, cmd: str, args: List[str]) -> str:
        try:
            return _text(Command.execute(cmd, args))
        except Exception:
            return ""

    def _live(self) -> Dict[str, Live]:
        """Partition label -> its filesystem's state, for converged disks only."""
        if self._target() is None:
            return {}
        parts = [(d, p) for d, p in self._btrfs_partitions()
                 if self._disks._disk_converged(d)]
        if not parts:
            return {}
        rows = []
        for r in parse_findmnt(self._host(
                "findmnt", ["-rn", "-t", "btrfs", "-o", "TARGET,SOURCE,UUID,OPTIONS"])):
            canon = self._canonical(r.target)
            if canon is not None:
                rows.append((r, canon))
        fstab = parse_fstab_subvols(self._read_fstab())
        live: Dict[str, Live] = {}
        for _disk, part in parts:
            names = {s.name: s.mountpoint for s in part.btrfs_subvolumes}
            uuids = {r.uuid for r, c in rows if names.get(r.subvol) == c}
            if not uuids:   # every declared subvolume is somewhere else (moves)
                uuids = {r.uuid for r, _c in rows if r.subvol in names}
            if len(uuids) != 1:
                continue    # not mounted, or ambiguous: not our call yet
            uuid = uuids.pop()
            mine = [(r, c) for r, c in rows if r.uuid == uuid]
            listing = self._host("btrfs", ["subvolume", "list", mine[0][0].target])
            live[part.label] = Live(
                mounted={r.subvol: c for r, c in mine},
                fstab=fstab,
                existing=parse_subvolume_list(listing),
            )
        return live

    # --- v3 contract ------------------------------------------------------ #

    def actual(self) -> Set[str]:
        live = self._live()
        out: Set[str] = set()
        for w in self._wanted():
            lv = live.get(w.label)
            if (lv is not None and lv.mounted.get(w.name) == w.mountpoint
                    and lv.fstab.get(w.mountpoint) == w.name):
                out.add(w.key)
        return out

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def _would_delete_contents(self, mountpoint: str) -> bool:
        path = self._p(mountpoint)
        try:
            return (os.path.isdir(path) and not os.path.ismount(path)
                    and bool(os.listdir(path)))
        except OSError:
            return False

    def plan(self, managed: Any) -> List[Change]:
        wanted = self._wanted()
        mountpoints = {w.key: w.mountpoint for w in wanted}
        declared = {p.label for _d, p in self._btrfs_partitions()}
        changes: List[Change] = []
        for op, key, reason in diff(wanted, self._live(), declared, list(managed or [])):
            destructive = (op is Op.INSTALL and key in mountpoints
                           and self._would_delete_contents(mountpoints[key]))
            changes.append(Change(self._DOMAIN, op, key, reason=reason,
                                  destructive=destructive))
        return changes

    def apply(self, changes) -> None:
        """Mechanics arrive in Task 3 of the day-2 btrfs plan."""
        return None

    def import_state(self, managed=None) -> dict:
        """Nothing: subvolumes are a property of a PARTITION, and the ``disks``
        block has exactly one author — ``DiskPartitionAction.import_state``.
        Two actions emitting ``disks`` would clobber each other, since
        ``ConfigWriter.merge`` overwrites a key rather than merging two halves
        of one."""
        return {}
