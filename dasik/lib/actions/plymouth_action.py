"""Action: capture the `plymouth` block back from the machine (v3 domain "plymouth").

Convergence is owned elsewhere — the expand toggle installs the package and
writes ``/etc/plymouth/plymouthd.conf``, ``KernelCmdlineAction`` maintains
``splash``, and the initramfs backends put the hook/module in the image. Nothing
owned the way BACK, so a ``sync`` produced a config carrying a bare ``splash`` in
``kernel_cmdline`` and no ``plymouth`` block at all: the same policy, spelled the
way dasik cannot reason about, and re-applying it would never install plymouth.

CAPTURE-ONLY: ``plan()`` is deliberately empty (it is overridden so the
Reconciler treats this as a v3 action and visits it during sync), and all the
work is in ``import_state``.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from .abstract_action import AbstractAction

_PLYMOUTHD = "/usr/bin/plymouthd"
_CONF = "/etc/plymouth/plymouthd.conf"
_THEME_RE = re.compile(r"^\s*Theme\s*=\s*(\S+)\s*$")


def plymouth_installed(target) -> bool:
    """Whether the target has plymouth installed.

    Probed by the daemon binary rather than ``pacman -Qq plymouth``: it needs no
    chroot round trip (so it also answers for a target that is merely mounted),
    and it cannot be fooled by a package database mid-transaction.
    """
    path = target.path(_PLYMOUTHD) if target is not None else "/mnt" + _PLYMOUTHD
    return os.path.exists(path)


class PlymouthAction(AbstractAction):
    """Reconstruct the `plymouth` declaration from the live boot splash."""

    _DOMAIN = "plymouth"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "Plymouth (boot splash)"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    # --- v3 contract --------------------------------------------------- #

    def plan(self, managed: Any) -> list:
        """Nothing to converge here — see the module docstring."""
        return []

    def managed_keys(self) -> dict:
        """Owns no manifest domain: it never applies anything."""
        return {}

    # --- capture -------------------------------------------------------- #

    def _theme(self) -> Optional[str]:
        target = self._target()
        path = target.path(_CONF) if target is not None else "/mnt" + _CONF
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    match = _THEME_RE.match(line)
                    if match:
                        return match.group(1)
        except OSError:
            pass
        return None

    def import_state(self, managed=None) -> dict:
        """The `plymouth` block this machine is running, or ``{}``.

        An installed plymouth with no ``Theme=`` captures as an empty block: the
        splash is declared, the theme is plymouth's own. Naming a theme the
        machine never set would pin a default that upstream is free to change.
        """
        if not plymouth_installed(self._target()):
            return {}
        theme = self._theme()
        return {self._DOMAIN: {"theme": theme} if theme else {}}


