#!/usr/bin/env python3
"""Apply ONLY dasik's disk-partitioning against a disposable loop/nbd device.

`dasik apply` also runs base install (pacstrap) and every other action, which
needs the network and minutes. The loopback layer wants to exercise just the
most destructive code path — DiskPartitionAction — in isolation and fast. This
driver does exactly that, guarded so it can only ever write to a file-backed
device.

Usage:  apply_disks_only.py <config.json>

Safety: every device in the config's `disks` section must be /dev/loop* or
/dev/nbd*. Anything else aborts before a single command runs — defense in depth
behind the shell guard in lib.sh. Refusal is exit code 3; real work is exit 0.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Only file-backed, disposable devices are ever acceptable here.
_DISPOSABLE = re.compile(r"^/dev/(loop|nbd)\d+$")
_REFUSE_EXIT = 3


def _devices(config: dict) -> list[str]:
    disks_section = config.get("disks")
    if isinstance(disks_section, dict):
        entries = disks_section.get("disks", [])
    elif isinstance(disks_section, list):
        entries = disks_section
    else:
        entries = []
    return [d.get("device", "") for d in entries if isinstance(d, dict)]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    config_path = Path(argv[1])
    try:
        config = json.loads(config_path.read_text())
    except Exception as e:  # noqa: BLE001 — surface any load error to the operator
        print(f"error: could not load config {config_path}: {e}", file=sys.stderr)
        return 2

    devices = _devices(config)
    if not devices:
        print("error: config has no disks[].device entries to apply.", file=sys.stderr)
        return 2

    for dev in devices:
        if not _DISPOSABLE.match(dev):
            print(
                f"REFUSING: device {dev!r} is not a disposable loop/nbd device.\n"
                f"This driver only writes to /dev/loop* or /dev/nbd*. Aborting "
                f"before any command runs to protect real hardware.",
                file=sys.stderr,
            )
            return _REFUSE_EXIT

    # Import after the guard so a mis-pointed config can never reach real tools.
    from dasik.lib.actions.disk_partition_action import DiskPartitionAction

    action = DiskPartitionAction(config.get("disks"), context=None)
    changes = action.plan(managed=[])
    if not changes:
        print("Nothing to do: the declared layout already matches the device(s).")
        return 0

    print(f"Applying disk layout to: {', '.join(devices)}")
    action.apply(changes)

    print("Partition map:")
    for label, node in action.get_all_partitions().items():
        print(f"  {label}: {node}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
