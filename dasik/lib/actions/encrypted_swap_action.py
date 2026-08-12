"""Action: the /etc/fstab and /etc/crypttab lines of a random-key swap.

The partition itself is formatted by DiskPartitionAction (a 1 MiB ext2
filesystem carrying the label the crypttab entry addresses). What is left are
two lines nobody else writes:

* **fstab** — ``genfstab`` runs during the install and can only describe what is
  mounted. ``/dev/mapper/swap`` does not exist yet: it is created at the FIRST
  boot, by the crypttab entry. So the swap line has to be appended afterwards,
  or the installed system boots with the swap inert.
* **crypttab** — owned by DracutBackend whenever the generator is dracut (it
  composes the derived root entry there, and now the swap one too). With
  mkinitcpio nobody composes it, so this action does. The two never write it at
  the same time.

Removal is gated on ownership, like everywhere else: a mapper name this tool
never recorded in a manifest belongs to somebody else's swap and is left alone,
however much it looks like ours.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from .initramfs.base import detect_encryption
from .swap_encryption import (
    crypttab_line,
    fstab_line,
    random_swap_partitions,
    swap_names,
)
from ..state.change import Change, Op

_FSTAB = "/etc/fstab"
_CRYPTTAB = "/etc/crypttab"


class EncryptedSwapAction(AbstractAction):
    """Own the fstab (and, without dracut, crypttab) lines of a random-key swap."""

    _DOMAIN = "swap_encryption"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self._parts = random_swap_partitions(cfg)
        # dracut composes /etc/crypttab itself — but ONLY when there is
        # encryption to compose: InitramfsAction runs its backend when the
        # initramfs domain plans a change, and with no LUKS volume the dracut
        # config is empty, the action no-ops, and the file is never written.
        # Yielding on `initramfs: dracut` alone therefore left NOBODY writing
        # it: VM-proven, /etc/crypttab had no swap line, /dev/mapper/swap never
        # appeared and the swap was inert. This is the same condition
        # DropFilesAction uses to decide the very same handover.
        self._dracut_owns_crypttab = (
            cfg.get("initramfs") == "dracut" and detect_encryption(cfg))

    @property
    def name(self) -> str:
        return "Encrypted Swap"

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

    def _read(self, canonical: str) -> str:
        try:
            with open(self._p(canonical), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # --- desired vs actual ----------------------------------------------- #

    def _desired(self) -> Dict[str, Dict[str, str]]:
        """mapper name -> {"fstab": line, "crypttab": line}."""
        out: Dict[str, Dict[str, str]] = {}
        for part in self._parts:
            mapper, _ = swap_names(part)
            out[mapper] = {"fstab": fstab_line(part), "crypttab": crypttab_line(part)}
        return out

    @staticmethod
    def _has_line(text: str, line: str) -> bool:
        return line in [ln.strip() for ln in text.splitlines()]

    def _converged(self, lines: Dict[str, str]) -> bool:
        if not self._has_line(self._read(_FSTAB), lines["fstab"]):
            return False
        if self._dracut_owns_crypttab:
            # Not ours to check: dracut writes that file later in the same run,
            # and judging ourselves unconverged by its absence would re-plan the
            # same change forever.
            return True
        return self._has_line(self._read(_CRYPTTAB), lines["crypttab"])

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {m for m, lines in self._desired().items() if self._converged(lines)}

    # --- v3 contract ------------------------------------------------------ #

    def plan(self, managed: Any) -> List[Change]:
        desired = self._desired()
        actual = self.actual()
        changes: List[Change] = []
        for mapper in desired:
            if mapper not in actual:
                changes.append(Change(self._DOMAIN, Op.INSTALL, mapper,
                                      reason="crypttab + fstab entry"))
        for mapper in managed or []:
            if mapper not in desired and self._mentions(mapper):
                changes.append(Change(self._DOMAIN, Op.REMOVE, mapper,
                                      reason="no longer declared"))
        return changes

    def _mentions(self, mapper: str) -> bool:
        """Whether the target still carries either line for this mapper."""
        if any(ln.split()[:1] == [f"/dev/mapper/{mapper}"]
               for ln in self._read(_FSTAB).splitlines() if ln.strip()):
            return True
        return any(ln.split()[:1] == [mapper]
                   for ln in self._read(_CRYPTTAB).splitlines() if ln.strip())

    def apply(self, changes) -> None:
        if not changes or self._target() is None:
            return
        desired = self._desired()
        for change in changes:
            if change.op is Op.REMOVE:
                self._drop(change.item)
            elif change.item in desired:
                self._write(desired[change.item])

    def _append(self, canonical: str, line: str) -> None:
        path = self._p(canonical)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._read(canonical)
        if self._has_line(current, line):
            return
        with open(path, "a", encoding="utf-8") as f:
            if current and not current.endswith("\n"):
                f.write("\n")
            f.write(line + "\n")

    def _write(self, lines: Dict[str, str]) -> None:
        self._append(_FSTAB, lines["fstab"])
        if not self._dracut_owns_crypttab:
            self._append(_CRYPTTAB, lines["crypttab"])

    def _drop(self, mapper: str) -> None:
        for canonical, first_field in ((_FSTAB, f"/dev/mapper/{mapper}"),
                                       (_CRYPTTAB, mapper)):
            current = self._read(canonical)
            if not current:
                continue
            kept = [ln for ln in current.splitlines()
                    if not (ln.strip() and ln.split()[:1] == [first_field])]
            with open(self._p(canonical), "w", encoding="utf-8") as f:
                f.write("\n".join(kept) + ("\n" if kept else ""))

    def managed_keys(self) -> dict:
        return {self._DOMAIN: list(self._desired().keys())}

    def import_state(self, managed=None) -> dict:
        """Nothing: the mode is a property of a PARTITION, and the ``disks``
        block has exactly one author — ``DiskPartitionAction.import_state``,
        which captures ``swap_encryption`` alongside the rest of the layout. Two
        actions emitting ``disks`` would clobber each other, since
        ``ConfigWriter.merge`` overwrites a key rather than merging two halves
        of one."""
        return {}

    # --- legacy executor bridge ------------------------------------------ #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
