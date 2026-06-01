"""Action: configure the initramfs via a pluggable generator backend.

Scalar v3 domain "initramfs": the desired config is a single derived value.
The generator (mkinitcpio | dracut | …) is chosen by the root `initramfs`
config field. Registered config_key="__root__" (reads disks + selector).
"""
from typing import Any, Dict
from .scalar_action import ScalarV3Action
from .initramfs import make_backend


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

    def _import_fragment(self, value) -> dict:
        return {}
