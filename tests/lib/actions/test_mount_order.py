"""Mount ordering for DiskPartitionAction (root must mount before its children).

Regression found by the QEMU install harness: with the config listing the ESP
(``/boot``) before root (``/``), the old sort key ``mountpoint.count('/')`` gave
both a value of 1 (a tie), so the ESP was mounted at ``/mnt/boot`` first and then
root was mounted at ``/mnt``, SHADOWING it. Result: the kernel, loader entries,
and bootloader all landed on the root filesystem, the ESP stayed empty, and the
installed system was non-bootable (and the bootloader step never converged).

`_mount_depth` counts path components so root ("/") is depth 0 and always sorts
first. These tests pin the depth and the resulting mount order.
"""
import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction


@pytest.mark.parametrize(
    "mountpoint,depth",
    [
        ("/", 0),
        ("/boot", 1),
        ("/home", 1),
        ("/boot/efi", 2),
        ("/var/lib/machines", 3),
    ],
)
def test_mount_depth(mountpoint, depth):
    assert DiskPartitionAction._mount_depth(mountpoint) == depth


def test_root_sorts_before_boot_even_when_declared_after_it():
    """The core of the fix: root ('/') must sort ahead of '/boot' regardless of
    the order they appear in the config (ESP is conventionally declared first)."""
    mounts = ["/boot", "/"]  # config order: ESP before ROOT
    ordered = sorted(mounts, key=DiskPartitionAction._mount_depth)
    assert ordered[0] == "/"
    assert ordered == ["/", "/boot"]


def test_nested_mounts_order_parent_before_child():
    mounts = ["/boot/efi", "/", "/boot", "/home"]
    ordered = sorted(mounts, key=DiskPartitionAction._mount_depth)
    # root first; every parent before its child
    assert ordered[0] == "/"
    assert ordered.index("/boot") < ordered.index("/boot/efi")
