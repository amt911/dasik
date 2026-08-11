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
        # PacmanModel defaults every field, so an empty dict never reaches here
        # from a user config — only from the reconciler, which hands
        # empty_config() for a domain a previous generation owned. See plan().
        self._declared: bool = bool(cfg)

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
        # Write pacman.conf when it drifts (the composite base gates on changes)…
        super().apply(changes)
        # …then, if this apply just enabled [multilib] on an install target,
        # sync the pacman databases so the newly-enabled repo has a DB. Without
        # it a later `pacman -S lib32-...` aborts with "could not find database".
        # Gated on `changes`: the reconciler calls apply() for every action even
        # with no changes, so an unconditional -Sy would hit the network on every
        # idempotent re-run. `is_chroot` scopes it to install targets, not the
        # live host.
        t = self._target()
        if changes and self.multilib and t is not None and getattr(t, "is_chroot", False):
            Command.execute("pacman", ["-Sy"], target=t, check=True)

    def plan(self, managed):
        """Nothing declared is not "dasik's defaults".

        Dropping a `pacman` block a generation owned makes the reconciler plan
        the empty config, whose desired state is every default — which would
        re-comment `[multilib]` on a machine that depends on it. Leave
        pacman.conf alone instead.
        """
        return super().plan(managed) if self._declared else []

    def _import_fragment(self, value) -> dict:
        # Report the machine: no /etc/pacman.conf is an unbuilt target, not a
        # machine whose options happen to be dasik's defaults.
        st = self._actual_state()
        if st is None:
            return {}
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
