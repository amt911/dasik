"""Pure state parsing and diffing for day-2 btrfs subvolumes.

No file IO and no ``Command``: the later btrfs-subvolume action reads
``/mnt/etc/fstab``, ``findmnt`` and ``btrfs subvolume list`` output and hands
the raw text to the parsers here, then feeds the result to ``diff`` to decide
what to create, move or remove — the same split ``pacman_repos_state.py`` has
with ``PacmanRepositoriesAction``.

A subvolume is identified by a key ``"<label>:<name>"`` (``root:@var_lib_x``):
the partition label it lives on plus its name under the top level (id 5).
``diff`` returns ``(op, key, reason)`` triples and never touches the machine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Set, Tuple

from ..state.change import Op


class MountRow(NamedTuple):
    """One ``findmnt`` row: where, which device, which fs UUID, which subvolume."""

    target: str
    device: str
    uuid: str
    subvol: str


@dataclass(frozen=True)
class Live:
    """What one btrfs filesystem looks like right now."""

    mounted: Dict[str, str]   # subvolume name -> canonical mountpoint
    fstab: Dict[str, str]     # canonical mountpoint -> subvolume name
    existing: Set[str]        # top-level (id 5) subvolume names


@dataclass(frozen=True)
class Wanted:
    """One declared subvolume."""

    key: str
    label: str
    name: str
    mountpoint: str


# `subvol=/@name` inside a comma-separated option list (the value may itself
# contain ':' as in compress=zstd:3, so split on ',' not on ':').
def _subvol_option(options: str) -> str:
    for opt in options.split(","):
        if opt.startswith("subvol="):
            return opt[len("subvol="):].lstrip("/")
    return ""


def parse_fstab_subvols(text: str) -> Dict[str, str]:
    """Mountpoint -> subvolume name for btrfs fstab lines carrying ``subvol=``."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4 or fields[2] != "btrfs":
            continue
        subvol = _subvol_option(fields[3])
        if subvol:
            out[fields[1]] = subvol
    return out


def _unescape(target: str) -> str:
    # findmnt -r escapes a space as \x20 (and other bytes the same way).
    return re.sub(r"\\x([0-9a-fA-F]{2})",
                  lambda m: chr(int(m.group(1), 16)), target)


def parse_mount_targets(text: str) -> List[str]:
    """Targets of ``findmnt -rn -o TARGET`` output (any filesystem type)."""
    return [_unescape(ln.split()[0]) for ln in text.splitlines() if ln.split()]


def parse_findmnt(text: str) -> List[MountRow]:
    """Parse ``findmnt -rn -t btrfs -o TARGET,SOURCE,UUID,OPTIONS`` output."""
    rows: List[MountRow] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        target, source, uuid, options = fields[0], fields[1], fields[2], fields[3]
        subvol = _subvol_option(options)
        if not subvol:
            continue
        target = _unescape(target)
        rows.append(MountRow(target, source.split("[", 1)[0], uuid, subvol))
    return rows


_LIST_LINE = re.compile(r"^ID \d+ gen \d+ top level (\d+) path (.+)$")


def parse_subvolume_list(text: str) -> Set[str]:
    """Paths of the top-level (``top level 5``) entries of ``btrfs subvolume list``."""
    out: Set[str] = set()
    for line in text.splitlines():
        m = _LIST_LINE.match(line.strip())
        if m and m.group(1) == "5":
            out.add(m.group(2))
    return out


def _install_reason(w: Wanted, live: Live) -> str:
    if w.name not in live.existing:
        return "create + move contents"
    return "adopt existing subvolume"


def diff(wanted: List[Wanted], live_by_label: Dict[str, Live],
         declared_labels: Set[str], managed: List[str]) -> List[Tuple[Op, str, str]]:
    """Decide the changes that bring ``live_by_label`` to ``wanted``."""
    changes: List[Tuple[Op, str, str]] = []
    for w in wanted:
        if w.mountpoint == "/":
            # The root is never ours to plan: booted into a snapshot, or with a
            # root fstab line that only says `subvolid=`, it would never match
            # `@` and apply (which refuses the root) could never converge it.
            continue
        live = live_by_label.get(w.label)
        if live is None:
            continue  # disk not converged / partition not mounted: not our call yet
        at = live.mounted.get(w.name)
        if at == w.mountpoint:
            if live.fstab.get(w.mountpoint) != w.name:
                changes.append((Op.INSTALL, w.key, "add fstab line"))
        elif at is not None:
            changes.append((Op.MODIFY, w.key, f"move {at} -> {w.mountpoint}"))
        else:
            changes.append((Op.INSTALL, w.key, _install_reason(w, live)))

    wanted_keys = {w.key for w in wanted}
    for k in managed:
        if k in wanted_keys:
            continue
        label, _, name = k.partition(":")
        live = live_by_label.get(label)
        # A label that left the config means the partition is gone from the
        # declaration, not that its subvolumes should be deleted.
        if label not in declared_labels or live is None:
            continue
        if name in live.existing and live.mounted.get(name) != "/":
            changes.append((Op.REMOVE, k, "no longer declared"))
    return changes
