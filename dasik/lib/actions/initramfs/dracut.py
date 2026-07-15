"""dracut backend: derive /etc/dracut.conf.d/dasik.conf + /etc/crypttab + run dracut."""
from __future__ import annotations
import os
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command
from ..luks_uuid import luks_uuid

_CONF = "/etc/dracut.conf.d/dasik.conf"
_CRYPTTAB = "/etc/crypttab"


class DracutBackend(InitramfsBackend):

    def _add_modules(self) -> List[str]:
        """dracut modules included via add_dracutmodules.

        In hostonly mode (which we use for encryption) dracut AUTO-detects the
        crypt + filesystem modules from the running root, so listing `crypt`/`btrfs`
        explicitly is unnecessary and — for `crypt` — actively harmful: it pulls
        dracut's non-systemd crypt handler, which competes with the systemd module's
        systemd-cryptsetup and leaves rd.luks.name unparsed (boot hangs waiting for
        /dev/mapper/<name>). So we mirror a real dracut setup: only `bluetooth` here,
        plus `btrfs` for the rare non-hostonly (unencrypted) btrfs root."""
        mods: List[str] = []
        if self.root_fs == "btrfs" and not self.has_encryption:
            mods.append("btrfs")
        if self.bluetooth_in_initramfs:
            # dracut ships a `bluetooth` module so a paired BT keyboard works at the
            # LUKS passphrase / FIDO2 prompt (mirrors a real add_dracutmodules setup).
            mods.append("bluetooth")
        return mods

    def _force_modules(self) -> List[str]:
        """Modules that must be FORCED into the initramfs.

        `systemd` is required for ANY encrypted root: dasik's kernel cmdline uses
        the systemd-cryptsetup convention (rd.luks.name=<uuid>=<name> +
        rd.luks.options=...), which only the systemd module's cryptsetup generator
        parses. Without it dracut's plain `crypt` module never opens the device and
        the boot hangs waiting for /dev/mapper/<name> → emergency mode. fido2/tpm2
        add their token backends on top (and also need systemd)."""
        mods: List[str] = []
        if self.has_encryption or self.has_fido2 or self.has_tpm2:
            mods.append("systemd")
        if self.has_fido2:
            mods.append("fido2")
        if self.has_tpm2:
            mods.append("tpm2-tss")
        return mods

    def desired_value(self) -> str:
        add_mods = self._add_modules()
        force_mods = self._force_modules()
        if not add_mods and not force_mods:
            return ""
        lines = ["# Managed by dasik"]
        # hostonly bakes the detected crypt device into the initramfs so
        # systemd-cryptsetup knows what to open (and prompts) at boot; without it a
        # dracut encrypted root hangs waiting for /dev/mapper/<name>. Required for
        # ANY encryption (matches a real dracut LUKS setup).
        if self.has_encryption:
            lines.append('hostonly="yes"')
        if force_mods:
            lines.append(f'force_add_dracutmodules+=" {" ".join(force_mods)} "')
        if add_mods:
            lines.append(f'add_dracutmodules+=" {" ".join(add_mods)} "')
        return "\n".join(lines) + "\n"

    def crypttab(self) -> str:
        """`/etc/crypttab` content for the encrypted root(s).

        dracut regenerates the initramfs inside arch-chroot at install time, where
        hostonly detection of the live crypt device is unreliable. An explicit
        crypttab entry (name UUID=<uuid> none luks) gives systemd-cryptsetup the
        info to bake the crypt-open into the initramfs, so the boot actually asks
        for the passphrase instead of hanging on /dev/mapper/<name>."""
        lines: List[str] = []
        disks = self.config.get("disks", {})
        if isinstance(disks, dict):
            for disk in disks.get("disks", []):
                for part in disk.get("partitions", []):
                    if not part.get("encrypt"):
                        continue
                    name = part.get("luks_name", "cryptroot")
                    uuid = luks_uuid(name, part.get("luks_uuid"))
                    lines.append(f"{name} UUID={uuid} none luks")
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
        if self.target is not None:
            Command.execute("dracut", ["--regenerate-all", "--force"], target=self.target)
        else:
            Command.execute("dracut", ["--regenerate-all", "--force"], True)
