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


# --- convergence, which is what stops a re-apply from wiping the disk ------- #
#
# VM-proven on 2026-08-12: the second `dasik apply` of a config with a
# random-key swap REPARTITIONED the disk. `_disk_converged` compares the
# declared partition labels against the filesystem labels lsblk reports, and a
# random-key swap carries the ext2 label (`cryptswap`) rather than its own —
# so "swap" was never found, the disk never converged, and `wipe_disk: true`
# fired again. Destructive, and silent right up until it happens.

def _disk(**over):
    from dasik.lib.models.disk_model import DiskLayout
    spec = {"device": "/dev/vda", "wipe_disk": True, "partitions": [
        {"label": "esp", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot"},
        {"label": "swap", "size": "1GiB", "filesystem": "swap",
         "partition_type": "linux-swap", "swap_encryption": "random"},
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/"},
    ]}
    spec.update(over)
    return DiskLayout(**spec)


def _converged(labels_on_disk):
    action = DiskPartitionAction({}, None)
    with patch.object(DiskPartitionAction, "_device_labels",
                      return_value=set(labels_on_disk)):
        return action._disk_converged(_disk())


def test_a_random_key_swap_converges_on_its_derived_label():
    assert _converged({"esp", "cryptswap", "root"}) is True


def test_the_declared_label_alone_is_not_convergence():
    """`swap` never appears on disk: the partition holds the 1 MiB ext2 whose
    label is `cryptswap`, and the swap itself lives behind /dev/mapper."""
    assert _converged({"esp", "swap", "root"}) is False


def test_a_missing_partition_is_still_not_converged():
    assert _converged({"esp", "cryptswap"}) is False


# --- and it must not be swapon'd during the install ------------------------ #
#
# Also VM-proven: the mount pass ran `swapon /dev/vda2` on the random-key swap.
# There is no swap signature there — the partition holds the 1 MiB ext2 label
# filesystem, and the swap only exists behind /dev/mapper from the first boot,
# once crypttab has created it. The command fails, and the install prints
# "Enabling swap" for something it did not enable.

def _mount_with(partition_specs):
    from dasik.lib.models.disk_model import DiskLayout
    action = DiskPartitionAction({}, None)
    disk = DiskLayout(device="/dev/vda", partitions=partition_specs)
    action.partition_map = {p["label"]: f"/dev/vda{i + 1}"
                            for i, p in enumerate(partition_specs)}
    with patch.object(DiskPartitionAction, "_mount_partition"), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        action._mount_partitions(disk)
    return [call[0] for call in run.call_args_list]


def test_a_plain_swap_is_still_enabled_during_the_install():
    calls = _mount_with([{"label": "swap", "size": "1GiB", "filesystem": "swap"}])
    assert ("swapon", ["/dev/vda1"]) in [(c[0], c[1]) for c in calls]


def test_a_random_key_swap_is_never_swapped_on():
    calls = _mount_with([{"label": "swap", "size": "1GiB", "filesystem": "swap",
                          "swap_encryption": "random"}])
    assert "swapon" not in [c[0] for c in calls]
