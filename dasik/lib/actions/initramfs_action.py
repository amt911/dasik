"""Action: configure the initramfs via a pluggable generator backend.

Scalar v3 domain "initramfs": the desired config is a single derived value.
The generator (mkinitcpio | dracut | …) is chosen by the root `initramfs`
config field. Registered config_key="__root__" (reads disks + selector).
"""
import glob
import os
import re
from typing import Any, Dict, Optional
from .scalar_action import ScalarV3Action
from .initramfs import make_backend
from ..command_worker.command_worker import Command
from ..expand.toggles import NEUTRALIZER_MARKER as _NEUTRALIZER_MARKER


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

    def plan(self, managed: Any):
        """The scalar comparison, plus the one thing it cannot see.

        The scalar value is the generator's own configuration (hooks, modules,
        image freshness). It is computed by the DECLARED backend, so on a
        machine still running the other generator it can compare "converged"
        against a file that tool never wrote: switching `initramfs` from
        mkinitcpio to dracut installed dracut, neutralised mkinitcpio's hooks,
        left /boot/initramfs-linux.img untouched, and reported No changes.

        So the generator itself is part of the desired state. When the machine
        runs a different one, the image is rebuilt by the declared backend.
        """
        from ..state.change import Change, Op

        changes = super().plan(managed)
        if changes:
            return changes
        detected = self._detect_generator()
        declared = self._declared_generator()
        if detected and detected != declared:
            return [Change(self._DOMAIN, Op.MODIFY, declared,
                           reason=f"generator switch ({detected} -> {declared})")]
        return []

    def _declared_generator(self) -> str:
        cfg = self.config if isinstance(self.config, dict) else {}
        return cfg.get("initramfs", "mkinitcpio")

    def _detect_generator(self) -> Optional[str]:
        """Which initramfs generator the target actually uses.

        Detection is by EFFECTIVE OWNERSHIP, not by package coexistence: dasik
        deliberately keeps mkinitcpio installed when dracut is the generator and
        neutralizes its pacman hooks instead (safe + reversible — see
        ``expand_initramfs``). So "both installed" is the normal dracut layout;
        judging by presence alone imported a dracut host as mkinitcpio and let
        sync silently switch its generator. dracut wins when mkinitcpio is absent
        OR its hooks are neutralized by dasik."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return None
        dracut = _pkg_installed("dracut", target)
        mkinitcpio = _pkg_installed("mkinitcpio", target)
        if dracut and (not mkinitcpio or self._mkinitcpio_neutralized(target)):
            return "dracut"
        return "mkinitcpio"

    @staticmethod
    def _mkinitcpio_neutralized(target) -> bool:
        """True when dasik's no-op override of mkinitcpio's pacman hooks is in
        place (identified by the marker Target= it triggers on, which no package
        can ever match)."""
        hooks_dir = target.path("/etc/pacman.d/hooks")
        for name in ("90-mkinitcpio-install.hook", "60-mkinitcpio-remove.hook"):
            try:
                with open(os.path.join(hooks_dir, name), "r", encoding="utf-8") as f:
                    if _NEUTRALIZER_MARKER in f.read():
                        return True
            except OSError:
                continue
        return False

    def _dracut_bluetooth_in_initramfs(self) -> bool:
        """True if any /etc/dracut.conf.d/*.conf pulls the `bluetooth` module into
        the initramfs (a BT keyboard at the LUKS/FIDO2 prompt)."""
        target = getattr(self.context, "target", None) if self.context else None
        if target is None:
            return False
        conf_d = target.path("/etc/dracut.conf.d")
        rx = re.compile(r"add_dracutmodules\+?=.*\bbluetooth\b")
        try:
            for conf in glob.glob(os.path.join(conf_d, "*.conf")):
                with open(conf, "r") as f:
                    if rx.search(f.read()):
                        return True
        except OSError:
            return False
        return False

    def import_state(self, managed=None) -> dict:
        # sync captures the ACTIVE generator so a dracut host round-trips as
        # `"initramfs": "dracut"` instead of being silently dropped.
        gen = self._detect_generator()
        frag: Dict[str, Any] = {}
        if gen:
            frag["initramfs"] = gen
        # A BT keyboard in the initramfs isn't otherwise captured (bluez/service are,
        # via packages/systemd, but the initramfs module is only in dracut.conf.d).
        if self._dracut_bluetooth_in_initramfs():
            frag["bluetooth"] = {"enable": True, "in_initramfs": True}
        return frag

    def _import_fragment(self, value) -> dict:
        return {}
