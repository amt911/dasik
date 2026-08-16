"""Action: install Microsoft fonts from a Windows ISO (v3 domain "microsoft_fonts").

Idempotent: a no-op once the fonts directory is populated. Gated on a declared
`source_iso`. `apply()` mounts/extracts the ISO and copies the fonts (shelled
out; covered via mocked `_install`). Target-aware.
"""
from __future__ import annotations
import os
from typing import Any, Dict
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
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
        if changes:
            self._install()

    def import_state(self, managed=None) -> dict:
        # The section is user-owned (install flag + ISO path); sync leaves it.
        return {}

    # --- legacy executor bridge --------------------------------------- #



    def verify(self) -> bool:
        return self._fonts_present()

    # --- the destructive bit (shelled out; mocked in tests) ----------- #

    def _install(self) -> None:  # pragma: no cover - shells out to 7z inside the target
        """Extract the fonts out of a Windows ISO, inside the target.

        Every step goes through Command.execute — not subprocess — so it lands
        in the run log like everything else, gets the arch-chroot prefix from
        one place, and fails with dasik's own message when a binary is missing.
        A 7z extraction of somebody's Windows ISO is exactly the step you want
        to be able to read afterwards.
        """
        t = self._target()
        root = t.root if t is not None else "/mnt"
        Command.execute("pacman", ["--noconfirm", "--needed", "-S", "7zip"],
                        target=t, check=True)
        # /tmp here is inside the freshly-installed TARGET chroot during install
        # (single-user, no other local accounts yet), not the host /tmp — see the
        # B108 justification in [tool.bandit].
        os.makedirs(self._p("/tmp/ms-fonts-work"), exist_ok=True)
        iso_inner = (
            self.source_iso.replace(root, "", 1)
            if self.source_iso.startswith(root) else self.source_iso
        )
        Command.execute("7z", ["e", iso_inner, "sources/install.wim",
                               "-o/tmp/ms-fonts-work"], target=t, check=True)
        Command.execute("7z", ["e", "/tmp/ms-fonts-work/install.wim",
                               "1/Windows/Fonts/*.ttf", "1/Windows/Fonts/*.ttc",
                               "-o/tmp/ms-fonts-work/fonts/"], target=t, check=True)
        Command.execute("mkdir", ["-p", _FONTS_DIR], target=t, check=True)
        Command.execute("sh", ["-c",
                               f"cp /tmp/ms-fonts-work/fonts/* {_FONTS_DIR}/ && "
                               f"chmod 644 {_FONTS_DIR}/*"], target=t, check=True)
        Command.execute("fc-cache", ["--force"], target=t, check=True)
