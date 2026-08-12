"""What gets written to a swap partition depends on how it is encrypted.

A plain swap gets `mkswap`. A random-key swap gets a 1 MiB ext2 filesystem
instead: crypttab's `swap` option runs `mkswap` itself on the mapper device at
every boot, and what the partition needs to keep is the LABEL the crypttab entry
addresses it by. Formatting it as swap here would leave that entry pointing at a
label nothing provides — and a crypttab `swap` entry that resolves to the wrong
device reformats it.
"""
from unittest.mock import patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.models.disk_model import Partition


def _action():
    action = DiskPartitionAction({}, None)
    action.partition_map = {"swap": "/dev/vda2"}
    return action


def test_a_plain_swap_partition_is_mkswapped():
    part = Partition(label="swap", size="8GiB", filesystem="swap")
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        _action()._format_partition("/dev/vda", part)
    run.assert_called_once_with("mkswap", ["-L", "swap", "/dev/vda2"])


def test_a_random_key_swap_gets_a_1MiB_ext2_label_filesystem_instead():
    part = Partition(label="swap", size="8GiB", filesystem="swap",
                     swap_encryption="random")
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        _action()._format_partition("/dev/vda", part)
    run.assert_called_once_with(
        "mkfs.ext2", ["-F", "-L", "cryptswap", "/dev/vda2", "1M"])


def test_the_label_filesystem_never_takes_the_whole_partition():
    """The size argument is the point: without it mkfs.ext2 would claim the
    entire partition and there would be no room behind it for the swap."""
    part = Partition(label="swap", size="8GiB", filesystem="swap",
                     swap_encryption="random")
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        _action()._format_partition("/dev/vda", part)
    assert run.call_args[0][1][-1] == "1M"
