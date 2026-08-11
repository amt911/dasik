"""mkinitcpio backend: derive HOOKS from the disk config + run mkinitcpio -P."""
from __future__ import annotations
import re
from typing import List, Optional
from .base import InitramfsBackend
from ...command_worker.command_worker import Command

_CONF = "/etc/mkinitcpio.conf"
_DEFAULT_HOOKS = ["base", "udev", "autodetect", "modconf", "kms",
                  "keyboard", "keymap", "consolefont", "block",
                  "filesystems", "fsck"]


class MkinitcpioBackend(InitramfsBackend):

    def _raw_hooks(self) -> Optional[List[str]]:
        try:
            with open(self._path(_CONF), "r") as f:
                for line in f:
                    m = re.match(r"^HOOKS=\((.+)\)", line)
                    if m:
                        return m.group(1).split()
        except FileNotFoundError:
            return None
        return None

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

    def desired_value(self) -> str:
        base = self._raw_hooks() or _DEFAULT_HOOKS
        return " ".join(self._compute(base))

    def actual_value(self) -> Optional[str]:
        raw = self._raw_hooks()
        return " ".join(raw) if raw is not None else None

    def apply(self) -> None:
        hooks_str = self.desired_value()
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
                    f.write(f"HOOKS=({hooks_str})\n")
                    found = True
                else:
                    f.write(line)
            if not found:                      # no existing HOOKS line → add one
                f.write(f"HOOKS=({hooks_str})\n")
        if self.target is not None:
            Command.execute("mkinitcpio", ["-P"], target=self.target, check=True)
        else:
            Command.execute("mkinitcpio", ["-P"], True, check=True)
