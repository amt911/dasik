"""Initramfs generator backend interface + shared disk-config detection."""
from __future__ import annotations
from typing import Any, Dict, Optional

from ..partition_utils import mounts_root
from ..swap_encryption import is_random_swap


def detect_encryption(cfg: Dict[str, Any]) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("encrypt", False):
                    return True
    return False


def detect_root_fs(cfg: Dict[str, Any]) -> Optional[str]:
    """Filesystem of the partition that provides ``/``.

    Uses the shared ``mounts_root`` predicate so a synced btrfs root — with
    ``mountpoint: null`` and ``/`` living on the ``@`` subvolume — is recognized;
    otherwise the backend never forces btrfs into the initramfs and the encrypted
    root fails to mount."""
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if mounts_root(part):
                    return part.get("filesystem")
    return None


from ...models.disk_model import fido2_count


def _any_partition_flag(cfg: Dict[str, Any], flag: str) -> bool:
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get(flag, False):
                    return True
    return False


def detect_fido2(cfg: Dict[str, Any]) -> bool:
    """Does any partition ask for a FIDO2 token?

    Through `fido2_count`, so "how many keys" is decided in ONE place: the flag
    is a bool OR a count, and `unlock_fido2: 0` has to mean the same as `false`
    to every consumer, the initramfs included.
    """
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if fido2_count(part):
                    return True
    return False


def detect_tpm2(cfg: Dict[str, Any]) -> bool:
    return _any_partition_flag(cfg, "unlock_tpm2")


def detect_hibernation(cfg: Dict[str, Any]) -> bool:
    """True when the config asks for hibernation.

    Either a swap partition is declared, or the kernel cmdline names a resume
    device (a synced config can carry ``resume=`` with the swap described
    elsewhere). The initramfs needs the resume module in both cases: without it
    the kernel never restores the image and simply boots fresh — the hibernation
    write succeeds and the session is silently lost.
    """
    disks = cfg.get("disks", {})
    if isinstance(disks, dict):
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("filesystem") == "swap":
                    # A random-key swap cannot hold a hibernation image: the key
                    # is drawn fresh at every boot and discarded at shutdown, so
                    # resume would have nothing to decrypt it with. Asking for
                    # the resume module here only costs boot time hunting for an
                    # image that cannot exist. (Declaring `resume=` alongside one
                    # is refused by preflight — until then the cmdline below
                    # still wins, because the module is what makes that
                    # declaration mean anything.)
                    if is_random_swap(part):
                        continue
                    return True
    for token in cfg.get("kernel_cmdline", []) or []:
        for word in str(token).split():
            if word.startswith("resume="):
                return True
    return False


def _partitions(cfg: Dict[str, Any]):
    """Every partition stanza in the config, across all disks."""
    disks = cfg.get("disks", {})
    if not isinstance(disks, dict):
        return
    for disk in disks.get("disks", []):
        for part in disk.get("partitions", []):
            yield part


def detect_keydev_filesystems(cfg: Dict[str, Any]) -> "list[str]":
    """Filesystems of the key devices any encrypted partition unlocks from.

    The initramfs must carry those modules or it cannot read the key device at
    boot — the wiki states it as a requirement whenever the key device's
    filesystem differs from the root's. A key device whose filesystem is not
    declared yields nothing here (preflight warns about it); guessing would put
    an arbitrary module in the image.
    """
    found: "list[str]" = []
    for part in _partitions(cfg):
        fs = part.get("unlock_keydev_fs")
        if part.get("unlock_keyfile") and part.get("unlock_keydev") and fs:
            if fs not in found:
                found.append(fs)
    return sorted(found)


def detect_embedded_keyfiles(cfg: Dict[str, Any]) -> "list[str]":
    """Keyfiles that must be baked INTO the image: `unlock_keyfile` with no
    `unlock_keydev`, i.e. a path inside the target root. Without embedding them
    the kernel cmdline points at a file the initramfs cannot see."""
    found: "list[str]" = []
    for part in _partitions(cfg):
        keyfile = part.get("unlock_keyfile")
        if keyfile and not part.get("unlock_keydev") and keyfile not in found:
            found.append(keyfile)
    return found


def detect_plymouth(cfg: Dict[str, Any]) -> bool:
    """True when the config declares a boot splash.

    An EMPTY block counts: it declares the splash with plymouth's own theme.
    """
    return cfg.get("plymouth") is not None


def detect_plymouth_theme(cfg: Dict[str, Any]) -> Optional[str]:
    """The declared plymouth theme, if any. The image has to be rebuilt whenever
    it changes, so the backends fold it into the value they compare."""
    block = cfg.get("plymouth")
    if isinstance(block, dict):
        theme = block.get("theme")
        return str(theme) if theme else None
    return None


def detect_bluetooth_in_initramfs(cfg: Dict[str, Any]) -> bool:
    bt = cfg.get("bluetooth")
    return bool(isinstance(bt, dict) and bt.get("in_initramfs"))


class InitramfsBackend:
    """Compute + apply the initramfs configuration for one generator."""

    def __init__(self, config: Dict[str, Any], target=None):
        self.config = config if isinstance(config, dict) else {}
        self.target = target
        self.has_encryption = detect_encryption(self.config)
        self.root_fs = detect_root_fs(self.config)
        self.has_fido2 = detect_fido2(self.config)
        self.has_tpm2 = detect_tpm2(self.config)
        self.bluetooth_in_initramfs = detect_bluetooth_in_initramfs(self.config)
        self.has_hibernation = detect_hibernation(self.config)
        self.has_plymouth = detect_plymouth(self.config)
        self.plymouth_theme = detect_plymouth_theme(self.config)
        self.keydev_filesystems = detect_keydev_filesystems(self.config)
        self.embedded_keyfiles = detect_embedded_keyfiles(self.config)

    def _path(self, canonical: str) -> str:
        if self.target is not None:
            return self.target.path(canonical)
        return "/mnt" + canonical

    def desired_value(self) -> str:
        raise NotImplementedError

    def actual_value(self) -> Optional[str]:
        raise NotImplementedError

    def apply(self) -> None:
        raise NotImplementedError
