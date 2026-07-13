"""Action: configure kernel command line parameters (bootloader entries).

Supports both GRUB and systemd-boot.
Auto-derives parameters from disk config (encryption, btrfs) and
merges them with explicit ``kernel_cmdline`` entries from the JSON.

Idempotent: only writes when the desired parameters are missing.
"""
from __future__ import annotations
import os
import re
import subprocess
from typing import Any, Dict, List, Optional
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Op


class KernelCmdlineAction(AbstractAction):
    """Set kernel command line parameters declaratively."""

    _DOMAIN = "kernel_cmdline"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self.bootloader: str = cfg.get("bootloader", "grub")
        self.explicit_params: List[str] = cfg.get("kernel_cmdline", [])

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    @property
    def desired_params(self) -> List[str]:
        return self._merge(self._derive_from_disks(), self.explicit_params)

    # ------------------------------------------------------------------ #
    #  portable LUKS UUID resolution (via the open mapping; host-level)
    # ------------------------------------------------------------------ #

    def _luks_backing_device(self, luks_name: str) -> Optional[str]:
        result = Command.execute("cryptsetup", ["status", luks_name])
        if getattr(result, "returncode", 1) != 0:
            return None
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if "device:" in line:
                return line.split("device:")[1].strip()
        return None

    def _resolve_luks_uuid(self, luks_name: str) -> Optional[str]:
        dev = self._luks_backing_device(luks_name)
        if not dev:
            return None
        # Read the LUKS UUID straight from the on-disk header. `blkid` caches in
        # /run and returns a stale/empty result right after `luksFormat`, which
        # left `rd.luks.name` off the FIRST apply → a non-bootable encrypted
        # entry until a redundant second apply. `cryptsetup luksUUID` reads the
        # header directly, so a single apply produces a bootable, idempotent entry.
        result = Command.execute("cryptsetup", ["luksUUID", dev])
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return stdout.strip() or None

    # ------------------------------------------------------------------ #
    #  auto-derivation from disk config (UUID resolved → portable)
    # ------------------------------------------------------------------ #

    def _derive_from_disks(self) -> List[str]:
        params: List[str] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return params

        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("mountpoint") != "/":
                    continue
                if part.get("encrypt"):
                    from dasik.lib.actions.luks_uuid import luks_uuid
                    dm_name = part.get("luks_name", "cryptroot")
                    # Deterministic UUID (same value the disk was formatted with)
                    # — no probe, so this is correct at plan time on the very
                    # first apply, before the disk is even encrypted.
                    uuid = luks_uuid(dm_name, part.get("luks_uuid"))
                    params.append(f"rd.luks.name={uuid}={dm_name}")
                    params.append(f"root=/dev/mapper/{dm_name} rw")

                fs = part.get("filesystem", "")
                if fs == "btrfs":
                    subvols = part.get("btrfs_subvolumes", [])
                    root_sv = next((s for s in subvols if s.get("mountpoint") == "/"), None)
                    sv_name = root_sv["name"] if root_sv else "@"
                    options = root_sv.get("mount_options", ["compress-force=zstd"]) if root_sv else ["compress-force=zstd"]
                    opts_str = ",".join(options + [f"subvol={sv_name}"])
                    params.append(f"rootflags={opts_str}")
        return params

    @staticmethod
    def _merge(auto: List[str], explicit: List[str]) -> List[str]:
        """Merge auto-derived and explicit params, explicit wins on conflict."""
        # Use explicit as base; auto params only added if no explicit
        # param with the same key exists
        explicit_keys = set()
        for p in explicit:
            key = p.split("=")[0] if "=" in p else p
            explicit_keys.add(key)

        merged = list(explicit)
        for p in auto:
            key = p.split("=")[0] if "=" in p else p
            if key not in explicit_keys:
                merged.append(p)
        return merged

    # ------------------------------------------------------------------ #
    #  file manipulation
    # ------------------------------------------------------------------ #

    def _grub_file(self) -> str:
        t = self._target()
        return t.path("/etc/default/grub") if t is not None else "/mnt/etc/default/grub"

    def _sdboot_entries(self) -> List[str]:
        t = self._target()
        entries_dir = t.path("/boot/loader/entries") if t is not None else "/mnt/boot/loader/entries"
        if os.path.isdir(entries_dir):
            return [os.path.join(entries_dir, f) for f in os.listdir(entries_dir) if f.endswith(".conf")]
        return []

    # ------------------------------------------------------------------ #
    #  v3 contract (token set)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _tokens(entries: List[str]) -> List[str]:
        out: List[str] = []
        for entry in entries:
            out.extend(entry.split())
        return out

    def _desired_tokens(self) -> List[str]:
        merged = self._merge(self._derive_from_disks(), self.explicit_params)
        seen: set = set()
        deduped: List[str] = []
        for tok in self._tokens(merged):
            if tok not in seen:
                seen.add(tok)
                deduped.append(tok)
        return deduped

    def _current_cmdline(self) -> str:
        if self.bootloader == "grub":
            return self._current_params_grub()
        entries = self._sdboot_entries()
        return self._current_params_sdboot(entries[0]) if entries else ""

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return set(self._current_cmdline().split())

    def plan(self, managed):
        from ..state.set_math import compute_changes
        changes, _drift = compute_changes(
            self._DOMAIN,
            desired=self._desired_tokens(),
            managed=managed,
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: self._desired_tokens()}

    def import_state(self, managed=None) -> dict:
        # Round-trip the declared explicit params only. Never emit the resolved
        # LUKS UUID — keeping the config portable across machines.
        return {self._DOMAIN: list(self.explicit_params)}

    def _new_tokens(self, changes) -> List[str]:
        installs = [c.item for c in changes if c.op is Op.INSTALL]
        removes = {c.item for c in changes if c.op is Op.REMOVE}
        current = [t for t in self._current_cmdline().split() if t not in removes]
        for tok in installs:
            if tok not in current:
                current.append(tok)
        return current

    def apply(self, changes) -> None:
        if self._target() is None or not changes:
            return
        line = " ".join(self._new_tokens(changes))
        if self.bootloader == "grub":
            self._write_grub(line)
            Command.execute("grub-mkconfig", ["-o", "/boot/grub/grub.cfg"], target=self._target())
        else:
            for entry in self._sdboot_entries():
                self._write_sdboot(entry, line)

    def _write_grub(self, line: str) -> None:
        path = self._grub_file()
        with open(path, "r") as f:
            text = f.read()
        text = re.sub(r'^GRUB_CMDLINE_LINUX="(.*)"',
                      f'GRUB_CMDLINE_LINUX="{line}"', text, flags=re.MULTILINE)
        with open(path, "w") as f:
            f.write(text)

    def _write_sdboot(self, entry_file: str, line: str) -> None:
        with open(entry_file, "r") as f:
            lines = f.readlines()
        with open(entry_file, "w") as f:
            for ln in lines:
                if ln.startswith("options "):
                    f.write(f"options {line}\n")
                else:
                    f.write(ln)

    def _current_params_grub(self) -> str:
        path = self._grub_file()
        if not os.path.exists(path):
            return ""
        with open(path, "r") as f:
            for line in f:
                m = re.match(r'^GRUB_CMDLINE_LINUX="(.+)"', line)
                if m:
                    return m.group(1)
        return ""

    def _current_params_sdboot(self, entry_file: str) -> str:
        if not os.path.exists(entry_file):
            return ""
        with open(entry_file, "r") as f:
            for line in f:
                if line.startswith("options "):
                    return line[len("options "):].strip()
        return ""

    def _param_present(self, current: str, param: str) -> bool:
        """Check if a kernel param (key=val or flag) is already present."""
        if "=" in param:
            key = param.split("=")[0]
            return key in current
        return param in current.split()

    def _missing_params(self) -> List[str]:
        if self.bootloader == "grub":
            current = self._current_params_grub()
        else:
            entries = self._sdboot_entries()
            current = self._current_params_sdboot(entries[0]) if entries else ""
        return [p for p in self.desired_params if not self._param_present(current, p)]

    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return "Kernel Command Line"

    @property
    def is_optional(self) -> bool:
        return True

    def is_needed(self) -> bool:
        if not self.desired_params:
            return False
        return bool(self._missing_params())

    def execute(self) -> None:
        missing = self._missing_params()
        if not missing:
            return

        addition = " ".join(missing)

        if self.bootloader == "grub":
            self._append_grub(addition)
            # Regenerate grub config
            subprocess.run(["arch-chroot", "/mnt", "grub-mkconfig", "-o", "/boot/grub/grub.cfg"], check=True)
        else:
            for entry in self._sdboot_entries():
                self._append_sdboot(entry, addition)

    def _append_grub(self, addition: str) -> None:
        path = self._grub_file()
        with open(path, "r") as f:
            text = f.read()
        # Append to GRUB_CMDLINE_LINUX
        text = re.sub(
            r'^(GRUB_CMDLINE_LINUX=")(.*)"',
            rf'\1\2 {addition}"',
            text,
            flags=re.MULTILINE,
        )
        with open(path, "w") as f:
            f.write(text)

    def _append_sdboot(self, entry_file: str, addition: str) -> None:
        with open(entry_file, "r") as f:
            lines = f.readlines()
        with open(entry_file, "w") as f:
            for line in lines:
                if line.startswith("options "):
                    f.write(line.rstrip() + " " + addition + "\n")
                else:
                    f.write(line)

    def verify(self) -> bool:
        return not self._missing_params()
