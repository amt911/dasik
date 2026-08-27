"""mkinitcpio backend: HOOKS in the main conf + a dasik drop-in, then mkinitcpio -P."""
from __future__ import annotations
import os
import re
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/mkinitcpio.conf"
# mkinitcpio reads /etc/mkinitcpio.conf.d/*.conf as drop-ins (Arch wiki,
# mkinitcpio#Configuration). dasik owns this one file entirely, which is what
# makes its additions removable.
_DROPIN = "/etc/mkinitcpio.conf.d/dasik.conf"
_DROPIN_HEADER = "# Managed by dasik\n"
_DEFAULT_HOOKS = ["base", "udev", "autodetect", "modconf", "kms",
                  "keyboard", "keymap", "consolefont", "block",
                  "filesystems", "fsck"]
# Filesystem -> the modules the initramfs needs to mount it. FAT is the odd one:
# without its NLS charset modules the mount fails with "IO charset cp437 not
# found", so a pendrive keyfile on the commonest filesystem would be unreadable.
_FS_MODULES = {
    "vfat": ["vfat", "nls_cp437", "nls_iso8859-1"],
    "exfat": ["exfat", "nls_utf8"],
}


class MkinitcpioBackend(InitramfsBackend):

    CONF_DIR = "/etc/mkinitcpio.conf.d"

    def _raw_entries(self, directive: str) -> Optional[List[str]]:
        """The words inside `DIRECTIVE=(…)` in the on-disk conf, or None."""
        try:
            with open(self._path(_CONF), "r") as f:
                for line in f:
                    m = re.match(rf"^{directive}=\((.*)\)", line)
                    if m:
                        return m.group(1).split()
        except FileNotFoundError:
            return None
        return None

    def _raw_hooks(self) -> Optional[List[str]]:
        return self._raw_entries("HOOKS")

    @staticmethod
    def _merge(base: Optional[List[str]], extra: List[str]) -> List[str]:
        """base + extra, order-preserving and deduplicated.

        The user's own MODULES/FILES entries are never dropped: dasik adds what
        the config requires on top of what is already declared.
        """
        merged = list(base or [])
        for item in extra:
            if item not in merged:
                merged.append(item)
        return merged

    def _compute(self, base: List[str]) -> List[str]:
        hooks = list(base)
        if "keyboard" in hooks and "autodetect" in hooks:
            hooks = [h for h in hooks if h != "keyboard"]
            hooks.insert(hooks.index("autodetect"), "keyboard")
        if self.has_encryption:
            new: List[str] = []
            for h in hooks:
                if h == "udev":
                    new.append("systemd")
                elif h == "keymap":
                    new.append("sd-vconsole")
                elif h == "block":
                    new.append(h)
                    new.append("sd-encrypt")
                elif h in ("usr", "consolefont"):
                    continue
                elif h == "resume":
                    # Kept ONLY when the config hibernates. The encrypted
                    # rewrite used to drop it unconditionally, removing from a
                    # hibernating system the very hook that resumes it: on the
                    # systemd path this hook is what installs
                    # systemd-hibernate-resume, on the busybox path it is what
                    # performs the resume.
                    if self.has_hibernation:
                        new.append(h)
                else:
                    new.append(h)
            hooks = new
        if self.root_fs == "btrfs" and "btrfs" not in hooks:
            insert_after: "str | None"
            if self.has_encryption:
                insert_after = "systemd"
            else:
                insert_after = next((c for c in ("resume", "usr", "udev") if c in hooks), None)
            if insert_after and insert_after in hooks:
                hooks.insert(hooks.index(insert_after) + 1, "btrfs")
            else:
                hooks.insert(1, "btrfs")
        # Hibernation: the hook must exist, and it must run BEFORE the root is
        # mounted — resuming on top of a mounted root eats the filesystem. After
        # sd-encrypt/encrypt/block so the device it resumes from is open.
        if self.has_hibernation and "resume" not in hooks:
            anchor = next((h for h in ("filesystems", "fsck") if h in hooks), None)
            hooks.insert(hooks.index(anchor) if anchor else len(hooks), "resume")
        # Plymouth: after systemd/udev (it needs the device manager up) and
        # BEFORE sd-encrypt/encrypt — the wiki is explicit that a plymouth hook
        # placed after the crypt hook never takes over the passphrase prompt,
        # which on an encrypted machine means it cannot be unlocked at all.
        if not self.has_plymouth:
            # …and it GOES when the block goes. The computation starts from the
            # hooks on disk and layers what the config asks for, so nothing used
            # to subtract what it had stopped asking for: dropping the plymouth
            # block removed the PACKAGE and left the hook, and `mkinitcpio -P`
            # then fails with "Hook 'plymouth' cannot be found" — this apply and
            # every kernel update after it, until somebody edits the file.
            hooks = [h for h in hooks if h != "plymouth"]
        if self.has_plymouth and "plymouth" not in hooks:
            after = next((h for h in ("systemd", "udev", "base") if h in hooks), None)
            index = hooks.index(after) + 1 if after else 0
            for blocker in ("sd-encrypt", "encrypt"):
                if blocker in hooks:
                    index = min(index, hooks.index(blocker))
            hooks.insert(index, "plymouth")

        seen: set = set()
        deduped: List[str] = []
        for h in hooks:
            if h not in seen:
                seen.add(h)
                deduped.append(h)
        return deduped

    def _modules(self) -> List[str]:
        """Kernel modules the image needs beyond what the conf already declares.

        Today: the key device's filesystem, without which the initramfs cannot
        read the pendrive the keyfile lives on. FAT also needs its NLS charset
        modules — mounting vfat without them fails with "IO charset cp437 not
        found" (dracut's fs-lib pulls them in on its own).
        """
        modules: List[str] = []
        for fs in self.keydev_filesystems:
            for module in _FS_MODULES.get(fs, [fs]):
                if module not in modules:
                    modules.append(module)
        return modules

    def _dropin(self) -> str:
        """dasik's own /etc/mkinitcpio.conf.d fragment.

        Additions live in a drop-in rather than in the user's arrays for one
        reason: they can be taken BACK. Merging into ``MODULES=()`` in the main
        conf leaves the module — and, far worse, a ``FILES=(/keyfile)`` that
        keeps baking a LUKS key into every image — behind forever once the
        unlock is un-declared, because nothing records which entries were ours.
        """
        lines: List[str] = []
        modules = self._modules()
        if modules:
            lines.append(f"MODULES+=({' '.join(modules)})")
        if self.embedded_keyfiles:
            lines.append(f"FILES+=({' '.join(self.embedded_keyfiles)})")
        if self.plymouth_theme:
            # Not a directive — a fingerprint. A theme change rewrites only
            # plymouthd.conf, so without something here the value would be
            # unchanged, the plan silent, and `mkinitcpio -P` never re-run,
            # leaving the old theme in the image (the wiki requires a rebuild).
            lines.append(f"# plymouth theme: {self.plymouth_theme}")
        if not lines:
            return ""
        return _DROPIN_HEADER + "\n".join(lines) + "\n"

    @staticmethod
    def _render(directive: str, entries: List[str]) -> str:
        return f"{directive}=({' '.join(entries)})"

    def _read_dropin(self) -> str:
        try:
            with open(self._path(_DROPIN), "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def desired_value(self) -> str:
        hooks = self._render("HOOKS", self._compute(self._raw_hooks() or _DEFAULT_HOOKS))
        dropin = self._dropin()
        return f"{hooks}\n{dropin}" if dropin else hooks

    def actual_value(self) -> Optional[str]:
        raw_hooks = self._raw_hooks()
        if raw_hooks is None:
            return None
        hooks = self._render("HOOKS", raw_hooks)
        dropin = self._read_dropin()
        return f"{hooks}\n{dropin}" if dropin else hooks

    def apply(self) -> None:
        hooks = self._compute(self._raw_hooks() or _DEFAULT_HOOKS)
        path = self._path(_CONF)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        found = False
        with open(path, "w") as f:
            for line in lines:
                if re.match(r"^HOOKS=", line):
                    f.write(f"# {line}")
                    f.write(self._render("HOOKS", hooks) + "\n")
                    found = True
                else:
                    f.write(line)
            if not found:                      # no existing HOOKS line → add one
                f.write(self._render("HOOKS", hooks) + "\n")

        # The drop-in is dasik's alone, so it is written — or REMOVED — whole.
        dropin_path = self._path(_DROPIN)
        dropin = self._dropin()
        if dropin:
            os.makedirs(os.path.dirname(dropin_path), exist_ok=True)
            with open(dropin_path, "w") as f:
                f.write(dropin)
        else:
            try:
                os.remove(dropin_path)
            except OSError:
                pass

        if self.target is not None:
            Command.execute("mkinitcpio", ["-P"], target=self.target, check=True)
        else:
            Command.execute("mkinitcpio", ["-P"], True, check=True)
