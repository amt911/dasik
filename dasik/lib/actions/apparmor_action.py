"""Action: capture the `apparmor` block back from the machine (v3 domain "apparmor").

Convergence is owned elsewhere — the expand toggle installs `apparmor` (and the
audit framework), enables the units and drops the profiles, and
``KernelCmdlineAction`` maintains the ``lsm=`` parameter. Nothing owned the way
BACK, so a ``sync`` produced a config with `lsm=…` hand-set in
``kernel_cmdline`` and no ``apparmor`` block at all: the same policy spelled the
way dasik cannot reason about, and re-applying it would never install AppArmor.

CAPTURE-ONLY: ``plan()`` is deliberately empty (overriding it is what makes the
Reconciler treat this as a v3 action and visit it during sync), and all the work
is in ``import_state``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command

_PARSER_BIN = "/usr/bin/apparmor_parser"
_AUDITD_BIN = "/usr/bin/auditd"
_PROFILE_DIR = "/etc/apparmor.d"
_LSM_MODULE = "apparmor"
_AUDIT_PARAM = "audit=1"
_NOTIFY_AUTOSTART = ".config/autostart/apparmor-notify.desktop"


class ApparmorAction(AbstractAction):
    """Reconstruct the `apparmor` declaration from the live machine."""

    _DOMAIN = "apparmor"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "AppArmor"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else "/mnt" + canonical

    # --- v3 contract ------------------------------------------------------ #

    def plan(self, managed: Any) -> list:
        """Nothing to converge here — see the module docstring."""
        return []

    def managed_keys(self) -> dict:
        """Owns no manifest domain: it never applies anything."""
        return {}

    # --- probes ------------------------------------------------------------ #

    def _installed(self) -> bool:
        """Probed by the binary rather than ``pacman -Qq apparmor``: it needs no
        chroot round trip, so it also answers for a target that is merely
        mounted, and it cannot be fooled by a package database mid-transaction."""
        return os.path.exists(self._p(_PARSER_BIN))

    def _auditd_installed(self) -> bool:
        return os.path.exists(self._p(_AUDITD_BIN))

    def _live_params(self) -> List[str]:
        # Reuses KernelCmdlineAction's bootloader-entry readers rather than
        # growing a second copy of them (grub vs sd-boot, default entry, …).
        from .kernel_cmdline_action import KernelCmdlineAction
        return KernelCmdlineAction(self._cfg, self.context).live_params()

    @staticmethod
    def _lsm_names(params: List[str]) -> List[str]:
        for token in params:
            name, _, value = token.partition("=")
            if name == "lsm" and value:
                return value.split(",")
        return []

    def _pacman_owner(self, path: str) -> Optional[str]:
        """`pacman -Qo <path>` package name, or None (unowned / probe failed)."""
        try:
            res = Command.execute("pacman", ["-Qo", path], target=self._target())
        except Exception:      # nosec B110 - a failed probe just means "unknown"
            return None
        if getattr(res, "returncode", 1) != 0:
            return None
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        marker = " is owned by "
        return out.split(marker)[1].split()[0] if marker in out else None

    def _local_profiles(self) -> List[Dict[str, str]]:
        """Profiles under /etc/apparmor.d that no package owns.

        Files only, never the subdirectories: `abstractions/`, `tunables/` and
        `local/` are AppArmor's own machinery, and the profiles the package
        ships are already implied by the package — capturing them would drag
        hundreds of files into the config.
        """
        directory = self._p(_PROFILE_DIR)
        profiles: List[Dict[str, str]] = []
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return profiles
        for name in names:
            full = os.path.join(directory, name)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            if self._pacman_owner(f"{_PROFILE_DIR}/{name}"):
                continue
            try:
                with open(full, "r", encoding="utf-8") as f:
                    profiles.append({"name": name, "content": f.read()})
            except OSError:
                continue
        return profiles

    # --- capture ----------------------------------------------------------- #

    def import_state(self, managed=None) -> dict:
        """The `apparmor` block this machine is running, or ``{}``.

        ``enable`` reports whether AppArmor is *active*, not merely installed: a
        package with no ``lsm=`` naming it enforces nothing, and reporting true
        would describe a machine as protected when it is not.
        """
        if not self._installed():
            return {}
        params = self._live_params()
        block: Dict[str, Any] = {
            "enable": _LSM_MODULE in self._lsm_names(params),
            # The pair, not either half: auditd may be installed for its own
            # sake, and the parameter without the daemon logs into the void.
            "audit": self._auditd_installed() and _AUDIT_PARAM in params,
        }
        block["desktop_notifications"] = self._notifier_autostarts()
        profiles = self._local_profiles()
        if profiles:
            block["extra_profiles"] = profiles
        return {self._DOMAIN: block}

    def _notifier_autostarts(self) -> bool:
        """True when some user's home carries the aa-notify autostart entry.

        The `home_files` domain cannot answer this on its own: the entry is
        DERIVED by this block, so `subtract_contributions` strips it from the
        captured `home_files` — if nothing here re-derived it, the capture would
        lose the notifier entirely. Same shape as `sysrq` on the kernel cmdline.
        """
        for home in self._homes():
            if os.path.exists(os.path.join(self._p(home), _NOTIFY_AUTOSTART)):
                return True
        return False

    def _homes(self) -> List[str]:
        """Home directories of the machine's regular users, from /etc/passwd."""
        homes: List[str] = []
        try:
            with open(self._p("/etc/passwd"), "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return homes
        for line in lines:
            parts = line.rstrip("\n").split(":")
            if len(parts) < 6 or parts[0] == "root":
                continue
            try:
                uid = int(parts[2])
            except ValueError:
                continue
            if 1000 <= uid < 65534:        # a login account, not a service user
                homes.append(parts[5])
        return homes

    # --- legacy executor path ---------------------------------------------- #

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        return None
