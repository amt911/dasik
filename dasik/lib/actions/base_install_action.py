"""Action: pacstrap the base system into the target (v3 domain "base").

Idempotent: a no-op once the base system is pacstrapped (marker:
``<target>/usr/bin/pacman``). Install-only. Target-aware. The destructive
pacstrap/genfstab lives in ``_install()`` (mocked in tests).
"""
from __future__ import annotations
import os
from typing import Any, Dict, List
from colorama import Fore, Style, init
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..logging import run_logger
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_MARKER = "/usr/bin/pacman"
_DOMAIN = "base"




# pacstrap exits 0 even when a hook it ran FAILED — alpm reports the hook's
# failure on its own output and carries on. The 2026-07-19 install shows
# mkinitcpio failing inside pacstrap ("the image may not be complete",
# "command failed to execute correctly") with the outer command returning 0, so
# nothing surfaced. These are warnings, not aborts: the later initramfs action
# regenerates the image — but the user must see that the base image is suspect.
_HOOK_FAILURE_MARKERS = (
    "command failed to execute correctly",
    "image may not be complete",
    "==> ERROR",
    "error: could not",
)


def _report_hook_failures(output) -> None:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    text = output or ""
    hits = [line.strip() for line in text.splitlines()
            if any(marker in line for marker in _HOOK_FAILURE_MARKERS)]
    if not hits:
        return
    run_logger.get().warning(
        "a hook failed during pacstrap (pacstrap itself exited 0)",
        detail="\n".join(hits),
    )

class BaseInstallAction(AbstractAction):
    """Install the Arch base system (base, linux, firmware, microcode)."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.enable_microcode: bool = cfg.get("enable_microcode", False)
        self.packages: List[str] = ["base", "linux", "linux-firmware"]
        init(autoreset=True)
        if self.enable_microcode:
            self.packages += [self._detect_microcode()]

    @property
    def name(self) -> str:
        return "Base Installation"

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _target_root(self) -> str:
        t = self._target()
        return t.root if t is not None else "/mnt"

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    @staticmethod
    def _detect_microcode() -> str:
        with open("/proc/cpuinfo", "r") as cpuinfo:
            content = cpuinfo.read()
        if "AuthenticAMD" in content:
            return "amd-ucode"
        if "GenuineIntel" in content:
            return "intel-ucode"
        print(Fore.RED + "Unknown CPU Vendor. Exiting..." + Style.RESET_ALL)
        raise SystemExit(1)

    def _installed(self) -> bool:
        return os.path.exists(self._p(_MARKER))

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"base"} if self._installed() else set()

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        if not self._installed():
            return [Change(self._DOMAIN, Op.INSTALL, "base", reason="pacstrap")]
        return []

    def apply(self, changes) -> None:
        if changes:
            self._install()

    def _microcode_installed(self) -> bool:
        """True if a CPU microcode package (amd-ucode or intel-ucode) is installed
        in the target. Best-effort via `pacman -Qq`."""
        for pkg in ("amd-ucode", "intel-ucode"):
            # Best-effort probe: a pacman failure just means "try the next pkg".
            try:
                res = Command.execute("pacman", ["-Qq", pkg], target=self._target())
                if getattr(res, "returncode", 1) == 0:
                    return True
            except Exception:  # nosec B112
                continue
        return False

    def import_state(self, managed=None) -> dict:
        """Capture ``enable_microcode``. It is a bool (the intel-vs-amd package is
        auto-detected at apply from /proc/cpuinfo), so capturing the flag when a
        ucode package is present keeps the microcode initrd on the boot entry after
        a sync — the `amd-ucode`/`intel-ucode` package itself round-trips via
        `packages`, but the flag is what wires it into the bootloader."""
        if self._microcode_installed():
            return {"enable_microcode": True}
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return not self._installed()

    def execute(self) -> None:
        self._install()

    def verify(self) -> bool:
        return self._installed()

    # --- the destructive bit (mocked in tests) ------------------------ #

    def _install(self) -> None:
        root = self._target_root()
        Command.execute("pacman", ["--noconfirm", "-Sy", "archlinux-keyring"], stream=True)
        # A failed pacstrap must abort loudly — continuing to genfstab on a
        # half-installed root silently produces a broken system. It keeps its own
        # rc check below (stream=True doesn't change the returncode contract).
        pacstrap = Command.execute("pacstrap", ["-K", root] + self.packages, stream=True)
        if getattr(pacstrap, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"pacstrap failed (rc={getattr(pacstrap, 'returncode', '?')}); "
                f"base system not installed into {root}."
            )
        _report_hook_failures(pacstrap.stdout)
        result = Command.execute("genfstab", ["-U", root])
        if getattr(result, "returncode", 0) != 0:
            raise CommandExecutionError(
                f"genfstab failed (rc={getattr(result, 'returncode', '?')})."
            )
        out = result.stdout
        fstab = out.decode() if isinstance(out, bytes) else (out or "")
        # An empty fstab is worse than an error: the installed system would boot
        # with no mounts declared. Refuse to write it.
        if not fstab.strip():
            raise CommandExecutionError(
                "genfstab produced an empty fstab; refusing to write a mountless "
                "/etc/fstab (the installed system would be non-bootable)."
            )
        with open(self._p("/etc/fstab"), "a") as f:
            f.write(fstab)
