"""Pre-wipe guard: refuse to partition when the layout can't fit the disk.

A layout whose fixed-size partitions exceed the device used to fail late and
confusingly (parted clamps/errors, swallowed) — after other destructive steps
had already run. `_validate_sizes` aborts loudly BEFORE any wipe. `rest` / `%`
partitions are skipped (they fill whatever is left).
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.models.disk_model import DiskLayout, Partition


def _disk(*sizes):
    parts = [Partition(label=f"p{i}", size=s, filesystem="ext4", format=True)
             for i, s in enumerate(sizes)]
    return DiskLayout(device="/dev/vda", partition_table="gpt",
                      wipe_disk=True, partitions=parts)


def _action():
    return DiskPartitionAction(config=None)


@pytest.mark.parametrize("text,mib", [
    ("512MiB", 512.0), ("1GiB", 1024.0), ("4GiB", 4096.0), ("2048MiB", 2048.0),
    ("1000", 1000.0),                       # bare number → MiB
])
def test_size_to_mib_parses_binary(text, mib):
    assert _action()._size_to_mib(text) == mib


@pytest.mark.parametrize("text,mib", [
    ("1GB", 1e9 / 2 ** 20),                 # decimal GB
    ("1MB", 1e6 / 2 ** 20),                 # decimal MB
])
def test_size_to_mib_parses_decimal(text, mib):
    assert _action()._size_to_mib(text) == pytest.approx(mib)


def test_raises_when_layout_exceeds_disk():
    a = _action()
    with patch.object(DiskPartitionAction, "_get_disk_size_mib", return_value=8192.0):
        with pytest.raises(CommandExecutionError):
            a._validate_sizes(_disk("512MiB", "8GiB"))   # ~8704 MiB > 8192


def test_ok_when_layout_fits():
    a = _action()
    with patch.object(DiskPartitionAction, "_get_disk_size_mib", return_value=8192.0):
        a._validate_sizes(_disk("512MiB", "4GiB"))       # ~4608 < 8192 → no raise


def test_rest_and_percent_partitions_are_skipped():
    a = _action()
    # 512MiB fixed + a 'rest' partition: the 'rest' must not count against the
    # disk, so even a small disk (1 GiB) fits.
    with patch.object(DiskPartitionAction, "_get_disk_size_mib", return_value=1024.0):
        a._validate_sizes(_disk("512MiB", "rest"))       # no raise
