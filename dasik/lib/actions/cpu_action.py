"""Action: capture the `cpu` block back from the machine (v3 domain "cpu").

Convergence for CPU scaling is spread across pieces that already own
themselves: the expand toggle installs power-profiles-daemon / cpupower and
writes ``/etc/default/cpupower``, and ``KernelCmdlineAction`` maintains the
``amd_pstate=``/``intel_pstate=`` parameter. Nothing owned the way *back*, so a
``sync`` produced a config with the parameter hand-set in ``kernel_cmdline``
and no ``cpu`` block at all — the same policy, spelled the way dasik cannot
reason about.

This action is therefore CAPTURE-ONLY: ``plan()`` is deliberately empty (it is
overridden so the Reconciler treats it as a v3 action and visits it during
sync), and all the work is in ``import_state``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command

_CPUPOWER_CONF = "/etc/default/cpupower"
_PPD_UNIT = "power-profiles-daemon.service"
_GOVERNOR_RE = re.compile(r'^\s*governor\s*=\s*"?([a-z_]+)"?\s*$')
_PSTATE_PARAMS = ("amd_pstate", "intel_pstate")


class CpuAction(AbstractAction):
    """Reconstruct the `cpu` declaration from live CPU-scaling state."""

    _DOMAIN = "cpu"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "CPU Scaling"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    # --- v3 contract --------------------------------------------------- #

    def plan(self, managed: Any) -> list:
        """Nothing to converge here — see the module docstring.

        Overriding ``plan`` is what marks the class as v3, which is what makes
        ``Reconciler.sync`` visit it at all.
        """
        return []

    def managed_keys(self) -> dict:
        """Owns no manifest domain: it never applies anything."""
        return {}

    # --- capture -------------------------------------------------------- #

    @staticmethod
    def _driver_and_mode(params: List[str]) -> Tuple[Optional[str], str]:
        """The (scaling_driver, mode) a live cmdline parameter declares.

        ``<driver>=disable`` maps back to ``acpi_cpufreq``: that is the only
        reason dasik emits it (the built-in driver must stand down for
        acpi-cpufreq to bind). It is indistinguishable from a hand-written
        ``intel_pstate`` + ``mode=disable``, and the acpi_cpufreq reading is the
        one that reproduces the machine.
        """
        for token in params:
            name, _, value = token.partition("=")
            if name in _PSTATE_PARAMS and value:
                if value == "disable":
                    return "acpi_cpufreq", "active"
                return name, value
        return None, "active"

    def _governor(self) -> Optional[str]:
        target = self._target()
        path = target.path(_CPUPOWER_CONF) if target is not None else _CPUPOWER_CONF
        try:
            with open(path, "r") as f:
                for line in f:
                    match = _GOVERNOR_RE.match(line)
                    if match:
                        return match.group(1)
        except OSError:
            pass
        return None

    def _ppd_enabled(self) -> bool:
        """Whether power-profiles-daemon is enabled on the target.

        An unusable probe (no systemctl, no arch-chroot for this target) falls
        back to the model's default of True rather than to False: capturing
        False would delete a service the machine may well be running, while the
        worst case of True is that an apply enables what is already enabled.
        """
        target = self._target()
        if target is None:
            return True         # the model's default; nothing to read
        try:
            result = Command.execute("systemctl", ["is-enabled", _PPD_UNIT], target=target)
        except Exception:       # noqa: BLE001 - any probe failure is non-fatal
            return True
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return stdout.strip() == "enabled"

    def _live_params(self) -> List[str]:
        # Reuses KernelCmdlineAction's bootloader-entry readers rather than
        # growing a second copy of them (grub vs sd-boot, default entry, …).
        from .kernel_cmdline_action import KernelCmdlineAction
        return KernelCmdlineAction(self._cfg, self.context).live_params()

    def import_state(self, managed=None) -> dict:
        """The `cpu` block this machine is running, or ``{}``.

        Captures only when there IS a CPU policy to describe — a pstate
        parameter or a cpupower governor. power-profiles-daemon on its own is
        already captured as a package and an enabled unit, so emitting a `cpu`
        block for every machine that runs it would be noise.
        """
        driver, mode = self._driver_and_mode(self._live_params())
        governor = self._governor()
        if driver is None and governor is None:
            return {}

        block: Dict[str, Any] = {
            "scaling_driver": driver or "none",
            "mode": mode,
            "power_profiles_daemon": self._ppd_enabled(),
        }
        if governor:
            block["governor"] = governor
        return {self._DOMAIN: block}

    # --- legacy executor path ------------------------------------------- #

    def is_needed(self) -> bool:
        return False

    def execute(self) -> None:
        return None
