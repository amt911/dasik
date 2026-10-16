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
import re
import shutil
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from .abstract_action import AbstractAction
from .btrfs_subvol_fstab import without_btrfs_lines, write_fstab
from .btrfs_subvol_state import (
    Live,
    Wanted,
    diff,
    parse_findmnt,
    parse_fstab_subvols,
    parse_mount_targets,
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
_STAGING = ".dasik-moving"
# Anything that would split an fstab column or an option list, or be a control byte.
_UNSAFE = re.compile(r"[\s,\x00-\x1f\x7f]")


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
        self._fstab_changed = False

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

    @staticmethod
    def _strict(cmd: str, args: List[str]) -> str:
        """A probe a destructive guard depends on: a failure is a refusal, never
        'nothing found'."""
        try:
            res = Command.execute(cmd, args, check=True)
        except Exception as exc:
            raise SubvolumeChangeRefused(
                f"`{cmd} {' '.join(args)}` failed ({exc}), so it cannot be "
                "checked whether this is safe. Nothing was changed.") from exc
        if getattr(res, "returncode", 0) != 0:
            raise SubvolumeChangeRefused(
                f"`{cmd} {' '.join(args)}` exited {res.returncode}, so it cannot "
                "be checked whether this is safe. Nothing was changed.")
        return _text(res)

    def _mount_rows(self):
        """(row, canonical mountpoint) for every btrfs mount inside the target."""
        rows = []
        for r in parse_findmnt(self._host(
                "findmnt", ["-rn", "-t", "btrfs", "-o", "TARGET,SOURCE,UUID,OPTIONS"])):
            canon = self._canonical(r.target)
            if canon is not None:
                rows.append((r, canon))
        return rows

    def _label_of(self, device: str) -> str:
        lines = self._host("lsblk", ["-no", "LABEL", device]).strip().splitlines()
        return lines[0].strip() if lines else ""

    def _reality(self) -> Dict[str, str]:
        """``<label>:<name>`` -> mountpoint, for every top-level subvolume that
        is mounted AND has its fstab line, except the one at ``/``. Independent
        of the config: ownership must survive a sync whose config omits it."""
        if self._target() is None:
            return {}
        fstab = parse_fstab_subvols(self._read_fstab())
        labels: Dict[str, str] = {}
        out: Dict[str, str] = {}
        for r, canon in self._mount_rows():
            if canon == "/" or "/" in r.subvol or fstab.get(canon) != r.subvol:
                continue
            if r.device not in labels:
                labels[r.device] = self._label_of(r.device)
            if labels[r.device]:
                out[f"{labels[r.device]}:{r.subvol}"] = canon
        return out

    def _live(self) -> Dict[str, Live]:
        """Partition label -> its filesystem's state, for converged disks only."""
        if self._target() is None:
            return {}
        converged = self._disks.actual()
        parts = [(d, p) for d, p in self._btrfs_partitions() if d.device in converged]
        if not parts:
            return {}
        rows = self._mount_rows()
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
        """Reality, not the declaration (see ``_reality``): ``Reconciler`` keeps
        ``actual() & (managed | declared)``, so an actual() limited to what the
        config declares would dispossess a subvolume the config stopped listing."""
        return set(self._reality())

    def managed_keys(self) -> dict:
        """Only DECLARED items: an apply never claims a hand-made subvolume."""
        real = self._reality()
        return {self._DOMAIN: sorted(
            w.key for w in self._wanted() if real.get(w.key) == w.mountpoint)}

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

    def _fstab_write(self, text: str) -> None:
        write_fstab(self._p(_FSTAB), text)
        self._fstab_changed = True

    def _fstab_drop(self, mountpoint: str) -> None:
        """Drop the btrfs lines for *mountpoint*; any other line stays."""
        current = self._read_fstab()
        kept = without_btrfs_lines(current, mountpoint)
        if len(kept) == len(current.splitlines()):
            return
        self._fstab_write("\n".join(kept) + ("\n" if kept else ""))

    def _fstab_set(self, mountpoint: str, line: str, name: str, uuid: str) -> None:
        """Make *line* the one btrfs line for *mountpoint* — and for the
        subvolume *name* of filesystem *uuid*, wherever it was mounted before."""
        kept = without_btrfs_lines(self._read_fstab(), mountpoint, name, uuid)
        self._fstab_write("\n".join(kept + [line]) + "\n")

    def _fstab_options(self, mountpoint: str, name: str) -> str:
        for ln in self._read_fstab().splitlines():
            cols = ln.split()
            if len(cols) >= 4 and cols[1] == mountpoint and cols[2] == "btrfs":
                return cols[3]
        return f"subvol={name}"

    # --- vetting ---------------------------------------------------------- #

    @staticmethod
    def _vet_declaration(name: str, mountpoint: Optional[str]) -> None:
        """Config-derived strings become paths: refuse any that could leave
        the directory they are meant for."""
        for what, value in (("subvolume name", name), ("mountpoint", mountpoint)):
            if value is not None and _UNSAFE.search(value):
                raise SubvolumeChangeRefused(
                    f"{what} {value!r} contains whitespace, a comma or a control "
                    "character, which would corrupt /etc/fstab. Nothing was changed.")
        if not name or "/" in name or ".." in name.split("/") or name in (".", ".."):
            raise SubvolumeChangeRefused(
                f"subvolume name {name!r} must be a plain name (no '/' and no "
                "'..'). Nothing was changed.")
        if mountpoint is not None and (
                not mountpoint.startswith("/")
                or os.path.normpath(mountpoint) != mountpoint):
            raise SubvolumeChangeRefused(
                f"mountpoint {mountpoint!r} of {name} must be an absolute, "
                "normalized path (no '..', no '//', no trailing '/'). "
                "Nothing was changed.")

    @staticmethod
    def _inside(top: str, path: str) -> str:
        root = os.path.realpath(top)
        if os.path.commonpath([root, os.path.realpath(path)]) != root:
            raise SubvolumeChangeRefused(
                f"{path} resolves outside the btrfs top level {top}; "
                "nothing was changed.")
        return path

    def _subvolumes_below(self, top: str, rel_path: str) -> List[str]:
        """Subvolumes (any depth) whose path lies under *rel_path* (top-relative)."""
        prefix = rel_path.rstrip("/") + "/"
        out = []
        for line in self._strict("btrfs", ["subvolume", "list", top]).splitlines():
            _, sep, path = line.partition(" path ")
            if sep and (path.strip().startswith(prefix)
                        or path.strip() == rel_path.rstrip("/")):
                out.append(path.strip())
        return out

    def _assert_nothing_mounted_below(self, host_mp: str, what: str) -> None:
        """A mount over *host_mp* would hide whatever is mounted beneath it."""
        prefix = host_mp.rstrip("/") + "/"
        for t in parse_mount_targets(self._strict("findmnt", ["-rn", "-o", "TARGET"])):
            if t.startswith(prefix):
                raise SubvolumeChangeRefused(
                    f"{what}: {t} is mounted below {host_mp}; mounting there "
                    "would hide it. Unmount it or mount that one after this "
                    "subvolume. Nothing was changed.")

    def _vet_location(self, live: Live, top: str, name: str, mp: str) -> str:
        """The parent-side directory for *mp* inside the top-level mount, after
        refusing a nested subvolume at or below it."""
        parent, rel = self._parent(live, name, mp)
        src = self._inside(top, os.path.join(top, parent, rel))
        if os.path.isdir(src) and Command.execute(
                "btrfs", ["subvolume", "show", src]).returncode == 0:
            raise SubvolumeChangeRefused(
                f"{mp} is itself a btrfs subvolume (nested); remove or move "
                "it first. Nothing was changed.")
        below = self._subvolumes_below(top, f"{parent}/{rel}")
        if below:
            raise SubvolumeChangeRefused(
                f"{mp} contains nested subvolumes ({', '.join(below)}); copying "
                "would flatten and delete them. Remove or move them first. "
                "Nothing was changed.")
        return src

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
            self._fstab_set(mp, line, name, uuid)
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
            self._assert_nothing_mounted_below(host_mp, f"moving {name}")
            with self._top_level_mount(device) as top:
                target = self._vet_location(live, top, name, mp)
                if self._has_content(target):
                    raise SubvolumeChangeRefused(
                        f"{mp} is not empty inside its parent subvolume; mounting "
                        f"{name} there would hide that data. Empty it and run "
                        "dasik again. Nothing was changed.")
            self._assert_idle(self._p(old), f"moving {name}")
            Command.execute("umount", [self._p(old)], check=True)
            self._fstab_drop(old)
            self._fstab_set(mp, line, name, uuid)
            self._mount_new(device, part, subvol)
            return
        if os.path.ismount(host_mp):
            raise SubvolumeChangeRefused(
                f"{mp} is already a mountpoint; unmount it or pick another "
                "path. Nothing was changed.")
        self._assert_nothing_mounted_below(host_mp, f"adding {name}")
        self._assert_idle(host_mp, f"adding {name}")
        with self._top_level_mount(device) as top:
            src = self._vet_location(live, top, name, mp)
            dst = self._inside(top, os.path.join(top, name))
            self._fill_subvolume(live, src, dst, host_mp, name)
        if self._has_content(host_mp):
            raise SubvolumeChangeRefused(
                f"files appeared at {mp} while {name} was being filled (a process "
                f"opened a path there); mounting would hide them. {name} holds the "
                "copy and stays unmounted with no fstab line, so the next plan shows "
                f"it as an adopt and refuses while both hold data. Move those files "
                "away (see ls " + host_mp + ") and run dasik again.")
        self._mount_new(device, part, subvol)
        self._fstab_set(mp, line, name, uuid)

    def _fill_subvolume(self, live: Live, src: str, dst: str, host_mp: str,
                        name: str) -> None:
        """Create (or adopt) the subvolume *dst* and move the contents of the
        directory *src* into it.

        *src* is renamed aside FIRST (atomic, same subvolume) and the copy is
        taken from the renamed directory, so anything written to the path
        meanwhile lands in the fresh empty *src* and is never deleted. Only after
        a successful copy is the renamed directory removed. A failure puts it
        back and removes whatever this item created in *dst*.
        """
        staging = src + _STAGING
        if os.path.lexists(staging):
            raise SubvolumeChangeRefused(
                f"{staging} is left over from an interrupted move; check it, "
                "merge or remove it, and run dasik again. Nothing was changed.")
        adopt = name in live.existing
        has = self._has_content(src)
        if adopt and has and self._has_content(dst):
            raise SubvolumeChangeRefused(
                f"{host_mp} and the existing subvolume {name} both hold data; "
                "empty one of them and run dasik again. Nothing was changed.")
        created = False
        try:
            if not adopt:
                Command.execute("btrfs", ["subvolume", "create", dst], check=True)
                created = True
            if not os.path.isdir(src) or (adopt and not has):
                return      # nothing to carry (an adopted root keeps its own metadata)
            if not adopt:
                attrs = self._host("lsattr", ["-d", src]).split()
                if attrs and "C" in attrs[0]:
                    Command.execute("chattr", ["+C", dst], check=True)
            self._assert_idle(host_mp, f"adding {name}")    # right before the rename
            os.rename(src, staging)
            os.mkdir(src)
            try:
                # Always copied, even when empty: `cp -a src/. dst/` stamps the
                # directory's own owner, mode and ACLs onto the new subvolume.
                self._copy(staging, dst)
            except BaseException:
                self._unstage(src, staging)
                raise
        except BaseException:
            self._discard_partial(dst, created, adopt and has)
            raise
        # COMMITTED: the copy returned 0, dst holds everything. Nothing below may
        # touch dst, whatever happens to the old copy.
        if os.path.lexists(staging):
            try:
                shutil.rmtree(staging)
            except Exception as exc:    # noqa: BLE001 - the data is safe in dst
                run_logger.get().warning(
                    f"could not delete the old copy at {staging}; the data is "
                    f"safe in the new subvolume {name}. Remove it by hand",
                    detail=str(exc))

    @staticmethod
    def _unstage(src: str, staging: str) -> None:
        """Put the renamed-aside directory back where it was."""
        try:
            os.rmdir(src)
        except OSError as exc:
            raise SubvolumeChangeRefused(
                f"the copy failed and {src} received new files meanwhile; your "
                f"original data is intact in {staging}. Merge it back by hand."
            ) from exc
        os.rename(staging, src)

    def _discard_partial(self, dst: str, created: bool, emptiable: bool) -> None:
        """After a failed move-in: delete the subvolume THIS item created (it
        holds only a partial copy), or empty an adopted one (it was verified
        empty before the copy)."""
        try:
            if created:
                Command.execute("btrfs", ["subvolume", "delete", dst], check=True)
            elif emptiable and os.path.isdir(dst):
                self._empty_dir(dst)
        except Exception as exc:    # noqa: BLE001 - the original error matters more
            run_logger.get().warning(
                f"could not clean up the partial copy in {dst}; remove it by hand",
                detail=str(exc))

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
            raise SubvolumeChangeRefused(
                f"{name} is the root subvolume (mounted at /); dasik never removes it.")
        self._vet_declaration(name, mp)
        parent, rel = self._parent(live, name, mp)
        host_mp = self._p(mp)
        if name in live.mounted:
            self._assert_idle(host_mp, f"removing {name}")
        with self._top_level_mount(device) as top:
            src = self._inside(top, os.path.join(top, name))
            dst = self._inside(top, os.path.join(top, parent, rel))
            nested = self._strict("btrfs", ["subvolume", "list", "-o", src]).strip()
            if nested:
                raise SubvolumeChangeRefused(
                    f"{name} contains nested subvolumes:\n{nested}\nRemove them "
                    "first. Nothing was deleted.")
            if self._has_content(dst):
                raise SubvolumeChangeRefused(
                    f"{mp} is not empty inside its parent subvolume; empty it so "
                    "the data of the subvolume can be copied back. Nothing was deleted.")
            was_mounted = name in live.mounted
            opts = self._fstab_options(mp, name)
            if was_mounted:
                Command.execute("umount", [host_mp], check=True)
            try:
                self._copy(src, dst)
            except Exception as exc:
                if os.path.isdir(dst):      # verified empty above: drop the partial copy
                    try:
                        self._empty_dir(dst)
                    except OSError:
                        pass
                if not was_mounted:
                    raise
                try:
                    Command.execute("mount", ["-o", opts, device, host_mp], check=True)
                except Exception as exc2:
                    raise SubvolumeChangeRefused(
                        f"copying {name} back failed ({exc}) and remounting it "
                        f"at {mp} failed too ({exc2}); mount it by hand. "
                        "Nothing was deleted.") from exc
                raise SubvolumeChangeRefused(
                    f"copying {name} back failed ({exc}); it was remounted at "
                    f"{mp} and nothing was deleted.") from exc
            self._fstab_drop(mp)
            Command.execute("btrfs", ["subvolume", "delete", src], check=True)

    def apply(self, changes) -> None:
        """Re-probe PER ITEM, then act: the live state decides create / adopt /
        fstab-only / move (an earlier item may have changed the parent of a
        later one); ``Change.reason`` is display text and never read."""
        models = {f"{p.label}:{s.name}": (p, s)
                  for _d, p in self._btrfs_partitions() for s in p.btrfs_subvolumes}
        self._fstab_changed = False
        try:
            self._apply_each(changes, models)
        finally:
            self._reload_systemd()

    def _reload_systemd(self) -> None:
        """Make a live systemd re-read the fstab it generated mount units from.
        Skipped for an install target (nothing is running under /mnt) and when
        fstab did not change; same call as ``DropFilesAction._reload_systemd``."""
        target = self._target()
        if (not self._fstab_changed or target is None
                or getattr(target, "is_chroot", True)
                or not os.path.isdir("/run/systemd/system")):
            return
        try:
            Command.execute("systemctl", ["daemon-reload"], target=target)
        except Exception as exc:    # noqa: BLE001 - never mask the apply's own outcome
            run_logger.get().warning("systemctl daemon-reload failed",
                                     detail=str(exc))

    def _apply_each(self, changes, models) -> None:
        for c in changes:
            live = self._live()
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
            self._vet_declaration(subvol.name, subvol.mountpoint)
            self._install(lv, device, uuid, part, subvol)

    def import_state(self, managed=None) -> dict:
        """Nothing: subvolumes are a property of a PARTITION, and the ``disks``
        block has exactly one author — ``DiskPartitionAction.import_state``.
        Two actions emitting ``disks`` would clobber each other, since
        ``ConfigWriter.merge`` overwrites a key rather than merging two halves
        of one."""
        return {}
