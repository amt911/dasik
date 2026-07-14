"""DiskPartitionAction._has_partition_table must distinguish a REAL partition
table from parted's "unknown"/"loop" placeholders.

`parted -s <dev> print` always prints a "Partition Table:" line — even for a
genuinely empty disk, where it reads "Partition Table: unknown". The original
code did a naive substring check (`"Partition Table:" in stdout`) and so reported
True for an empty disk, which makes `plan()` refuse to partition it ("populated,
skipping") unless wipe_disk:true. That silently breaks first-install on a fresh
disk and the loopback test flow. These tests pin the correct parsing.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.exceptions.exceptions import CommandNotFoundException

_EMPTY = """Model: Loopback device (loopback)
Disk /dev/loop0: 2147MB
Sector size (logical/physical): 512B/512B
Partition Table: unknown
Disk Flags:
"""

_GPT = """Model: Loopback device (loopback)
Disk /dev/loop0: 2147MB
Sector size (logical/physical): 512B/512B
Partition Table: gpt
Disk Flags:
"""

_MSDOS = "Disk /dev/sda: 500GB\nPartition Table: msdos\n"
_LOOP = "Disk /dev/loop0: 2147MB\nPartition Table: loop\n"           # whole-device fs, no table
_SPANISH_GPT = "Disco /dev/sda: 500GB\nTabla de particiones: gpt\n"


@pytest.fixture
def action():
    return DiskPartitionAction(config=None)


def _with_parted(stdout):
    return patch(
        "dasik.lib.actions.disk_partition_action.Command.execute",
        return_value=SimpleNamespace(stdout=stdout),
    )


@pytest.mark.parametrize("output", [_EMPTY, _LOOP])
def test_empty_or_loop_is_not_a_partition_table(action, output):
    with _with_parted(output):
        assert action._has_partition_table("/dev/loop0") is False


@pytest.mark.parametrize("output", [_GPT, _MSDOS, _SPANISH_GPT])
def test_real_table_is_detected(action, output):
    with _with_parted(output):
        assert action._has_partition_table("/dev/sda") is True


def test_bytes_stdout_is_handled(action):
    with _with_parted(_GPT.encode("utf-8")):
        assert action._has_partition_table("/dev/loop0") is True


@pytest.mark.parametrize("exc", [
    RuntimeError("parted blew up"),
    CommandNotFoundException("parted"),
])
def test_probe_failure_is_fail_safe_assumes_a_table(action, exc):
    # A genuinely FAILED probe (parted/arch-chroot missing, exec error) is NOT the
    # same as "parted ran and found no label". We cannot tell if the disk is empty,
    # so on a destructive tool we must fail SAFE: assume a table exists so plan()
    # routes to "populated, skipping" instead of scheduling a wipe. Returning False
    # here would let an unreadable-but-populated disk be repartitioned on a
    # wipe_disk:false config. (A truly empty disk takes the normal path below, where
    # parted runs and reports "unknown" -> False -> partitioned as intended.)
    with patch(
        "dasik.lib.actions.disk_partition_action.Command.execute",
        side_effect=exc,
    ):
        assert action._has_partition_table("/dev/loop0") is True
