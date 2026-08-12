"""Action: install the bootloader and create the base boot entry (v3 domain "bootloader").

Installs systemd-boot (`bootctl install`) or GRUB (`grub-install` + `grub-mkconfig`)
and writes the initial loader entry. `KernelCmdlineAction` maintains the entry
params afterward. Idempotent via an install marker. Install-only. Target-aware.
The destructive install lives in `_install()` (mocked in tests).
"""
from __future__ import annotations
import os
import re
import shutil
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from .partition_utils import mounts_root
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError, CommandNotFoundException
from ..logging import run_logger
from ..state.change import Change, Op

_DOMAIN = "bootloader"
_SDBOOT = "sd-boot"
_GRUB = "grub"
_SDBOOT_MARKER = "/boot/EFI/systemd/systemd-bootx64.efi"
_GRUB_MARKER = "/boot/grub/grub.cfg"
_MARKERS = {_SDBOOT: _SDBOOT_MARKER, _GRUB: _GRUB_MARKER}
_FALLBACK_ENTRY = "/boot/loader/entries/arch-fallback.conf"
_FALLBACK_ITEM = "fallback-entry"
_MAIN_INITRD = "/initramfs-linux.img"
_FALLBACK_INITRD = "/initramfs-linux-fallback.img"

# What each loader leaves behind on the ESP. Fixed constants, never derived from
# config: nothing user-controlled may reach a recursive delete.
_SDBOOT_LEFTOVERS = ("/boot/EFI/systemd", "/boot/loader/entries",
                     "/boot/loader/loader.conf", "/boot/loader/random-seed")
_GRUB_LEFTOVERS = ("/boot/grub", "/boot/EFI/GRUB")

# The NVRAM entry `grub-install --bootloader-id=GRUB` creates. systemd-boot's
# ("Linux Boot Manager") is `bootctl remove`'s own business.
_GRUB_NVRAM_LABEL = "GRUB"
_EFIBOOTMGR_LINE = re.compile(r"^Boot([0-9A-Fa-f]{4})\*?\s+(.*?)\s*$")


class BootloaderAction(AbstractAction):
    """Install the bootloader (systemd-boot or GRUB) declaratively."""

    _DOMAIN = _DOMAIN

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self.bootloader: str = cfg.get("bootloader", "grub")
        self.enable_microcode: bool = cfg.get("enable_microcode", False)

    @property
    def name(self) -> str:
        return "Bootloader"

    @property
    def is_optional(self) -> bool:
        return False

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- config-derived helpers --------------------------------------- #

    def _root_label(self) -> str:
        disks = self._cfg.get("disks") or {}
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if mounts_root(part):
                    return part.get("label", "root")
        return "root"

    def _root_param(self) -> str:
        """The ``root=`` cmdline the base boot entry should carry.

        For an encrypted root this MUST be ``root=/dev/mapper/<luks_name>`` — the
        SAME token KernelCmdlineAction derives — so the two agree and the entry
        has a single, correct ``root=``. Emitting ``root=LABEL=…`` here (the old
        behaviour) left a stale second ``root=`` that kernel-cmdline could not
        remove (it's drift), producing a duplicate and a non-idempotent re-apply.
        """
        disks = self._cfg.get("disks") or {}
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if mounts_root(part):
                    if part.get("encrypt"):
                        return f"root=/dev/mapper/{part.get('luks_name', 'cryptroot')}"
                    return f"root=LABEL={part.get('label', 'root')}"
        return "root=LABEL=root"

    def _is_sdboot(self) -> bool:
        return self.bootloader in (_SDBOOT, "systemd-boot")

    def _desired(self) -> str:
        """The declared loader, canonicalized.

        ``systemd-boot`` is an accepted alias of ``sd-boot``; domain items use
        the canonical name only, or a manifest written under the alias would
        read as a switch on the next plan and remove the very loader it keeps.
        """
        return _SDBOOT if self._is_sdboot() else _GRUB

    def _installed(self) -> bool:
        return os.path.exists(self._p(_MARKERS[self._desired()]))

    def _installed_loaders(self) -> set:
        """Every loader with a marker on the ESP — not only the declared one.

        Probing just the declared loader made a leftover GRUB (or systemd-boot)
        invisible: nothing planned its removal and a switch left both on the
        ESP and in NVRAM.
        """
        return {name for name, marker in _MARKERS.items()
                if os.path.exists(self._p(marker))}

    # --- v3 contract -------------------------------------------------- #

    def _fallback_present(self) -> bool:
        return os.path.exists(self._p(_FALLBACK_ENTRY))

    def actual(self) -> set:
        found = self._installed_loaders()
        if self._fallback_present():
            found.add(_FALLBACK_ITEM)
        return found

    def managed_keys(self) -> dict:
        # Ownership is INTENT, like every other domain: a stale loader found on
        # the machine must never be recorded as something dasik wants.
        desired = [self._desired()]
        if self._is_sdboot():
            desired.append(_FALLBACK_ITEM)
        return {self._DOMAIN: sorted(desired)}

    def plan(self, managed) -> list:
        have = self.actual()
        desired = self._desired()
        changes = []
        if desired not in have:
            changes.append(Change(self._DOMAIN, Op.INSTALL, desired,
                                  reason="install bootloader"))
        # The rescue entry is a domain item of its own, so a machine whose
        # bootloader is ALREADY installed still gets it on the next apply.
        if self._is_sdboot() and _FALLBACK_ITEM not in have:
            changes.append(Change(self._DOMAIN, Op.INSTALL, _FALLBACK_ITEM,
                                  reason="rescue boot entry"))
        # Switching away leaves the old loader behind unless it is removed —
        # deliberately regardless of manifest ownership, since after a `sync`
        # the manifest is empty and two loaders on one ESP is not a state
        # anyone wants. `plan` always announces it first.
        for stale in sorted(have & set(_MARKERS)):
            if stale != desired:
                changes.append(Change(self._DOMAIN, Op.REMOVE, stale,
                                      reason=f"switched to {desired}"))
        if not self._is_sdboot() and _FALLBACK_ITEM in have:
            changes.append(Change(self._DOMAIN, Op.REMOVE, _FALLBACK_ITEM,
                                  reason=f"switched to {desired}"))
        return changes

    def apply(self, changes) -> None:
        items = {c.item for c in changes}
        # Uninstall FIRST: installing before removing leaves two loaders
        # fighting over the ESP mid-apply, and the removal would then delete
        # directories the new loader has just written.
        for stale in sorted(items & set(_MARKERS)):
            if stale != self._desired():
                self._uninstall(stale)
        if self._desired() in items:
            self._install()                 # writes both entries for sd-boot
        if _FALLBACK_ITEM in items and self._is_sdboot() \
                and not os.path.exists(self._p(_FALLBACK_ENTRY)):
            self._write_fallback_entry()

    def import_state(self, managed=None) -> dict:
        """Capture which bootloader is actually installed, by its on-disk marker
        (independent of the seed's `bootloader` value). systemd-boot's EFI stub or
        GRUB's grub.cfg — so a synced config keeps the right bootloader instead of
        defaulting to grub."""
        if os.path.exists(self._p(_SDBOOT_MARKER)):
            return {"bootloader": "sd-boot"}
        if os.path.exists(self._p(_GRUB_MARKER)):
            return {"bootloader": "grub"}
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        # "Anything left to do" — the loader marker OR the rescue entry.
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

    def verify(self) -> bool:
        # A stale loader still on the ESP is not a converged bootloader domain:
        # the firmware can still boot it.
        return self._installed_loaders() == {self._desired()}

    # --- uninstalling a loader we switched away from ------------------- #

    def _rm(self, canonical: str) -> None:
        path = self._p(canonical)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.lexists(path):
            os.remove(path)

    def _best_effort(self, cmd: str, args: List[str]):
        """Run a firmware command that is allowed to fail.

        A chroot without ``efivars`` (container, VM build, `--target /` on a
        BIOS box) cannot touch NVRAM, and that must not abort an otherwise-good
        install: the on-ESP files are removed either way. Only NVRAM work goes
        through here — file removal is not best-effort.
        """
        try:
            return Command.execute(cmd, args, target=self._target(), check=True)
        except (CommandExecutionError, CommandNotFoundException) as exc:
            run_logger.get().warning(
                f"could not update firmware boot entries via {cmd}", detail=str(exc))
            return None

    @staticmethod
    def _decode(out) -> str:
        if isinstance(out, bytes):
            return out.decode("utf-8", errors="replace")
        return out or ""

    def _nvram_entries(self, label: str) -> List[str]:
        """Boot entry numbers whose label is exactly *label*."""
        result = self._best_effort("efibootmgr", [])
        if result is None:
            return []
        found = []
        for line in self._decode(getattr(result, "stdout", "")).splitlines():
            m = _EFIBOOTMGR_LINE.match(line)
            # The label runs up to the tab that precedes the device path.
            if m and m.group(2).split("\t")[0].strip() == label:
                found.append(m.group(1))
        return found

    def _uninstall(self, loader: str) -> None:
        """Remove *loader* from the ESP and from the firmware's boot menu."""
        if loader == _SDBOOT:
            # bootctl clears both the EFI binaries and the "Linux Boot Manager"
            # NVRAM entry; it leaves loader.conf and entries/*.conf behind, and
            # it fails without efivars — so the files are removed explicitly
            # afterwards, which is also what makes the marker deterministically
            # gone.
            self._best_effort("bootctl", ["remove"])
            leftovers: tuple = _SDBOOT_LEFTOVERS
        else:
            leftovers = _GRUB_LEFTOVERS
        for path in leftovers:
            self._rm(path)
        if loader == _GRUB:
            for num in self._nvram_entries(_GRUB_NVRAM_LABEL):
                self._best_effort("efibootmgr", ["-b", num, "-B"])

    # --- destructive install (mocked in tests) ------------------------ #

    def _ucode_initrds(self) -> List[str]:
        # Only reference a ucode image that ACTUALLY EXISTS on the ESP. Listing
        # both /intel-ucode.img and /amd-ucode.img unconditionally makes
        # systemd-boot fail at boot with "Error preparing initrd: Not found" on
        # the one that isn't installed (only amd-ucode OR intel-ucode is present,
        # per the CPU). By install time the ucode package's .img is already in
        # /boot, so an existence check picks exactly the right one.
        if not self.enable_microcode:
            return []
        return [img for img in ("/intel-ucode.img", "/amd-ucode.img")
                if os.path.exists(self._p("/boot" + img))]

    def _fallback_initrd(self) -> str:
        """mkinitcpio's fallback image when the ESP has one, else the same image
        the main entry loads. dracut builds no fallback image, so there the entry
        is a duplicate — still worth having: it survives an edit that breaks
        arch.conf."""
        if os.path.exists(self._p("/boot" + _FALLBACK_INITRD)):
            return _FALLBACK_INITRD
        return _MAIN_INITRD

    def _write_fallback_entry(self) -> None:
        path = self._p(_FALLBACK_ENTRY)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["title Arch Linux (fallback initramfs)", "linux /vmlinuz-linux"]
        lines += [f"initrd {img}" for img in self._ucode_initrds()]
        lines.append(f"initrd {self._fallback_initrd()}")
        lines.append(f"options {self._root_param()} rw")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def _install(self) -> None:
        # Every mutating boot command runs with check=True: a bootloader that
        # failed to install must abort the action, never be followed by
        # loader.conf/arch.conf (files that make an unbootable ESP look applied).
        t = self._target()
        if self._is_sdboot():
            Command.execute("bootctl", ["install"], target=t, check=True)
            loader = self._p("/boot/loader/loader.conf")
            os.makedirs(os.path.dirname(loader), exist_ok=True)
            with open(loader, "w") as f:
                f.write("default arch\ntimeout 3\nconsole-mode max\n")
            entries_dir = self._p("/boot/loader/entries")
            os.makedirs(entries_dir, exist_ok=True)
            lines = ["title Arch Linux", "linux /vmlinuz-linux"]
            for img in self._ucode_initrds():
                lines.append(f"initrd {img}")
            lines.append(f"initrd {_MAIN_INITRD}")
            lines.append(f"options {self._root_param()} rw")
            with open(os.path.join(entries_dir, "arch.conf"), "w") as f:
                f.write("\n".join(lines) + "\n")
            self._write_fallback_entry()
        else:
            Command.execute("pacman", ["--noconfirm", "--needed", "-S", "grub", "efibootmgr"],
                            target=t, check=True)
            Command.execute("grub-install", [
                "--target=x86_64-efi", "--efi-directory=/boot", "--bootloader-id=GRUB",
            ], target=t, check=True)
            # …and again into the removable path, \EFI\BOOT\BOOTX64.EFI.
            # The first call writes /EFI/GRUB and an NVRAM entry, and a machine
            # that boots only from NVRAM stops booting the moment the firmware
            # forgets it: a CMOS reset, a disk moved to another board, a VM
            # handed a fresh OVMF_VARS (which is how this was found — the guest
            # fell through to PXE). `bootctl install` writes both paths, so
            # sd-boot machines already survive it; grub has to be asked.
            Command.execute("grub-install", [
                "--target=x86_64-efi", "--efi-directory=/boot", "--removable",
            ], target=t, check=True)
            Command.execute("grub-mkconfig", ["-o", "/boot/grub/grub.cfg"],
                            target=t, check=True)
