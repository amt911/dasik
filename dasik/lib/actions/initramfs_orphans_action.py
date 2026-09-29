"""Action: prune initramfs images nothing can boot (v3 domain "initramfs_orphans").

`dracut --regenerate-all` and a bare `dracut -f` name images by kver
(initramfs-<kver>.img), which no boot entry references, and a removed kernel
leaves its images behind. They pile up on the ESP until an image build fails
for lack of space. Measured on a real machine: four of them, ~560 MB of a
1 GiB ESP, and the next `dracut --force` could not fit its temporary copy.

Only KVER-named images are candidates — initramfs-<kver>.img and its -fallback,
kver starting with a digit, which is what those commands write. A pkgbase-named
image with no kernel may be a hand-kept backup loaded from a config dasik cannot
see, and dracut-remove.hook already deletes a removed kernel's own images, so
those are never touched. A candidate is kept when any boot entry or grub .cfg
references it, or when its kver is installed WITHOUT Arch's pkgbase file (a
hand-built kernel whose kver image may be the only one it has). Everything else
is planned as a REMOVE, so the deletion is always announced by `plan` first.

Driven by the machine, not by the config: there is no block to declare and
nothing for `sync` to capture. Without a single installed kernel the action
plans nothing at all.
"""
from __future__ import annotations

import glob
import os
import re
from typing import Any, List, Set

from .abstract_action import AbstractAction
from .initramfs.base import installed_kernels
from ..state.change import Change, Op

_DOMAIN = "initramfs_orphans"
_BOOT = "/boot"
_ENTRIES = "/boot/loader/entries/*.conf"
_GRUB_CFGS = "/boot/grub/*.cfg"
_IMAGE = re.compile(r"initramfs-[^\s/]+\.img")
_KVER_IMAGE = re.compile(r"initramfs-(\d[^\s/]*?)(?:-fallback)?\.img")


class InitramfsOrphansAction(AbstractAction):
    """Remove kver-named initramfs images no boot entry loads."""

    _DOMAIN = _DOMAIN

    @property
    def name(self) -> str:
        return "Orphaned initramfs images"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    # --- paths --------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- state --------------------------------------------------------- #

    def _modules(self) -> str:
        return self._p("/usr/lib/modules")

    def _hand_built(self, kver: str) -> bool:
        """An installed kver with no pkgbase: a kernel outside Arch packaging."""
        root = os.path.join(self._modules(), kver)
        return os.path.isdir(root) and not os.path.exists(os.path.join(root, "pkgbase"))

    def _referenced(self) -> Set[str]:
        """Every initramfs image a systemd-boot entry or grub.cfg names."""
        found: Set[str] = set()
        for path in glob.glob(self._p(_ENTRIES)) + glob.glob(self._p(_GRUB_CFGS)):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    found.update(_IMAGE.findall(f.read()))
            except OSError:
                continue
        return found

    def actual(self) -> Set[str]:
        """Regular files named initramfs-*.img on the ESP. Directories and
        symlinks are never candidates: nothing dracut or mkinitcpio writes."""
        boot = self._p(_BOOT)
        try:
            names = os.listdir(boot)
        except OSError:
            return set()
        return {n for n in names if _IMAGE.fullmatch(n)
                and os.path.isfile(os.path.join(boot, n))
                and not os.path.islink(os.path.join(boot, n))}

    # --- v3 contract --------------------------------------------------- #

    def plan(self, managed: Any) -> List[Change]:
        if not installed_kernels(self._modules()):
            return []
        referenced = self._referenced()
        orphans = []
        for name in sorted(self.actual()):
            m = _KVER_IMAGE.fullmatch(name)
            if not m or name in referenced or self._hand_built(m.group(1)):
                continue
            orphans.append(name)
        return [Change(self._DOMAIN, Op.REMOVE, name,
                       reason="named by kver: no boot entry loads it")
                for name in orphans]

    def apply(self, changes) -> None:
        for change in changes:
            if change.op is not Op.REMOVE:
                continue
            name = os.path.basename(str(change.item))
            if not _KVER_IMAGE.fullmatch(name):   # never delete anything else
                continue
            try:
                os.remove(os.path.join(self._p(_BOOT), name))
            except FileNotFoundError:
                pass

    def managed_keys(self) -> dict:
        return {self._DOMAIN: []}

    def import_state(self, managed=None) -> dict:
        # Nothing to capture: there is no block, only the machine.
        return {}
