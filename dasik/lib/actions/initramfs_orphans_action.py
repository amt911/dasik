"""Action: prune initramfs images nothing can boot (v3 domain "initramfs_orphans").

`dracut --regenerate-all` and a bare `dracut -f` name images by kver
(initramfs-<kver>.img), which no boot entry references, and a removed kernel
leaves its images behind. They pile up on the ESP until an image build fails
for lack of space. Measured on a real machine: four of them, ~560 MB of a
1 GiB ESP, and the next `dracut --force` could not fit its temporary copy.

An image is kept when an installed kernel owns it — initramfs-<pkgbase>.img or
initramfs-<pkgbase>-fallback.img, the names the entries use — or when any boot
entry references it. Everything else named initramfs-*.img is planned as a
REMOVE, so the deletion is always announced by `plan` first.

Driven by the machine, not by the config: there is no block to declare and
nothing for `sync` to capture. Without a single installed kernel the action
plans nothing at all, because then every image would look orphaned.
"""
from __future__ import annotations

import glob
import os
import re
from typing import Any, List, Set

from .abstract_action import AbstractAction
from .initramfs.base import image_path, installed_kernels
from ..state.change import Change, Op

_DOMAIN = "initramfs_orphans"
_BOOT = "/boot"
_ENTRIES = "/boot/loader/entries/*.conf"
_GRUB_CFG = "/boot/grub/grub.cfg"
_IMAGE = re.compile(r"initramfs-[^\s/]+\.img")


class InitramfsOrphansAction(AbstractAction):
    """Remove initramfs images no installed kernel owns and no entry loads."""

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

    def _owned(self) -> Set[str]:
        """Images an installed kernel owns — empty when there is no kernel."""
        owned: Set[str] = set()
        for _kver, pkgbase in installed_kernels(self._p("/usr/lib/modules")):
            owned.add(os.path.basename(image_path(pkgbase)))
            owned.add(os.path.basename(image_path(pkgbase, fallback=True)))
        return owned

    def _referenced(self) -> Set[str]:
        """Every initramfs image a systemd-boot entry or grub.cfg names."""
        found: Set[str] = set()
        for path in glob.glob(self._p(_ENTRIES)) + [self._p(_GRUB_CFG)]:
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
        owned = self._owned()
        if not owned:
            return []
        orphans = self.actual() - owned - self._referenced()
        return [Change(self._DOMAIN, Op.REMOVE, name,
                       reason="no installed kernel owns it and no boot entry loads it")
                for name in sorted(orphans)]

    def apply(self, changes) -> None:
        for change in changes:
            if change.op is not Op.REMOVE:
                continue
            name = os.path.basename(str(change.item))
            if not _IMAGE.fullmatch(name):   # never delete anything else
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
