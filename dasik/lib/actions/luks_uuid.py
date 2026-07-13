"""Deterministic LUKS UUID.

The reconciler builds the whole plan BEFORE applying anything, so on a fresh
encrypted install the LUKS header (and its UUID) does not exist yet when
KernelCmdlineAction computes ``rd.luks.name=<uuid>=<name>``. Reading the UUID at
plan time therefore failed on the first apply, leaving a non-bootable entry until
a redundant second apply.

The fix: pin the UUID up front. DiskPartitionAction formats with
``cryptsetup luksFormat --uuid=<uuid>`` and KernelCmdlineAction derives the SAME
value — a stable UUID5 of the mapper name (or an explicit ``luks_uuid`` from the
config) — so a single apply produces a complete, idempotent entry with no disk
read at all.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional

# Fixed namespace so the derivation is stable across machines and runs.
_DASIK_LUKS_NS = _uuid.uuid5(_uuid.NAMESPACE_URL, "dasik.luks")


def luks_uuid(luks_name: str, explicit: Optional[str] = None) -> str:
    """Return the LUKS UUID for *luks_name*.

    An explicit config value wins; otherwise a deterministic UUID5 of the mapper
    name — identical every run, so the disk header and the kernel cmdline always
    agree without probing the device.
    """
    if explicit:
        return explicit
    return str(_uuid.uuid5(_DASIK_LUKS_NS, luks_name or "cryptroot"))
