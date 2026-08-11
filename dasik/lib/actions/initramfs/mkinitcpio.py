"""mkinitcpio backend: derive HOOKS/MODULES/FILES from the config + run mkinitcpio -P."""
from __future__ import annotations
import re
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/mkinitcpio.conf"
_DEFAULT_HOOKS = ["base", "udev", "autodetect", "modconf", "kms",
                  "keyboard", "keymap", "consolefont", "block",
                  "filesystems", "fsck"]
# Directives dasik manages, in the order they are rendered. HOOKS is always
# present; the other two only when the config gives them content.
_MANAGED = ("HOOKS", "MODULES", "FILES")


class MkinitcpioBackend(InitramfsBackend):

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

    def _entries(self, directive: str) -> List[str]:
        """The words dasik wants inside `DIRECTIVE=(…)`."""
        if directive == "HOOKS":
            return self._compute(self._raw_hooks() or _DEFAULT_HOOKS)
        if directive == "MODULES":
            # The key device's filesystem module: without it the initramfs
            # cannot read the pendrive the keyfile lives on.
            return self._merge(self._raw_entries("MODULES"), self.keydev_filesystems)
        # FILES: a keyfile with no key device only exists at boot if it travels
        # inside the image.
        return self._merge(self._raw_entries("FILES"), self.embedded_keyfiles)

    @staticmethod
    def _render(directive: str, entries: List[str]) -> str:
        return f"{directive}=({' '.join(entries)})"

    def desired_value(self) -> str:
        lines = [self._render("HOOKS", self._entries("HOOKS"))]
        for directive in ("MODULES", "FILES"):
            entries = self._entries(directive)
            if entries:
                lines.append(self._render(directive, entries))
        return "\n".join(lines)

    def actual_value(self) -> Optional[str]:
        raw_hooks = self._raw_hooks()
        if raw_hooks is None:
            return None
        lines = [self._render("HOOKS", raw_hooks)]
        for directive in ("MODULES", "FILES"):
            entries = self._raw_entries(directive)
            if entries:
                lines.append(self._render(directive, entries))
        return "\n".join(lines)

    def apply(self) -> None:
        desired = {d: self._entries(d) for d in _MANAGED}
        path = self._path(_CONF)
        try:
            with open(path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        written: set = set()
        with open(path, "w") as f:
            for line in lines:
                directive = next((d for d in _MANAGED
                                  if re.match(rf"^{d}=", line)), None)
                if directive is None:
                    f.write(line)
                    continue
                # Keep the previous value visible, commented out, exactly as the
                # HOOKS rewrite has always done.
                f.write(f"# {line}")
                if desired[directive]:
                    f.write(self._render(directive, desired[directive]) + "\n")
                written.add(directive)
            for directive in _MANAGED:
                if directive not in written and desired[directive]:
                    f.write(self._render(directive, desired[directive]) + "\n")
        if self.target is not None:
            Command.execute("mkinitcpio", ["-P"], target=self.target, check=True)
        else:
            Command.execute("mkinitcpio", ["-P"], True, check=True)
