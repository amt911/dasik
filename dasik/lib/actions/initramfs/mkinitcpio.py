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
                elif h in ("usr", "resume", "consolefont"):
                    continue
                else:
                    new.append(h)
            hooks = new
        if self.root_fs == "btrfs" and "btrfs" not in hooks:
            if self.has_encryption:
                insert_after = "systemd"
            else:
                insert_after = next((c for c in ("resume", "usr", "udev") if c in hooks), None)
            if insert_after and insert_after in hooks:
                hooks.insert(hooks.index(insert_after) + 1, "btrfs")
            else:
                hooks.insert(1, "btrfs")
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
        with open(path, "w") as f:
            for line in lines:
                if re.match(r"^HOOKS=", line):
                    f.write(f"# {line}")
                    f.write(f"HOOKS=({hooks_str})\n")
                else:
                    f.write(line)
        if self.target is not None:
            Command.execute("mkinitcpio", ["-P"], target=self.target)
        else:
            Command.execute("mkinitcpio", ["-P"], True)
