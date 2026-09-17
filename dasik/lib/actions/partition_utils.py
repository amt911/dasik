"""Shared partition predicates (used by the bootloader + kernel-cmdline actions)."""
from typing import Any, Dict, List

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


def token_name(token: str) -> str:
    """The key half of a ``name`` or ``name=value`` token.

    Deliberately not "mount option" in the name (N4, review of
    fix/rootflags-sync-drift): this splits a btrfs mount option
    (``compress-force=zstd`` -> ``compress-force``) exactly the same way it
    splits a kernel command-line parameter (``rootflags=...`` -> ``rootflags``)
    — a kernel parameter is not a mount option, so a mount-option-flavored
    name would mislead the next reader at the second call site.
    ``KernelCmdlineAction._token_key`` is the kernel-flavored alias other code
    in that module reads.

    Shared so the btrfs subvolume-option merge (``merge_mount_options_by_name``,
    ``DiskPartitionAction._correct_subvol_options``), the rootflags=
    equivalence check, ``KernelCmdlineAction._merge``'s explicit-wins key, and
    ``import_state``'s derived-key subtraction never grow a second copy of the
    same one-line split.
    """
    return token.split("=", 1)[0]


# The kernel's own default compression LEVEL for a btrfs `compress`/
# `compress-force` mount option declared without one. Measured via findmnt on
# a real mount (a bare `compress-force=zstd` comes back `...zstd:3`) and, for
# the fuller table of edge levels, a loopback btrfs image in the vmtest guest
# — see docs/FACTS.md FACT-RFD-1. lzo has no level at all, so it is
# deliberately absent here.
_COMPRESS_DEFAULT_LEVEL = {"zstd": "3", "zlib": "3"}
_COMPRESS_NAMES = ("compress", "compress-force")

# The kernel CLAMPS an out-of-range explicit level to the nearest valid one
# (SF-2, re-review round 2 of fix/rootflags-sync-drift) — measured on a real
# loopback btrfs image with the guest's own kernel (FACT-RFD-3), nothing
# guessed: `zstd:0`/`zlib:0` clamp UP to the algorithm's own default level
# (indistinguishable from not naming a level at all), `zstd:16` clamps DOWN
# to 15, `zlib:12` clamps down to 9. Exactly these four are in the table —
# an unmeasured level (`zstd:17+`, `zlib:10`/`11`/`13+`, a negative zstd
# level, `lzo:N`) is deliberately left alone rather than normalized on a
# guess ("nothing more" than what was measured).
_COMPRESS_LEVEL_CLAMPS = {
    ("zstd", "0"): "3", ("zstd", "16"): "15",
    ("zlib", "0"): "3", ("zlib", "12"): "9",
}

# A bare `compress`/`compress-force` (no algorithm at all) resolves to the
# kernel's OWN default algorithm, which is zlib, not zstd (FACT-RFD-3,
# measured) — the level is zlib's own default (3).
_COMPRESS_BARE_DEFAULT = "zlib:3"


def normalize_compress_value(value: str) -> str:
    """``zstd`` <-> ``zstd:3`` (the kernel's default level) for EQUIVALENCE
    only. An explicit level is never touched EXCEPT for the four measured
    clamped spellings (SF-2, `_COMPRESS_LEVEL_CLAMPS`) — ``zstd:1`` stays
    ``zstd:1``, so it is never confused with the default ``zstd:3``. lzo has
    no default to fill in."""
    if ":" in value:
        algo, _, level = value.partition(":")
        clamped = _COMPRESS_LEVEL_CLAMPS.get((algo, level))
        return f"{algo}:{clamped}" if clamped else value
    default = _COMPRESS_DEFAULT_LEVEL.get(value)
    return f"{value}:{default}" if default else value


def normalize_compress_option(opt: str) -> str:
    """A full ``compress``/``compress-force`` OPTION token — bare, or
    ``name=value`` — normalized to a canonical ``name=value`` form for
    EQUIVALENCE only (never written back). Anything whose name is not one of
    ``_COMPRESS_NAMES`` is returned unchanged.

    SF-2 (re-review round 2): a bare name (no ``=`` at all, e.g. declaring
    just ``compress-force``) needs its own branch — it is not the same case
    as an explicit value, and the kernel resolves it to its OWN default
    algorithm+level (``zlib:3``, not the algorithm-specific
    ``normalize_compress_value`` table, which only fills in a LEVEL for an
    algorithm the option already names).

    Feeds both ``_rootflags_option_set`` (``KernelCmdlineAction``, the
    plan-time equivalence) and ``option_equivalent`` (the capture
    subtraction) — one table, not two copies of the same clamp/bare rule.
    """
    name = token_name(opt)
    if name not in _COMPRESS_NAMES:
        return opt
    if "=" in opt:
        _, _, raw = opt.partition("=")
        return f"{name}={normalize_compress_value(raw)}"
    return f"{name}={_COMPRESS_BARE_DEFAULT}"


def option_equivalent(a: str, b: str) -> bool:
    """Whether two individual mount options describe the SAME setting.

    Same NAME (``token_name``), and for ``compress``/``compress-force`` the
    kernel's default level filled in (and the measured clamps applied, SF-2),
    so a bare ``compress-force=zstd`` matches a kernel-reported
    ``compress-force=zstd:3``, and a bare ``compress-force`` (no algorithm at
    all) matches ``compress-force=zlib:3``. Everything else compares as an
    exact token.

    Shared by the rootflags= equivalence check (``KernelCmdlineAction``) and
    ``DiskPartitionAction._correct_subvol_options`` (S4, review of
    fix/rootflags-sync-drift) — a live ``compress-force=zstd:3`` subtracted
    from a declared base ``compress-force=zstd`` by whole-token comparison
    used to survive as "new" and get captured a second time onto the
    subvolume, so the derived ``rootflags=`` carried the same option twice.
    """
    name_a, name_b = token_name(a), token_name(b)
    if name_a != name_b:
        return False
    if name_a in _COMPRESS_NAMES:
        return normalize_compress_option(a) == normalize_compress_option(b)
    return a == b


def merge_mount_options_by_name(base: "List[str]", overrides: "List[str]") -> "List[str]":
    """Merge two option lists by NAME: an option in *overrides* replaces the
    base option of the SAME NAME in place — the more specific statement wins —
    rather than being appended next to it.

    Shared (S4, review of fix/rootflags-sync-drift) by
    ``DiskPartitionAction._subvol_mount_options`` (the mount pass, working on
    pydantic models) and ``KernelCmdlineAction._derive_from_disks`` (the
    rootflags= derivation, working on plain config dicts) — a whole-token dedup
    in the latter let a hoisted partition-level ``compress-force=zstd`` and a
    subvolume-level ``compress-force=zstd:3`` both survive into one
    ``rootflags=`` value, which is not a merge but a contradiction the kernel
    resolves by silently taking the last one.
    """
    merged = list(base)
    for opt in overrides:
        name = token_name(opt)
        for i, existing in enumerate(merged):
            if token_name(existing) == name:
                merged[i] = opt      # the override is more specific, in place
                break
        else:
            merged.append(opt)
    return merged


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
