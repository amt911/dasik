"""dracut backend: derive /etc/dracut.conf.d/dasik.conf + /etc/crypttab + run dracut."""
from __future__ import annotations
import glob
import os
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command
from ...exceptions.exceptions import CommandExecutionError
from ..luks_uuid import luks_uuid
from ..partition_utils import keydev_spec, mounts_root
from ..swap_encryption import crypttab_line, random_swap_partitions, swap_names

_CONF_D = "/etc/dracut.conf.d"
_CONF = f"{_CONF_D}/dasik.conf"
_CRYPTTAB = "/etc/crypttab"
_FSTAB = "/etc/fstab"
# Fallback to the passphrase prompt when the key device is absent (mirrors
# KernelCmdlineAction._KEYFILE_TIMEOUT — same policy, two files that describe
# the same unlock).
_KEYFILE_TIMEOUT = "keyfile-timeout=10s"
# Written by the `plymouth` expand toggle; an input to the image, not to this
# file's own content (kept as a literal so the backend stays free of imports
# from the expand layer).
_PLYMOUTHD_CONF = "/etc/plymouth/plymouthd.conf"


class DracutBackend(InitramfsBackend):

    CONF_DIR = _CONF_D

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
        # Hibernation: dracut ships 74resume, but its check() only passes in
        # hostonly mode when a swap is in host_fs_types[] — from a chroot it is
        # not, exactly like the LUKS root above. Verified in a VM on 2026-08-08:
        # without forcing, the image carried no resume module, /sys/power/resume
        # stayed 0:0, and the boot after `systemctl hibernate` was a COLD one.
        if self.has_hibernation:
            mods.append("resume")
        # Plymouth: dracut auto-detects it, but that detection runs in the same
        # chroot where it already dropped systemd-cryptsetup and resume. A
        # declared splash that silently never made it into the image is exactly
        # the failure this file exists to prevent, so force it.
        if self.has_plymouth:
            mods.append("plymouth")
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
        if not add_mods and not force_mods and not self.keydev_filesystems \
                and not self.embedded_keyfiles:
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
        for fs in self.keydev_filesystems:
            # The key device's filesystem: hostonly detection sees the root's
            # filesystems, never the pendrive the keyfile lives on, so the
            # module has to be named explicitly or the key is unreadable.
            lines.append(f'filesystems+=" {fs} "')
        for keyfile in self.embedded_keyfiles:
            # No key device: the file must travel INSIDE the image, or the
            # rd.luks.key dasik writes points at a path the initramfs cannot see.
            lines.append(f'install_items+=" {keyfile} "')
        return "\n".join(lines) + "\n"

    def _captured_crypttab(self) -> str:
        """The verbatim /etc/crypttab captured in the config's ``files`` (if any).

        dracut is the sole writer of /etc/crypttab, but a synced config may carry
        non-root entries here (e.g. an encrypted swap) that must be preserved.
        DropFilesAction yields writing the file; the content still flows through
        here as the source of those non-root lines."""
        for entry in self.config.get("files", []) or []:
            if isinstance(entry, dict):
                path, content = entry.get("path"), entry.get("content", "")
            else:
                path, content = getattr(entry, "path", None), getattr(entry, "content", "")
            if path == _CRYPTTAB:
                return content or ""
        return ""

    def crypttab(self) -> str:
        """Composed ``/etc/crypttab`` — dracut is its single owner.

        Derived root entries (from ``disks``) plus any non-root lines captured in
        the config's ``files`` (e.g. an encrypted swap), deduplicated by mapper
        name with the derived entry winning. The volume that provides ``/`` gets
        ``luks,x-initrd.attach`` so dracut/systemd include and attach it at initrd
        time even when hostonly detection is unsure (crypttab(5) recommends it for
        the device holding ``/``); a non-root encrypted data volume stays plain
        ``luks``. The UUID matches the deterministic one DiskPartitionAction passes
        to ``cryptsetup luksFormat --uuid``."""
        derived: "dict[str, str]" = {}   # mapper -> line (insertion-ordered)
        disks = self.config.get("disks", {})
        if isinstance(disks, dict):
            for disk in disks.get("disks", []):
                for part in disk.get("partitions", []):
                    if not part.get("encrypt"):
                        continue
                    name = part.get("luks_name", "cryptroot")
                    uuid = luks_uuid(name, part.get("luks_uuid"))
                    opts = ["luks"]
                    # The key field, NOT `none`, whenever an unlock keyfile is
                    # declared. VM-proven: with `none` here the initramfs asks
                    # for the passphrase and never looks at the key device, even
                    # though rd.luks.key= is on the cmdline — this line IS the
                    # description of the volume for
                    # systemd-cryptsetup-generator, and `none` means "ask". The
                    # same image booted with rd.luks.crypttab=no (crypttab
                    # ignored, cmdline alone) unlocked from the pendrive
                    # unattended. crypttab(5) takes the same
                    # `<path>:<device spec>` syntax as rd.luks.key.
                    keyfile = part.get("unlock_keyfile")
                    keydev = part.get("unlock_keydev")
                    key_field = "none"
                    if keyfile:
                        key_field = (f"{keyfile}:{keydev_spec(keydev)}"
                                     if keydev else keyfile)
                    # x-initrd.attach: the volume must be open before the real
                    # root is. True for / and for a swap holding the hibernation
                    # image — resume happens in the initramfs or not at all.
                    if mounts_root(part) or part.get("filesystem") == "swap":
                        opts.append("x-initrd.attach")
                    # Same reason as the cmdline's rd.luks.options: without
                    # `discard` the mapping swallows the TRIM that enable_trim
                    # schedules.
                    if self.config.get("enable_trim"):
                        opts.append("discard")
                    # Same reason as the cmdline's keyfile-timeout: a key device
                    # that is not plugged in must fall back to the prompt, not
                    # wait forever. The user's own value wins.
                    if keyfile and keydev and not any(
                            str(o).startswith("keyfile-timeout=")
                            for o in part.get("luks_options", []) or []):
                        opts.append(_KEYFILE_TIMEOUT)
                    opts.extend(o for o in part.get("luks_options", []) or []
                                if str(o).startswith("keyfile-timeout="))
                    derived[name] = f"{name} UUID={uuid} {key_field} {','.join(opts)}"

        # A random-key swap is plain dm-crypt, not LUKS, so it never appears in
        # the loop above — but /etc/crypttab has exactly one owner and this is
        # it. Derived (not captured) so the entry always names the label
        # DiskPartitionAction actually wrote, and so a stale captured line for
        # the same mapper is dropped by the dedup below.
        for part in random_swap_partitions(self.config):
            mapper, _ = swap_names(part)
            derived[mapper] = crypttab_line(part)

        # Non-root captured lines (swap etc.); skip any whose mapper is a derived
        # root name — the derived (correct) entry wins over a stale captured one.
        extra: List[str] = []
        for raw in self._captured_crypttab().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            mapper = line.split()[0]
            if mapper in derived:
                continue
            extra.append(line)

        lines = list(derived.values()) + extra
        return ("# Managed by dasik\n" + "\n".join(lines) + "\n") if lines else ""

    def actual_value(self) -> Optional[str]:
        """Return the on-disk dasik.conf, but ONLY when the on-disk /etc/crypttab
        also matches what we would compose. If the crypttab is missing or drifted,
        return None so InitramfsAction plans a MODIFY and dracut regenerates —
        without this, a crypttab change (dasik.conf unchanged) would be invisible
        and the initramfs would keep a stale crypt setup."""
        try:
            with open(self._path(_CONF), "r", encoding="utf-8") as f:
                conf = f.read()
        except FileNotFoundError:
            return None

        desired_ct = self.crypttab()
        if desired_ct:
            try:
                with open(self._path(_CRYPTTAB), "r", encoding="utf-8") as f:
                    actual_ct = f.read()
            except FileNotFoundError:
                actual_ct = ""
            if actual_ct != desired_ct:
                return None

        # …and ONLY when the images those inputs describe actually exist and are
        # not older than them. Reading intent alone made a failed dracut run look
        # converged: the conf/crypttab are written BEFORE dracut runs, so a crash
        # (or an image later clobbered by mkinitcpio) left the next plan empty
        # while /boot held a stale — or no — initramfs for the declared kernel.
        inputs = self._conf_inputs()
        if self.has_plymouth:
            # A theme change rewrites plymouthd.conf and nothing else: dasik.conf
            # is identical, so without counting the theme file as an input the
            # plan is silent and the image keeps the previous theme. The wiki
            # states the rule outright — rebuild on every theme change.
            inputs.append(self._path(_PLYMOUTHD_CONF))
        if not self._images_current(*inputs):
            return None
        return conf

    def _conf_inputs(self) -> "list[str]":
        """Every file that feeds the image from /etc/dracut.conf.d, plus the
        directory itself.

        dracut reads the WHOLE directory, so dasik.conf is only one of its
        inputs. A drop-in somebody adds through `etc_tree` — an omit_drivers, an
        add_drivers for a storage controller, a keyboard module for the LUKS
        prompt — changes what dracut would produce while leaving dasik.conf
        byte-identical. Counting only our own file made `apply` write that
        drop-in, `plan` report converged, and the image keep its old content
        until an unrelated kernel upgrade happened to run dracut. You could not
        tell "applied" from "ignored", which is the same failure the plymouth
        theme input above exists to prevent.

        The DIRECTORY is an input too, and it is the only thing that can catch a
        REMOVAL: delete a drop-in and every file left behind is still older than
        the image, so the file list alone reports converged while the image is
        still built from the file that is gone. Creating, deleting or renaming
        bumps the directory's mtime; nothing else writes there in steady state,
        so this costs no spurious rebuilds.

        Only `*.conf` counts — that is what dracut reads, and a .pacnew or an
        editor backup left in that directory must not force a rebuild on every
        plan.
        """
        conf_d = self._path(_CONF_D)
        return [conf_d, *sorted(glob.glob(os.path.join(conf_d, "*.conf")))]

    def _images_current(self, *input_paths: str) -> bool:
        """True when every target kernel has an initramfs image at least as new
        as the newest input file. No kernel yet (pre-pacstrap) → nothing to
        verify, so the file compare decides on its own."""
        kernels = self._target_kernels()
        if not kernels:
            return True
        newest_input = 0.0
        for path in input_paths:
            try:
                newest_input = max(newest_input, os.path.getmtime(path))
            except OSError:
                continue
        for _kver, pkgbase in kernels:
            image = self._path(f"/boot/initramfs-{pkgbase}.img")
            try:
                if os.path.getmtime(image) < newest_input:
                    return False
            except OSError:                      # missing image → not converged
                return False
        return True

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
        fstab_abs = self._path(_FSTAB) if self.target is not None else "/mnt" + _FSTAB
        if not os.path.exists(fstab_abs):
            raise CommandExecutionError(
                f"Refusing to run dracut: no {_FSTAB} in the target ({fstab_abs}). "
                "Base install must create it first."
            )

        # `dracut --regenerate-all` names images /boot/initramfs-<kver>.img, but
        # the bootloader entry loads /initramfs-<pkgbase>.img (e.g.
        # initramfs-linux.img). If we used --regenerate-all the boot would keep
        # loading the STALE mkinitcpio image pacstrap left at that name — with no
        # crypt/systemd-cryptsetup — and the encrypted root would hang. So write
        # the pkgbase-named image explicitly, once per target kernel, passing the
        # TARGET's kver (never the chroot host's uname -r).
        kernels = self._target_kernels()
        if not kernels:
            raise CommandExecutionError(
                "Refusing to run dracut: no kernel found under "
                f"{self._path('/usr/lib/modules') if self.target else '/mnt/usr/lib/modules'}."
            )
        for kver, pkgbase in kernels:
            out = f"/boot/initramfs-{pkgbase}.img"
            args = ["--force", "--fstab", out, kver]
            if self.target is not None:
                Command.execute("dracut", args, target=self.target, check=True)
            else:
                Command.execute("dracut", args, run_as_chroot=True, check=True)

    def _target_kernels(self) -> "list[tuple[str, str]]":
        """``(kver, pkgbase)`` for every kernel in the target's
        ``/usr/lib/modules``. ``pkgbase`` (an Arch convention: the file
        ``/usr/lib/modules/<kver>/pkgbase``) is the image basename the bootloader
        entry references, so ``initramfs-<pkgbase>.img`` lines up with it. A
        modules dir without a ``pkgbase`` file is skipped (not a bootable Arch
        kernel)."""
        base = self._path("/usr/lib/modules") if self.target is not None \
            else "/mnt/usr/lib/modules"
        kernels: "list[tuple[str, str]]" = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return kernels
        for kver in names:
            pkgbase_file = os.path.join(base, kver, "pkgbase")
            try:
                with open(pkgbase_file, "r", encoding="utf-8") as f:
                    pkgbase = f.read().strip()
            except OSError:
                continue
            if pkgbase:
                kernels.append((kver, pkgbase))
        return kernels
