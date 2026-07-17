"""dracut backend: derive /etc/dracut.conf.d/dasik.conf + /etc/crypttab + run dracut."""
from __future__ import annotations
import os
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command
from ...exceptions.exceptions import CommandExecutionError
from ..luks_uuid import luks_uuid
from ..partition_utils import mounts_root

_CONF = "/etc/dracut.conf.d/dasik.conf"
_CRYPTTAB = "/etc/crypttab"
_FSTAB = "/etc/fstab"


class DracutBackend(InitramfsBackend):

    def _add_modules(self) -> List[str]:
        """dracut modules included via add_dracutmodules (non-forced).

        Only `bluetooth` (so a paired BT keyboard works at the passphrase / FIDO2
        prompt) and `btrfs` for a non-encrypted btrfs root. The encrypted case
        forces btrfs instead (see _force_modules), because hostonly detection from
        a chroot cannot be trusted to add it."""
        mods: List[str] = []
        if self.root_fs == "btrfs" and not self.has_encryption:
            mods.append("btrfs")
        if self.bluetooth_in_initramfs:
            mods.append("bluetooth")
        return mods

    def _force_modules(self) -> List[str]:
        """Modules FORCED into the initramfs (bypassing each module's check()).

        Forcing matters because dasik runs `dracut` inside `arch-chroot /mnt` at
        install time. There, dracut's hostonly filesystem detection does NOT see
        the target's LUKS root in `host_fs_types[]`, so 71systemd-cryptsetup's
        check() returns non-zero and the module — the systemd-cryptsetup-generator
        + binary that actually opens the device from rd.luks.name / crypttab — is
        silently omitted, and the boot hangs on /dev/mapper/<name>.

        Empirically on dracut 111: `systemd-cryptsetup` declares
        `depends() { deps="crypt systemd-ask-password" ... }`, i.e. `crypt` is a
        DEPENDENCY of the systemd LUKS path, not a competitor. So an encrypted root
        forces `crypt systemd systemd-cryptsetup`; a btrfs-on-LUKS root also forces
        `btrfs`; fido2/tpm2 add their token backends. Order-preserving dedupe."""
        mods: List[str] = []
        if self.has_encryption:
            mods += ["crypt", "systemd", "systemd-cryptsetup"]
            if self.root_fs == "btrfs":
                mods.append("btrfs")
        elif self.has_fido2 or self.has_tpm2:
            mods.append("systemd")
        if self.has_fido2:
            mods.append("fido2")
        if self.has_tpm2:
            mods.append("tpm2-tss")
        # dedupe, preserve order
        seen: set = set()
        deduped: List[str] = []
        for m in mods:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        return deduped

    def desired_value(self) -> str:
        add_mods = self._add_modules()
        force_mods = self._force_modules()
        if not add_mods and not force_mods:
            return ""
        lines = ["# Managed by dasik"]
        if self.has_encryption:
            # hostonly bakes the crypt device in; hostonly_cmdline="no" stops
            # dracut copying the live ISO's kernel cmdline into the image — the
            # bootloader entry dasik writes is the single source of truth.
            lines.append('hostonly="yes"')
            lines.append('hostonly_cmdline="no"')
        if force_mods:
            lines.append(f'force_add_dracutmodules+=" {" ".join(force_mods)} "')
        if add_mods:
            lines.append(f'add_dracutmodules+=" {" ".join(add_mods)} "')
        return "\n".join(lines) + "\n"

    def crypttab(self) -> str:
        """`/etc/crypttab` content for the encrypted volume(s).

        The volume that provides `/` gets `luks,x-initrd.attach` so dracut/systemd
        include and attach it at initrd time even when hostonly detection is unsure
        (crypttab(5) recommends x-initrd.attach for the device holding `/`). A
        non-root encrypted data volume stays plain `luks` — it is opened after
        pivot, not in the initramfs. The UUID matches the deterministic one
        DiskPartitionAction passes to `cryptsetup luksFormat --uuid`."""
        lines: List[str] = []
        disks = self.config.get("disks", {})
        if isinstance(disks, dict):
            for disk in disks.get("disks", []):
                for part in disk.get("partitions", []):
                    if not part.get("encrypt"):
                        continue
                    name = part.get("luks_name", "cryptroot")
                    uuid = luks_uuid(name, part.get("luks_uuid"))
                    options = "luks,x-initrd.attach" if mounts_root(part) else "luks"
                    lines.append(f"{name} UUID={uuid} none {options}")
        return ("# Managed by dasik\n" + "\n".join(lines) + "\n") if lines else ""

    def actual_value(self) -> Optional[str]:
        try:
            with open(self._path(_CONF), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def apply(self) -> None:
        desired = self.desired_value()
        path = self._path(_CONF)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(desired)
        # crypttab must exist BEFORE dracut runs so hostonly bakes the crypt-open.
        crypttab = self.crypttab()
        if crypttab:
            with open(self._path(_CRYPTTAB), "w") as f:
                f.write(crypttab)

        # dracut runs from a chroot whose root != the kernel's real root, so it
        # must read /etc/fstab (--fstab) instead of /proc/self/mountinfo, or it
        # derives the wrong root and builds a non-booting image. Refuse to
        # regenerate without one (BaseInstallAction/genfstab writes it first).
        if self.target is not None:
            if not os.path.exists(self._path(_FSTAB)):
                raise CommandExecutionError(
                    f"Refusing to run dracut: no {_FSTAB} in the target "
                    f"({self._path(_FSTAB)}). Base install must create it first."
                )
            Command.execute(
                "dracut", ["--regenerate-all", "--force", "--fstab"],
                target=self.target, check=True,
            )
        else:
            if not os.path.exists("/mnt" + _FSTAB):
                raise CommandExecutionError(
                    f"Refusing to run dracut: no /mnt{_FSTAB}."
                )
            Command.execute(
                "dracut", ["--regenerate-all", "--force", "--fstab"],
                run_as_chroot=True, check=True,
            )
