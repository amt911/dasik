"""Shared partition predicates (used by the bootloader + kernel-cmdline actions)."""
from typing import Any, Dict

# `unlock_keydev` spec kinds and the /dev/disk/by-* directory each resolves to.
_BY_DIR = {"UUID": "by-uuid", "PARTUUID": "by-partuuid",
           "PARTLABEL": "by-partlabel", "LABEL": "by-label"}


def keydev_path(spec: str) -> str:
    """Block device path for an ``unlock_keydev`` spec.

    Accepts what the kernel accepts on ``rd.luks.key``: a bare filesystem UUID
    (the documented form), an explicit ``UUID=``/``PARTUUID=``/``PARTLABEL=``/
    ``LABEL=``, or a device path. Shared so the action that mounts the key
    device and the sync that probes it always look at the same node.
    """
    spec = str(spec).strip()
    if spec.startswith("/dev/"):
        return spec
    kind, sep, value = spec.partition("=")
    if not sep:
        return f"/dev/disk/by-uuid/{spec}"
    by = _BY_DIR.get(kind.upper())
    return f"/dev/disk/{by}/{value}" if by else value


def keydev_spec(value: str) -> str:
    """Normalize ``unlock_keydev`` into a device spec the kernel and crypttab(5)
    both resolve.

    The field documents a filesystem UUID, and that bare value is what a user
    writes — but ``rd.luks.key`` (and the crypttab key field, which takes the
    same ``<path>:<device spec>`` syntax) needs ``UUID=<uuid>``. An explicit
    ``PARTUUID=``/``LABEL=``/``/dev/…`` is passed through untouched. Shared, so
    the kernel parameter and the crypttab line can never disagree about which
    device the key is on.
    """
    value = str(value).strip()
    return value if "=" in value or value.startswith("/dev/") else f"UUID={value}"


def mounts_root(part: Dict[str, Any]) -> bool:
    """True if this partition provides ``/``: either the partition itself mounts
    ``/``, or (btrfs) one of its subvolumes does. A synced btrfs root often has
    ``mountpoint: null`` with the ``/`` living on the ``@`` subvolume — the entry
    derivation must still treat it as the root, or the LUKS never opens and boot
    hangs on ``/dev/disk/by-label/root``."""
    if part.get("mountpoint") == "/":
        return True
    return any(s.get("mountpoint") == "/"
               for s in part.get("btrfs_subvolumes", []) or [])
