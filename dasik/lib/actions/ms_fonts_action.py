"""Action: install Microsoft fonts from a Windows ISO (v3 domain "microsoft_fonts").

Idempotent: a no-op once the fonts directory is populated. Gated on a declared
`source_iso`. `apply()` mounts/extracts the ISO and copies the fonts (shelled
out; covered via mocked `_install`). Target-aware.
"""
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict
from .abstract_action import AbstractAction
from ..state.change import Change, Op

_FONTS_DIR = "/usr/local/share/fonts/WindowsFonts"
_DOMAIN = "microsoft_fonts"


class MicrosoftFontsAction(AbstractAction):
    """Extract and install MS fonts from a Windows ISO (v3 domain)."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.install: bool = cfg.get("install", False)
        self.source_iso: str = cfg.get("source_iso") or ""

    @property
    def name(self) -> str:
        return "Microsoft Fonts"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _fonts_present(self) -> bool:
        d = self._p(_FONTS_DIR)
        return os.path.isdir(d) and len(os.listdir(d)) > 10

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"windows-fonts"} if self._fonts_present() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if self.install and self.source_iso and not self._fonts_present():
            return [Change(self._DOMAIN, Op.INSTALL, "windows-fonts", reason="from source_iso")]
        return []

    def apply(self, changes) -> None:
        # Re-check at apply time (the plan can be stale on a re-run before /mnt
        # is mounted): only extract when declared, an ISO is given and the fonts
        # aren't already there.
        if self.install and self.source_iso and not self._fonts_present():
            self._install()

    def import_state(self, managed=None) -> dict:
        # The section is user-owned (install flag + ISO path); sync leaves it.
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self._install()

    def verify(self) -> bool:
        return self._fonts_present()

    # --- the destructive bit (shelled out; mocked in tests) ----------- #

    def _install(self) -> None:  # pragma: no cover - shells out to 7z/arch-chroot
        root = self._target().root if self._target() is not None else "/mnt"
        subprocess.run(
            ["arch-chroot", root, "pacman", "--noconfirm", "--needed", "-S", "7zip"],
            check=True,
        )
        os.makedirs(self._p("/tmp/ms-fonts-work"), exist_ok=True)
        iso_inner = (
            self.source_iso.replace(root, "", 1)
            if self.source_iso.startswith(root) else self.source_iso
        )
        subprocess.run(
            ["arch-chroot", root, "7z", "e", iso_inner, "sources/install.wim",
             "-o/tmp/ms-fonts-work"], check=True,
        )
        subprocess.run(
            ["arch-chroot", root, "7z", "e", "/tmp/ms-fonts-work/install.wim",
             "1/Windows/Fonts/*.ttf", "1/Windows/Fonts/*.ttc",
             "-o/tmp/ms-fonts-work/fonts/"], check=True,
        )
        subprocess.run(["arch-chroot", root, "mkdir", "-p", _FONTS_DIR], check=True)
        subprocess.run(
            ["arch-chroot", root, "sh", "-c",
             f"cp /tmp/ms-fonts-work/fonts/* {_FONTS_DIR}/ && chmod 644 {_FONTS_DIR}/*"],
            check=True,
        )
        subprocess.run(["arch-chroot", root, "fc-cache", "--force"], check=True)
