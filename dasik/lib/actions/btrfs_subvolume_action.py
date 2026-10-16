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
import shutil
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

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
from ..exceptions.exceptions import CommandExecutionError, SubvolumeChangeRefused
from ..logging import run_logger
from ..models.disk_model import FileSystemType
from ..state.change import Change, Op

_FSTAB = "/etc/fstab"
_TOP = "/run/dasik-btrfs-top"


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
        # label -> (block device, filesystem UUID), refreshed by every _live()
        self._fs: Dict[str, Tuple[str, str]] = {}

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
        except Exception as exc:
            run_logger.get().warning(
                f"btrfs probe `{cmd}` failed; treating its output as empty",
                detail=str(exc))
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
        # First pass: the filesystem that already carries a declared subvolume
        # at its declared mountpoint. Claimed UUIDs are off-limits to the move
        # fallback, so a common name like `@` on a second, unmounted partition
        # cannot borrow another partition's filesystem.
        first: Dict[str, Set[str]] = {}
        for _disk, part in parts:
            names = {s.name: s.mountpoint for s in part.btrfs_subvolumes}
            first[part.label] = {r.uuid for r, c in rows if names.get(r.subvol) == c}
        claimed = {u for us in first.values() for u in us}
        for _disk, part in parts:
            names = {s.name: s.mountpoint for s in part.btrfs_subvolumes}
            uuids = set(first[part.label])
            if not uuids:   # every declared subvolume is somewhere else (moves)
                for u in {r.uuid for r, _c in rows if r.subvol in names} - claimed:
                    here = {r.subvol for r, _c in rows if r.uuid == u}
                    if set(names) <= here:   # stricter: all declared names present
                        uuids.add(u)
            if len(uuids) > 1:
                run_logger.get().warning(
                    f"btrfs partition {part.label}: several mounted filesystems "
                    "match its subvolumes; skipping it",
                    detail=", ".join(sorted(uuids)))
                continue
            if not uuids:
                continue    # not mounted: not our call yet
            uuid = uuids.pop()
            mine = [(r, c) for r, c in rows if r.uuid == uuid]
            self._fs[part.label] = (mine[0][0].device, uuid)
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

    # --- apply ------------------------------------------------------------ #

    _PROC = "/proc"

    @contextmanager
    def _top_level_mount(self, device: str) -> Iterator[str]:
        """Mount the filesystem's top level (subvolid=5) on the host; ALWAYS
        unmount and remove the directory again, even when the body raises."""
        top = self._p(_TOP)
        os.makedirs(top, exist_ok=True)
        Command.execute("mount", ["-o", "subvolid=5", device, top], check=True)
        try:
            yield top
        finally:
            res = Command.execute("umount", [top])
            if getattr(res, "returncode", 0) != 0:
                run_logger.get().warning(
                    f"could not unmount the btrfs top level at {top}")
            try:
                os.rmdir(top)   # never rmtree: still mounted/non-empty = left alone
            except OSError:
                pass

    def _busy_pids(self, host_path: str, proc: Optional[str] = None) -> List[int]:
        """Pids whose cwd or an open fd lies at or under *host_path*."""
        proc = proc or self._PROC
        base = os.path.normpath(host_path)

        def under(link: str) -> bool:
            link = os.path.normpath(link.removesuffix(" (deleted)"))
            return link == base or link.startswith(base.rstrip("/") + "/")

        pids: List[int] = []
        try:
            entries = os.listdir(proc)
        except OSError:
            return pids
        for entry in entries:
            if not entry.isdigit():
                continue
            links = [os.path.join(proc, entry, "cwd")]
            fd_dir = os.path.join(proc, entry, "fd")
            try:
                links += [os.path.join(fd_dir, fd) for fd in os.listdir(fd_dir)]
            except OSError:
                pass
            for link in links:
                try:
                    if under(os.readlink(link)):
                        pids.append(int(entry))
                        break
                except OSError:
                    continue
        return sorted(pids)

    def _comm(self, pid: int) -> str:
        try:
            with open(os.path.join(self._PROC, str(pid), "comm"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "?"

    def _assert_idle(self, host_path: str, what: str) -> None:
        pids = self._busy_pids(host_path)
        if pids:
            who = ", ".join(f"pid {p} ({self._comm(p)})" for p in pids)
            raise SubvolumeChangeRefused(
                f"{what}: {host_path} is in use by {who}. Stop those processes "
                "(or close their files) and run dasik again; nothing was changed.")

    @staticmethod
    def _has_content(path: str) -> bool:
        try:
            return os.path.isdir(path) and bool(os.listdir(path))
        except OSError:
            return False

    @staticmethod
    def _empty_dir(path: str) -> None:
        """Delete the entries of *path*, not the directory itself."""
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            if os.path.isdir(full) and not os.path.islink(full):
                shutil.rmtree(full)
            else:
                os.unlink(full)

    def _copy(self, src: str, dst: str) -> None:
        """Copy the CONTENTS of *src* into *dst*: reflink first (instant, no
        extra space), else a plain copy when the free space allows it."""
        os.makedirs(dst, exist_ok=True)
        args = [src.rstrip("/") + "/.", dst.rstrip("/") + "/"]
        res = Command.execute("cp", ["-a", "--reflink=always", *args])
        if getattr(res, "returncode", 0) == 0:
            return
        need = None
        du = Command.execute("du", ["-sb", src])
        if getattr(du, "returncode", 0) == 0:
            try:
                need = int(_text(du).split()[0])
            except (IndexError, ValueError):
                need = None
        st = os.statvfs(dst)
        free = st.f_bavail * st.f_frsize
        if need is None or need > free:
            raise SubvolumeChangeRefused(
                f"not enough free space to copy {src} into {dst} without "
                f"reflinks (needs {need if need is not None else 'an unknown amount'} "
                f"bytes, {free} free). Free some space and run dasik again; "
                "nothing was deleted.")
        res = Command.execute("cp", ["-a", *args], check=True)
        if getattr(res, "returncode", 0) != 0:
            raise CommandExecutionError(f"copying {src} into {dst} failed")

    def _fstab_line(self, uuid: str, part, subvol) -> str:
        opts = ",".join(DiskPartitionAction._subvol_mount_options(part, subvol))
        return f"UUID={uuid} {subvol.mountpoint} btrfs {opts} 0 0"

    def _fstab_drop(self, mountpoint: str) -> None:
        """Drop the btrfs lines for *mountpoint*; any other line stays."""
        current = self._read_fstab()
        kept = []
        for ln in current.splitlines():
            cols = ln.split()
            if len(cols) >= 3 and not ln.lstrip().startswith("#") \
                    and cols[1] == mountpoint and cols[2] == "btrfs":
                continue
            kept.append(ln)
        if len(kept) == len(current.splitlines()):
            return
        with open(self._p(_FSTAB), "w", encoding="utf-8") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))

    def _fstab_set(self, mountpoint: str, line: str) -> None:
        self._fstab_drop(mountpoint)
        path = self._p(_FSTAB)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._read_fstab()
        with open(path, "a", encoding="utf-8") as f:
            if current and not current.endswith("\n"):
                f.write("\n")
            f.write(line + "\n")

    @staticmethod
    def _parent(live: Live, name: str, mountpoint: str) -> Tuple[str, str]:
        """(parent subvolume, path of *mountpoint* inside it): the mounted
        subvolume whose mountpoint is the longest prefix of *mountpoint*."""
        best: Optional[Tuple[str, str]] = None
        for n, mp in live.mounted.items():
            if n == name or mp == mountpoint:
                continue
            if mp == "/" or mountpoint.startswith(mp.rstrip("/") + "/"):
                if best is None or len(mp) > len(best[1]):
                    best = (n, mp)
        if best is None:
            raise SubvolumeChangeRefused(
                f"{mountpoint}: no mounted subvolume contains it; nothing was changed.")
        return best[0], os.path.relpath(mountpoint, best[1])

    def _mount_new(self, device: str, part, subvol) -> None:
        host_mp = self._p(subvol.mountpoint)
        os.makedirs(host_mp, exist_ok=True)
        opts = ",".join(DiskPartitionAction._subvol_mount_options(part, subvol))
        Command.execute("mount", ["-o", opts, device, host_mp], check=True)

    def _install(self, live: Live, device: str, uuid: str, part, subvol) -> None:
        name, mp = subvol.name, subvol.mountpoint
        line = self._fstab_line(uuid, part, subvol)
        if live.mounted.get(name) == mp:                       # fstab-only
            self._fstab_set(mp, line)
            return
        host_mp = self._p(mp)
        old = live.mounted.get(name)
        if old is not None:                                     # move
            if old == "/":
                raise SubvolumeChangeRefused(
                    f"{name} is the root subvolume; dasik never moves it.")
            if os.path.ismount(host_mp):
                raise SubvolumeChangeRefused(
                    f"{mp} is already a mountpoint; unmount it first.")
            self._assert_idle(self._p(old), f"moving {name}")
            Command.execute("umount", [self._p(old)], check=True)
            self._fstab_drop(old)
            self._fstab_set(mp, line)
            self._mount_new(device, part, subvol)
            return
        if os.path.ismount(host_mp):
            raise SubvolumeChangeRefused(
                f"{mp} is already a mountpoint; unmount it or pick another "
                "path. Nothing was changed.")
        parent, rel = self._parent(live, name, mp)
        self._assert_idle(host_mp, f"adding {name}")
        with self._top_level_mount(device) as top:
            src = os.path.join(top, parent, rel)
            dst = os.path.join(top, name)
            if os.path.isdir(src) and Command.execute(
                    "btrfs", ["subvolume", "show", src]).returncode == 0:
                raise SubvolumeChangeRefused(
                    f"{mp} is itself a btrfs subvolume (nested); remove or move "
                    "it first. Nothing was changed.")
            adopt = name in live.existing
            has = self._has_content(src)
            if adopt and has and self._has_content(dst):
                raise SubvolumeChangeRefused(
                    f"{mp} and the existing subvolume {name} both hold data; "
                    "empty one of them and run dasik again. Nothing was changed.")
            if not adopt:
                Command.execute("btrfs", ["subvolume", "create", dst], check=True)
            if has:
                attrs = self._host("lsattr", ["-d", src]).split()
                if attrs and "C" in attrs[0] and not adopt:
                    Command.execute("chattr", ["+C", dst], check=True)
                self._copy(src, dst)
                self._empty_dir(src)
        self._mount_new(device, part, subvol)
        self._fstab_set(mp, line)

    def _remove(self, live: Live, device: str, name: str) -> None:
        mp = live.mounted.get(name)
        if mp is None:
            for m, n in live.fstab.items():
                if n == name:
                    mp = m
        if mp is None:
            raise SubvolumeChangeRefused(
                f"{name}: cannot tell where it was mounted, so its data would "
                "have nowhere to go. Nothing was deleted.")
        if mp == "/":
            raise SubvolumeChangeRefused(f"{name} is the root subvolume (mounted at /); dasik never removes it.")
        parent, rel = self._parent(live, name, mp)
        host_mp = self._p(mp)
        if name in live.mounted:
            self._assert_idle(host_mp, f"removing {name}")
        with self._top_level_mount(device) as top:
            src = os.path.join(top, name)
            dst = os.path.join(top, parent, rel)
            nested = self._host("btrfs", ["subvolume", "list", "-o", src]).strip()
            if nested:
                raise SubvolumeChangeRefused(
                    f"{name} contains nested subvolumes:\n{nested}\nRemove them "
                    "first. Nothing was deleted.")
            if self._has_content(dst):
                raise SubvolumeChangeRefused(
                    f"{mp} is not empty inside its parent subvolume; empty it so "
                    "the data of the subvolume can be copied back. Nothing was deleted.")
            if name in live.mounted:
                Command.execute("umount", [host_mp], check=True)
            self._copy(src, dst)
            self._fstab_drop(mp)
            Command.execute("btrfs", ["subvolume", "delete", src], check=True)

    def apply(self, changes) -> None:
        """Re-probe, then act per item: the live state decides create / adopt /
        fstab-only / move; ``Change.reason`` is display text and never read."""
        live = self._live()
        models = {f"{p.label}:{s.name}": (p, s)
                  for _d, p in self._btrfs_partitions() for s in p.btrfs_subvolumes}
        for c in changes:
            label, _, name = c.item.partition(":")
            lv = live.get(label)
            if lv is None or label not in self._fs:
                raise SubvolumeChangeRefused(
                    f"{c.item}: its filesystem is not mounted; nothing was changed.")
            device, uuid = self._fs[label]
            if c.op is Op.REMOVE:
                self._remove(lv, device, name)
                continue
            if c.item not in models:
                raise SubvolumeChangeRefused(f"{c.item} is not declared; nothing was changed.")
            part, subvol = models[c.item]
            if subvol.mountpoint == "/":
                raise SubvolumeChangeRefused(
                    f"{c.item} is declared at /; dasik never touches the root "
                    "mount. Nothing was changed.")
            self._install(lv, device, uuid, part, subvol)

    def import_state(self, managed=None) -> dict:
        """Nothing: subvolumes are a property of a PARTITION, and the ``disks``
        block has exactly one author — ``DiskPartitionAction.import_state``.
        Two actions emitting ``disks`` would clobber each other, since
        ``ConfigWriter.merge`` overwrites a key rather than merging two halves
        of one."""
        return {}
