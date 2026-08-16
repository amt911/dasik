"""The layouts the wizard offers, as pure functions of a few options.

Every one of these is a layout this repo installs and boots in QEMU. That is the
point: an assistant earns its keep by producing a block that is *correct*, and
these are the ones known to work. The custom path is there for everything else,
and goes through the same model validation before it reaches a screen.

Nothing here touches a disk, reads the system, or writes a file — a recipe is
`(options) -> dict`, which is what makes the whole wizard testable without
hardware.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models.disk_model import DiskLayout

# The passphrase NEVER lands in the config in clear. `$include_line` reads the
# first line of a file the config points at, which is the directive meant for
# secrets (see json_parser/includes.py).
SECRET_DEFAULT = "secrets/luks-passphrase"   # nosec B105 - a PATH, not a password

# The subvolume layout the repo installs everywhere: @ for /, and the ones that
# must stay OUT of a root snapshot — logs, the package cache, the snapshots
# themselves.
_SUBVOLUMES = (
    ("@", "/"),
    ("@home", "/home"),
    ("@log", "/var/log"),
    ("@pkg", "/var/cache/pacman/pkg"),
    ("@.snapshots", "/.snapshots"),
)
_COMPRESSION = "compress-force=zstd:3"


@dataclass(frozen=True)
class Options:
    """Everything a recipe can be tuned with. All of it has a working default."""

    device: str
    esp_size: str = "512MiB"
    swap_size: str = "8GiB"
    luks_name: str = "cryptroot"
    swap_luks_name: str = "cryptswap"
    secret: str = SECRET_DEFAULT
    # DESTRUCTIVE, and off unless the wizard asked and was told yes. An empty
    # disk does not need it: `plan` installs onto a disk with no partition table
    # without any wipe at all.
    wipe: bool = False


@dataclass(frozen=True)
class Contribution:
    """What a recipe produces: a disk stanza, and anything else it implies."""

    disk: Dict[str, Any]
    # Some layouts need more than the `disks` block to actually work. The
    # hibernate one is the case: `resume=` is not derived from anything, so
    # without it the machine has a swap it can never resume from.
    kernel_cmdline: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Recipe:
    """A layout, named so the row stands on its own.

    The titles used to read "…and a swap with a random key", which does not say
    that it also gives you LUKS and btrfs — you cannot pick a layout from a menu
    of continuations. Every title now names the WHOLE layout.
    """

    key: str
    title: str
    detail: str
    _build: Callable[[Options], Contribution]

    def summary(self, options: "Optional[Options]" = None) -> List[str]:
        """The partitions this layout would create, one line each.

        Shown under the cursor in the menu, so choosing does not require
        remembering what each name implies.
        """
        built = self.build(options or Options(device="/dev/…"))
        lines = []
        for part in built.disk["partitions"]:
            bits = [f"{part['label']:<6}", f"{part['size']:>8}",
                    f"{part['filesystem']:<6}"]
            if part.get("mountpoint"):
                bits.append(f"-> {part['mountpoint']}")
            if part.get("encrypt"):
                bits.append(f"[LUKS {part.get('luks_name')}]")
            if part.get("swap_encryption") == "random":
                bits.append("[random key, cannot hibernate]")
            for subvol in part.get("btrfs_subvolumes") or []:
                bits.append("")
            lines.append(" ".join(b for b in bits if b))
            subvols = part.get("btrfs_subvolumes") or []
            if subvols:
                lines.append("         subvolumes: " +
                             " ".join(f"{s['name']}->{s['mountpoint']}"
                                      for s in subvols))
        for token in built.kernel_cmdline:
            lines.append(f"         also adds: {token}")
        return lines

    def build(self, options: Options) -> Contribution:
        contribution = self._build(options)
        # Never hand a screen something the model would reject.
        validate_layout(contribution.disk)
        return contribution


def _resolved(value: Any) -> Any:
    """A copy with every `$include_*` directive standing in for its value.

    The wizard writes the config a HUMAN keeps, so the passphrase leaves as
    `{"$include_line": "secrets/…"}` — but the model only ever sees configs the
    loader has already resolved, and it declares `luks_password: str`. So the
    shape is validated against a resolved copy while the directive is what gets
    written. Validating the raw block instead would fail on the one rule the
    issue insists on: never write the passphrase in clear.
    """
    if isinstance(value, dict):
        if len(value) == 1 and next(iter(value)).startswith("$include"):
            return "(from the file this points at)"
        return {k: _resolved(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolved(v) for v in value]
    return value


def validate_layout(disk: Dict[str, Any]) -> None:
    """Raise if *disk* is not a layout the model accepts."""
    DiskLayout.model_validate(_resolved(copy.deepcopy(disk)))


def _secret_ref(options: Options) -> Dict[str, str]:
    return {"$include_line": options.secret}


def _esp(options: Options) -> Dict[str, Any]:
    return {"label": "ESP", "size": options.esp_size, "filesystem": "fat32",
            "partition_type": "esp", "mountpoint": "/boot", "format": True}


def _disk(options: Options, partitions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"device": options.device, "partition_table": "gpt",
            "wipe_disk": options.wipe, "partitions": partitions}


def _btrfs_root(options: Options) -> Dict[str, Any]:
    return {
        "label": "root", "size": "rest", "filesystem": "btrfs",
        "partition_type": "linux",
        # A btrfs root is mounted THROUGH its subvolumes; a mountpoint here
        # would mount the top-level volume over them.
        "mountpoint": None,
        "format": True,
        "encrypt": True,
        "luks_name": options.luks_name,
        "luks_password": _secret_ref(options),
        "mount_options": [_COMPRESSION],
        "btrfs_subvolumes": [{"name": name, "mountpoint": mount}
                             for name, mount in _SUBVOLUMES],
    }


def _ext4(options: Options) -> Contribution:
    return Contribution(disk=_disk(options, [
        _esp(options),
        {"label": "root", "size": "rest", "filesystem": "ext4",
         "partition_type": "linux", "mountpoint": "/", "format": True},
    ]), notes=("No encryption: anyone with the disk can read it.",))


def _luks_btrfs(options: Options) -> Contribution:
    return Contribution(
        disk=_disk(options, [_esp(options), _btrfs_root(options)]),
        notes=(f"The passphrase is read from {options.secret} — the wizard "
               f"writes that file for you, at mode 0600.",))


def _luks_btrfs_swap(options: Options) -> Contribution:
    swap = {"label": "swap", "size": options.swap_size, "filesystem": "swap",
            "partition_type": "linux-swap", "format": True,
            "swap_encryption": "random"}
    return Contribution(
        disk=_disk(options, [_esp(options), swap, _btrfs_root(options)]),
        notes=("The swap takes a fresh random key on every boot, so it can "
               "never be read back — which also means it can never hold a "
               "hibernation image. Pick the hibernate layout if you want that.",))


def _luks_btrfs_hibernate(options: Options) -> Contribution:
    swap = {"label": "swap", "size": options.swap_size, "filesystem": "swap",
            "partition_type": "linux-swap", "format": True,
            "encrypt": True, "luks_name": options.swap_luks_name,
            "luks_password": _secret_ref(options)}
    return Contribution(
        disk=_disk(options, [_esp(options), swap, _btrfs_root(options)]),
        kernel_cmdline=(f"resume=/dev/mapper/{options.swap_luks_name}",),
        notes=(f"Size the swap at least as large as your RAM, or the image "
               f"will not fit.",
               f"Adds resume=/dev/mapper/{options.swap_luks_name} to "
               f"kernel_cmdline: it is not derived from the swap partition, and "
               f"without it the machine never resumes.",))


RECIPES: Tuple[Recipe, ...] = (
    Recipe("ext4",
           "ESP + ext4 root",
           "No encryption. The simplest thing that boots.", _ext4),
    Recipe("luks-btrfs",
           "ESP + encrypted (LUKS) btrfs root, with subvolumes",
           "No swap. Root inside LUKS, btrfs with @/@home/@log/@pkg/@.snapshots.",
           _luks_btrfs),
    Recipe("luks-btrfs-swap",
           "ESP + encrypted btrfs root + encrypted swap (random key)",
           "Same as above plus swap. The swap key is new on every boot, so it "
           "is safe but CANNOT hibernate.",
           _luks_btrfs_swap),
    Recipe("luks-btrfs-hibernate",
           "ESP + encrypted btrfs root + encrypted swap (LUKS, hibernates)",
           "Same as above, but the swap lives in LUKS with a keyslot, so a "
           "hibernation image can be read back. Adds resume= too.",
           _luks_btrfs_hibernate),
)


def find(key: str) -> Recipe:
    """The recipe named *key*, or a KeyError that lists the ones that exist."""
    for recipe in RECIPES:
        if recipe.key == key:
            return recipe
    raise KeyError(f"unknown layout {key!r}; known: "
                   f"{', '.join(r.key for r in RECIPES)}")


def custom_disk(device: str, partitions: List[Dict[str, Any]],
                partition_table: str = "gpt", wipe: bool = False) -> Dict[str, Any]:
    """A disk stanza from partitions the user composed, validated here.

    Validating at the point of composition is the difference between "two
    partitions cannot both be sized `rest`" arriving now, and arriving three
    screens later as a traceback.
    """
    disk = {"device": device, "partition_table": partition_table,
            "wipe_disk": wipe, "partitions": list(partitions)}
    validate_layout(disk)                # raises ValidationError (a ValueError)
    return disk
