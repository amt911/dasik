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
from .partition_utils import mounts_root
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
                # LUKS parameters belong to EVERY encrypted partition, not only
                # the one providing /: a second device (swap for hibernation, an
                # encrypted /home) is left closed by the initramfs without its
                # own rd.luks.name, and /etc/crypttab alone runs too late.
                if part.get("encrypt"):
                    from dasik.lib.actions.luks_uuid import luks_uuid
                    dm_name = part.get("luks_name", "cryptroot")
                    # Deterministic UUID (same value the disk was formatted with)
                    # — no probe, so this is correct at plan time on the very
                    # first apply, before the disk is even encrypted.
                    uuid = luks_uuid(dm_name, part.get("luks_uuid"))
                    params.append(f"rd.luks.name={uuid}={dm_name}")
                    if mounts_root(part):
                        params.append(f"root=/dev/mapper/{dm_name} rw")
                    keyfile = part.get("unlock_keyfile")
                    if keyfile:
                        # Automatic unlock via a keyfile (e.g. on a pendrive);
                        # append the key device UUID when given so the initramfs
                        # locates it. The passphrase still works as a fallback.
                        keydev = part.get("unlock_keydev")
                        key = f"{keyfile}:{keydev}" if keydev else keyfile
                        params.append(f"rd.luks.key={uuid}={key}")
                    # Hardware-backed auto-unlock (sd-encrypt reads these options).
                    opts = []
                    if part.get("unlock_tpm2"):
                        opts.append("tpm2-device=auto")
                    if part.get("unlock_fido2"):
                        opts.append("fido2-device=auto")
                    # Extra verbatim rd.luks.options (e.g. "token-timeout=10s").
                    opts.extend(part.get("luks_options", []) or [])
                    # TRIM has to be passed THROUGH the mapping: without
                    # `discard` the fstrim.timer that `enable_trim` schedules
                    # runs against a LUKS volume that swallows every discard.
                    # Opt-in, because it reveals which blocks are in use.
                    if self._cfg.get("enable_trim") and "discard" not in opts:
                        opts.append("discard")
                    if opts:
                        params.append(f"rd.luks.options={uuid}={','.join(opts)}")

                # rootflags describe the mount of /, so only the partition that
                # provides it contributes them — an encrypted btrfs /home must
                # not overwrite the root's subvol= and compression options.
                fs = part.get("filesystem", "")
                if fs == "btrfs" and mounts_root(part):
                    subvols = part.get("btrfs_subvolumes", [])
                    root_sv = next((s for s in subvols if s.get("mountpoint") == "/"), None)
                    sv_name = root_sv["name"] if root_sv else "@"
                    # Partition-level mount_options are the base (a shared option
                    # like compress-force is hoisted there and the subvol's own
                    # list may be empty); the subvol's own options add on top.
                    base = list(part.get("mount_options", []) or [])
                    sv_opts = root_sv.get("mount_options", []) if root_sv else []
                    options = base + [o for o in sv_opts if o not in base]
                    if not options:
                        options = ["compress-force=zstd"]
                    opts_str = ",".join(options + [f"subvol={sv_name}"])
                    params.append(f"rootflags={opts_str}")
        return params

    # Kernel parameters that may appear MORE THAN ONCE, one per device. For
    # these the whole token identifies the entry — keying on the name alone made
    # a single explicit `rd.luks.name` drop every derived one, so a config that
    # declared a second encrypted device silently lost the unlock for the first
    # and stopped booting.
    _REPEATABLE = ("rd.luks.name", "rd.luks.key", "rd.luks.options")

    @classmethod
    def _merge(cls, auto: List[str], explicit: List[str]) -> List[str]:
        """Merge auto-derived and explicit params; explicit wins on conflict.

        "Conflict" means the same single-valued key (``root=``, ``resume=``).
        A repeatable parameter never conflicts: the explicit token is added
        alongside the derived ones, deduplicated by full value.
        """
        def key_of(param: str) -> str:
            name = param.split("=")[0] if "=" in param else param
            # Repeatable: the token itself is the identity, so a different
            # device can never collide with another device's entry.
            return param if name in cls._REPEATABLE else name

        explicit_keys = {key_of(p) for p in explicit}

        merged = list(explicit)
        for p in auto:
            if key_of(p) not in explicit_keys:
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
        """Capture the boot entry's own parameters.

        Everything dasik DERIVES from ``disks`` is subtracted: those tokens carry
        resolved LUKS UUIDs and a machine-specific root device, and re-emitting
        them would pin the config to one machine (they are re-derived on apply).
        What is left is what somebody set by hand — ``resume=``, ``amd_pstate=``,
        an unlock for a device this config does not describe — and dropping it,
        as this used to, quietly removed hibernation from a captured config.

        Falls back to the declared params when the entry cannot be read (no
        target, no bootloader entry yet): sync must never blank a declaration
        just because it could not look.
        """
        live = self._current_cmdline().split() if self._target() is not None else []
        if not live:
            return {self._DOMAIN: list(self.explicit_params)}

        derived_keys = set()
        for token in self._tokens(self._derive_from_disks()):
            name = token.split("=")[0] if "=" in token else token
            derived_keys.add(token if name in self._REPEATABLE else name)

        kept: List[str] = []
        for token in live:
            name = token.split("=")[0] if "=" in token else token
            key = token if name in self._REPEATABLE else name
            if key in derived_keys or token in kept:
                continue
            kept.append(token)
        return {self._DOMAIN: kept}

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
