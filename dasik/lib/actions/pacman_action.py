"""Action: configure pacman.conf (parallel, color, verbose, multilib).

Composite v3 domain "pacman": the desired state is the four flags dasik knows
(Parallel, Color, VerbosePkgLists, multilib). Bidirectional — a flag set False
is commented back out and the [multilib] block re-commented. Target-aware. One
MODIFY when any flag drifts.
"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional
from .composite_action import CompositeV3Action
from ..command_worker.command_worker import Command

_PACMAN_CONF = "/etc/pacman.conf"

# config-facing flag -> pacman.conf token
_OPTION_TOKENS = {
    "Parallel": "ParallelDownloads",
    "Color": "Color",
    "VerbosePkgLists": "VerbosePkgLists",
}


class PacmanAction(CompositeV3Action):
    """Configure pacman.conf declaratively (composite v3 domain)."""

    _DOMAIN = "pacman"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        opts = cfg.get("options", {}) or {}
        self.parallel = opts.get("Parallel", True)
        self.color = opts.get("Color", True)
        self.verbose = opts.get("VerbosePkgLists", False)
        self.multilib = cfg.get("multilib", False)

    @property
    def name(self) -> str:
        return "Pacman Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _read(self) -> Optional[str]:
        try:
            with open(self._p(_PACMAN_CONF), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- conf parsing helpers ----------------------------------------- #

    @staticmethod
    def _option_active(text: str, token: str) -> bool:
        return bool(re.search(rf"^\s*{token}\b", text, re.MULTILINE))

    @staticmethod
    def _multilib_active(text: str) -> bool:
        return re.search(r"^\[multilib\]\s*\n\s*Include", text, re.MULTILINE) is not None

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {
            "Parallel": bool(self.parallel),
            "Color": bool(self.color),
            "VerbosePkgLists": bool(self.verbose),
            "multilib": bool(self.multilib),
        }

    def _actual_state(self) -> Optional[dict]:
        text = self._read()
        if text is None:
            return None
        return {
            "Parallel": self._option_active(text, "ParallelDownloads"),
            "Color": self._option_active(text, "Color"),
            "VerbosePkgLists": self._option_active(text, "VerbosePkgLists"),
            "multilib": self._multilib_active(text),
        }

    def apply(self, changes) -> None:
        # Write pacman.conf (only when it drifts) via the composite machinery…
        super().apply(changes)
        # …then, if multilib is enabled on an install target, refresh the pacman
        # databases so the newly-enabled [multilib] repo has a DB. Without this a
        # later `pacman -S` aborts with "failed to prepare transaction (could not
        # find database)". Run every apply (not just on conf drift) so a re-run
        # after a partial install still ends up with the DB synced.
        t = self._target()
        if self.multilib and t is not None and getattr(t, "is_chroot", False):
            Command.execute_checked("pacman", ["-Sy"], target=t)

    def _import_fragment(self, value) -> dict:
        st = self._actual_state() or self._desired_state()
        return {self._DOMAIN: {
            "options": {
                "Parallel": st["Parallel"],
                "Color": st["Color"],
                "VerbosePkgLists": st["VerbosePkgLists"],
            },
            "multilib": st["multilib"],
        }}

    def _set_value(self) -> None:
        text = self._read() or ""
        desired = self._desired_state()
        for flag, token in _OPTION_TOKENS.items():
            if desired[flag]:
                text = re.sub(rf"^#\s*({token}\b.*)", r"\1", text, flags=re.MULTILINE)
            else:
                text = re.sub(rf"^({token}\b.*)", r"#\1", text, flags=re.MULTILINE)
        if desired["multilib"]:
            text = re.sub(
                r"^#\s*\[multilib\]\s*\n#\s*(Include\s*=.*)",
                r"[multilib]\n\1",
                text, flags=re.MULTILINE,
            )
        else:
            text = re.sub(
                r"^\[multilib\]\s*\n(Include\s*=.*)",
                r"#[multilib]\n#\1",
                text, flags=re.MULTILINE,
            )
        with open(self._p(_PACMAN_CONF), "w") as f:
            f.write(text)
