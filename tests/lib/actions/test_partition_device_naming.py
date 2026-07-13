"""Partition-device naming for DiskPartitionAction._get_partition_device.

The partition node of a block device is either ``<dev><N>`` (sda -> sda1) or
``<dev>p<N>`` (nvme0n1 -> nvme0n1p1). The correct rule is: insert ``p`` when the
device name ends in a digit, otherwise the partition number runs into it. The
original code special-cased only ``nvme``/``mmcblk`` and so produced the wrong
node for **loop** and **nbd** devices (``/dev/loop0`` -> ``/dev/loop01`` instead
of ``/dev/loop0p1``) — which silently breaks the loopback testing flow
documented in docs/testing-without-a-vm.md (mkfs then runs against a node that
does not exist). These tests pin the correct behaviour for every device family.
"""
import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction


@pytest.fixture
def action():
    return DiskPartitionAction(config=None)


@pytest.mark.parametrize(
    "device,expected",
    [
        # letter-terminated names: partition number appends directly
        ("/dev/sda", "/dev/sda1"),
        ("/dev/sdb", "/dev/sdb1"),
        ("/dev/vda", "/dev/vda1"),
        ("/dev/hda", "/dev/hda1"),
        # digit-terminated names: need the 'p' separator
        ("/dev/nvme0n1", "/dev/nvme0n1p1"),
        ("/dev/mmcblk0", "/dev/mmcblk0p1"),
        ("/dev/loop0", "/dev/loop0p1"),
        ("/dev/loop12", "/dev/loop12p1"),
        ("/dev/nbd0", "/dev/nbd0p1"),
    ],
)
def test_partition_node_naming(action, device, expected):
    assert action._get_partition_device(device, 1) == expected


def test_partition_number_is_respected(action):
    assert action._get_partition_device("/dev/vda", 3) == "/dev/vda3"
    assert action._get_partition_device("/dev/loop0", 2) == "/dev/loop0p2"
    assert action._get_partition_device("/dev/nvme0n1", 2) == "/dev/nvme0n1p2"
