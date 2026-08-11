"""Action: the LUKS unlock keyfile (v3 domain "luks_keyfile").

The key material behind ``rd.luks.key``: a random file on a pendrive (or inside
the target root) enrolled as an ADDITIONAL LUKS key, so the machine unlocks
itself when the device is present and still accepts the passphrase when it is
not. This is what the old imperative installer's ``enable_crypt_keyfile`` did,
made declarative and idempotent.

Why an action of its own, instead of the disk action that used to do it:
``DiskPartitionAction`` enrolls inside ``_setup_encryption``, which only runs
when a disk is being FORMATTED. An already-installed machine could therefore
never gain a pendrive, and re-running told you nothing about whether the key was
enrolled. Here the check is the real thing —
``cryptsetup open --test-passphrase --key-file <file> <device>`` — so the state
of the volume itself decides, and a converged system plans nothing.

The keyslot is never REMOVED: ``luksKillSlot`` on the wrong slot destroys access
to the volume. Un-declaring the keyfile drops the kernel parameter and reports
the keyslot it is leaving behind.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

# Where a key device is mounted while the keyfile is created/read. Under /run so
# nothing survives a reboot and the target root is never polluted.
_MOUNTPOINT = "/run/dasik-keydev"
# The wiki's recipe: 4 × 512 random bytes, readable only by root.
_KEYFILE_BS = "512"
_KEYFILE_COUNT = "4"


class LuksKeyfileAction(AbstractAction):
    """Create and enroll the keyfile every encrypted partition declares."""

    _DOMAIN = "luks_keyfile"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "LUKS Unlock Keyfile"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    # --- the declarations ----------------------------------------------- #

    def _declared(self) -> List[Tuple[Dict[str, Any], str, str]]:
        """``(partition, luks_name, keyfile path)`` for every declared unlock."""
        out: List[Tuple[Dict[str, Any], str, str]] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return out
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                keyfile = part.get("unlock_keyfile")
                if part.get("encrypt") and keyfile:
                    out.append((part, part.get("luks_name", "cryptroot"), keyfile))
        return out

    @staticmethod
    def _item(luks_name: str, keyfile: str) -> str:
        return f"{luks_name}:{keyfile}"

    # --- probes ----------------------------------------------------------- #

    @staticmethod
    def _keydev_path(spec: str) -> str:
        """Block device for an ``unlock_keydev`` spec.

        Accepts what the kernel accepts: a bare UUID (the documented form), an
        explicit ``UUID=``/``PARTUUID=``/``LABEL=``, or a device path.
        """
        spec = str(spec).strip()
        if spec.startswith("/dev/"):
            return spec
        kind, sep, value = spec.partition("=")
        if not sep:
            return f"/dev/disk/by-uuid/{spec}"
        by = {"UUID": "by-uuid", "PARTUUID": "by-partuuid",
              "PARTLABEL": "by-partlabel", "LABEL": "by-label"}.get(kind.upper())
        return f"/dev/disk/{by}/{value}" if by else value

    def _key_device_present(self, part: Dict[str, Any]) -> bool:
        """Whether the key device is attached (always True with no key device:
        the keyfile then lives inside the target root)."""
        keydev = part.get("unlock_keydev")
        if not keydev:
            return True
        return os.path.exists(self._keydev_path(keydev))

    @staticmethod
    def _key_works(device: str, local_path: str) -> bool:
        """Whether *local_path* already unlocks *device*.

        ``--test-passphrase`` creates no mapping, so this probe cannot disturb a
        running system — which is what makes it safe to run from ``plan()``. Any
        failure to even ask (no cryptsetup, no device) reads as "not enrolled":
        the enrollment is then planned and ``apply`` says out loud why it cannot
        proceed, instead of a silent skip that leaves a machine whose declared
        unlock does not exist.
        """
        try:
            result = Command.execute(
                "cryptsetup",
                ["open", "--test-passphrase", "--key-file", local_path, device])
        except Exception:            # noqa: BLE001 - any probe failure is non-fatal
            return False
        return getattr(result, "returncode", 1) == 0

    def _luks_device(self, luks_name: str) -> Optional[str]:
        """The block device behind an open mapping, from ``cryptsetup status``."""
        try:
            result = Command.execute("cryptsetup", ["status", luks_name])
        except Exception:            # noqa: BLE001 - not open / no cryptsetup
            return None
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if "device:" in line:
                return line.split("device:")[1].strip()
        return None

    # --- mounting the key device ------------------------------------------ #

    def _mount_keydev(self, part: Dict[str, Any], read_only: bool = False) -> Optional[str]:
        """Mount the key device and return its mountpoint, or None when the
        keyfile lives in the target root (nothing to mount)."""
        keydev = part.get("unlock_keydev")
        if not keydev:
            return None
        os.makedirs(_MOUNTPOINT, exist_ok=True)
        args = ["-o", "ro"] if read_only else []
        Command.execute("mount", [*args, self._keydev_path(keydev), _MOUNTPOINT])
        return _MOUNTPOINT

    @staticmethod
    def _umount_keydev(mountpoint: Optional[str]) -> None:
        if not mountpoint:
            return
        try:
            Command.execute("umount", [mountpoint])
        except Exception:            # noqa: BLE001 - best effort, never fatal
            pass

    def _local_path(self, keyfile: str, mountpoint: Optional[str]) -> str:
        """Where the keyfile is readable from HERE.

        With a key device the declared path is relative to that device's root;
        without one it is an absolute path inside the target.
        """
        if mountpoint:
            return os.path.join(mountpoint, keyfile.lstrip("/"))
        target = self._target()
        return target.path(keyfile) if target is not None else "/mnt" + keyfile

    # --- v3 contract ------------------------------------------------------ #

    def plan(self, managed) -> List[Change]:
        changes: List[Change] = []
        desired: List[str] = []
        for part, luks_name, keyfile in self._declared():
            item = self._item(luks_name, keyfile)
            desired.append(item)
            if not self._key_device_present(part):
                changes.append(Change(
                    self._DOMAIN, Op.INSTALL, item,
                    reason="key device not attached — plug it in before apply"))
                continue
            mountpoint = None
            try:
                mountpoint = self._mount_keydev(part, read_only=True)
                local = self._local_path(keyfile, mountpoint)
                if not os.path.exists(local):
                    changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                          reason="keyfile does not exist yet"))
                    continue
                device = self._luks_device(luks_name)
                if device and self._key_works(device, local):
                    continue
                changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                      reason="keyfile is not enrolled on the volume"))
            except Exception:        # noqa: BLE001 - an unreadable key device
                changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                      reason="key device could not be read"))
            finally:
                self._umount_keydev(mountpoint)

        # Un-declared but owned: the parameter goes, the keyslot stays.
        for item in managed or []:
            if item not in desired:
                changes.append(Change(
                    self._DOMAIN, Op.REMOVE, item,
                    reason="kernel parameter dropped; the LUKS keyslot is LEFT IN "
                           "PLACE — remove it yourself with `cryptsetup "
                           "luksRemoveKey` once you have another way in"))
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [self._item(name, keyfile)
                               for _part, name, keyfile in self._declared()]}

    def apply(self, changes) -> None:
        wanted = {c.item for c in changes if c.op is Op.INSTALL}
        for part, luks_name, keyfile in self._declared():
            if self._item(luks_name, keyfile) not in wanted:
                continue
            device = self._luks_device(luks_name)
            if not device:
                raise CommandExecutionError(
                    f"Cannot enroll {keyfile!r}: no open LUKS mapping named "
                    f"{luks_name!r}, so the volume to add the key to is unknown.")
            mountpoint = None
            try:
                mountpoint = self._mount_keydev(part)
                local = self._local_path(keyfile, mountpoint)
                self._create_keyfile(local)
                self._enroll(device, local, part)
            finally:
                self._umount_keydev(mountpoint)

    def _create_keyfile(self, local: str) -> None:
        """Create the keyfile if it is not there yet, and only then.

        An existing file is left ALONE: the pendrive may already carry the key
        another machine unlocks with, and overwriting it would revoke that.
        """
        if os.path.exists(local):
            return
        os.makedirs(os.path.dirname(local), exist_ok=True)
        Command.execute("dd", [f"bs={_KEYFILE_BS}", f"count={_KEYFILE_COUNT}",
                               "if=/dev/random", f"of={local}", "iflag=fullblock"],
                        check=True)
        os.chmod(local, 0o600)

    @staticmethod
    def _enroll(device: str, local: str, part: Dict[str, Any]) -> None:
        """Add the keyfile as an extra key, authorised by the existing one."""
        existing_keyfile = part.get("luks_keyfile")
        password = part.get("luks_password")
        if existing_keyfile:
            Command.execute("cryptsetup",
                            ["luksAddKey", "--key-file", existing_keyfile, device, local],
                            check=True)
        elif password is not None:
            # Over stdin: the passphrase never reaches argv or the process list.
            Command.execute("cryptsetup",
                            ["luksAddKey", "--key-file", "-", device, local],
                            input=str(password).encode(), check=True)
        else:
            raise CommandExecutionError(
                f"Cannot enroll {local!r} on {device}: the partition declares "
                "neither luks_password nor luks_keyfile, and cryptsetup would "
                "block on an interactive prompt.")

    def import_state(self, managed=None) -> dict:
        # Nothing: the declaration belongs to the partition, and
        # DiskPartitionAction.import_state captures it from the live cmdline.
        return {}

    # --- legacy executor bridge ------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

    def verify(self) -> bool:
        return not self.plan(managed=[])
