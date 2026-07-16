"""Action: configure the initramfs via a pluggable generator backend.

Scalar v3 domain "initramfs": the desired config is a single derived value.
The generator (mkinitcpio | dracut | …) is chosen by the root `initramfs`
config field. Registered config_key="__root__" (reads disks + selector).
"""
from typing import Any, Dict, Optional
from .scalar_action import ScalarV3Action
from .initramfs import make_backend
from ..command_worker.command_worker import Command


def _pkg_installed(pkg: str, target) -> bool:
    try:
        result = Command.execute("pacman", ["-Qq", pkg], target=target)
        return getattr(result, "returncode", 1) == 0
    except Exception:
        return False


class InitramfsAction(ScalarV3Action):
    """Configure + regenerate the initramfs (mkinitcpio/dracut/…)."""

    _DOMAIN = "initramfs"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        target = getattr(context, "target", None) if context else None
        self._backend = make_backend(cfg.get("initramfs", "mkinitcpio"), cfg, target)

    @property
    def name(self) -> str:
        return "Initramfs Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _desired_value(self):
        return self._backend.desired_value() or None

    def _actual_value(self):
        return self._backend.actual_value()

    def _set_value(self) -> None:
        self._backend.apply()

    def _detect_generator(self) -> Optional[str]:
        """Which initramfs generator the target actually uses. dracut iff it is
        installed and mkinitcpio is not (mkinitcpio is removed when you switch to
        dracut); mkinitcpio otherwise (the Arch default)."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return None
        dracut = _pkg_installed("dracut", target)
        mkinitcpio = _pkg_installed("mkinitcpio", target)
        if dracut and not mkinitcpio:
            return "dracut"
        return "mkinitcpio"

    def import_state(self, managed=None) -> dict:
        # sync captures the ACTIVE generator so a dracut host round-trips as
        # `"initramfs": "dracut"` instead of being silently dropped.
        gen = self._detect_generator()
        return {"initramfs": gen} if gen else {}

    def _import_fragment(self, value) -> dict:
        return {}
