"""Action: dasik-owned pacman hooks — written BEFORE the first pacman runs.

Both belong to the dracut generator: the mkinitcpio neutralizers — a same-named
hook under ``/etc/pacman.d/hooks`` overrides mkinitcpio's own in
``/usr/share/libalpm/hooks`` so only dracut ever regenerates the initramfs — and
the pair that keeps dasik's generic fallback image in step with the kernel,
which dracut's own hook never rebuilds.

Why a separate action instead of the `files` domain: ``expand_initramfs``
contributed these hooks to ``files``, which ``DropFilesAction`` writes in phase 4
— after ``BaseInstallAction`` (pacstrap) and ``PackagesAction`` have already run
every transaction that installs a kernel, systemd or a DKMS module. The
2026-07-19 install shows the result: dracut's hook ran, then mkinitcpio ran right
after it and overwrote ``/boot/initramfs-linux.img`` with an image built without
``sd-encrypt`` — no way to open the LUKS root. This action is registered in
phase 1, between the disk actions (which mount the target) and pacstrap, so the
neutralizers are in place before the very first transaction.

Idempotent: a change is planned only when the on-disk content differs from the
derived one, and a hook is removed only when dasik's own marker is in it.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from ..expand.toggles import (
    DRACUT_FALLBACK_HOOKS, FALLBACK_HOOK_MARKER, MKINITCPIO_HOOKS, NEUTRALIZER_MARKER,
    _fallback_hook, _neutralizer_hook,
)
from ..state.change import Change, Op

_HOOKS_DIR = "/etc/pacman.d/hooks"


class PacmanHooksAction(AbstractAction):
    """Own the pacman hooks dasik derives from the config."""

    _DOMAIN = "pacman_hooks"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.generator: str = cfg.get("initramfs", "mkinitcpio")

    @property
    def name(self) -> str:
        return "Pacman Hooks"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    # --- paths --------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self, name: str) -> str:
        t = self._target()
        canonical = f"{_HOOKS_DIR}/{name}"
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _desired(self) -> Dict[str, str]:
        """hook name -> content. Empty unless dracut is the generator."""
        if self.generator != "dracut":
            return {}
        hooks = {name: _neutralizer_hook(name) for name in MKINITCPIO_HOOKS}
        hooks.update({name: _fallback_hook(name) for name in DRACUT_FALLBACK_HOOKS})
        return hooks

    def _read(self, name: str) -> "str | None":
        try:
            with open(self._path(name), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    # --- v3 contract --------------------------------------------------- #

    def actual(self) -> set:
        """The hooks on disk that carry dasik's marker — never a same-named file
        someone else wrote."""
        owned = {name for name in MKINITCPIO_HOOKS
                 if NEUTRALIZER_MARKER in (self._read(name) or "")}
        owned |= {name for name in DRACUT_FALLBACK_HOOKS
                  if FALLBACK_HOOK_MARKER in (self._read(name) or "")}
        return owned

    def plan(self, managed) -> List[Change]:
        desired = self._desired()
        changes: List[Change] = []
        for name, content in desired.items():
            if self._read(name) != content:
                reason = ("mkinitcpio neutralizer" if name in MKINITCPIO_HOOKS
                          else "dracut fallback image")
                changes.append(Change(self._DOMAIN, Op.MODIFY, name, reason=reason))
        # Only dasik's own hooks are removable — a same-named hook someone else
        # wrote is left untouched.
        for name in self.actual() - set(desired):
            changes.append(Change(self._DOMAIN, Op.REMOVE, name,
                                  reason="generator is no longer dracut"))
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self._desired())}

    def apply(self, changes) -> None:
        desired = self._desired()
        for change in changes:
            path = self._path(change.item)
            if change.op is Op.REMOVE:
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(desired[change.item])

    def import_state(self, managed=None) -> dict:
        # Nothing to capture: the hooks are DERIVED from `initramfs`, which
        # InitramfsAction already round-trips.
        return {}

    # --- legacy executor bridge ---------------------------------------- #


    def verify(self) -> bool:
        return not self.plan(managed=[])
