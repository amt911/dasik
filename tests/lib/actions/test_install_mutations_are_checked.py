"""A mutation nobody checks is a mutation that can fail unnoticed.

Three live install-path commands ran without `check=True`:

  * `btrfs subvolume create` — a failure leaves the subvolume missing, and the
    machine installs "successfully" with no /home;
  * the two `mount` calls — the exact family behind "genfstab produced an empty
    fstab" (#147): the mount fails, nothing is mounted there, and the abort
    arrives later and points somewhere else;
  * `ufw allow …` and `ufw --force enable` — a rule that was never added or a
    firewall that was never enabled, reported as applied.

`umount` and the probes are deliberately left alone: a best-effort unmount that
fails is benign, and for a probe a non-zero exit IS the answer.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.models.disk_model import BtrfsSubvolume, Partition
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _calls(execute, cmd):
    return [c for c in execute.call_args_list if c.args[0] == cmd]


def test_creating_a_subvolume_is_checked(tmp_path):
    action = DiskPartitionAction({}, None)
    subvols = [BtrfsSubvolume(name="@home", mountpoint="/home")]

    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute, \
         patch("pathlib.Path.mkdir"), patch("pathlib.Path.rmdir"):
        execute.return_value = MagicMock(returncode=0)
        action._create_btrfs_subvolumes("/dev/vda2", subvols)

    create = _calls(execute, "btrfs")[0]
    assert create.kwargs.get("check") is True


def test_mounting_a_partition_is_checked():
    action = DiskPartitionAction({}, None)
    action.partition_map = {"ROOT": "/dev/vda2"}
    part = Partition(label="ROOT", size="rest", filesystem="ext4",
                     partition_type="linux", mountpoint="/")

    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute, \
         patch("dasik.lib.actions.disk_partition_action._make_mountpoint"):
        execute.return_value = MagicMock(returncode=0)
        action._mount_partition(part)

    assert _calls(execute, "mount")[0].kwargs.get("check") is True


def test_mounting_a_subvolume_is_checked():
    action = DiskPartitionAction({}, None)
    action.partition_map = {"ROOT": "/dev/vda2"}
    part = Partition(label="ROOT", size="rest", filesystem="btrfs",
                     partition_type="linux",
                     btrfs_subvolumes=[BtrfsSubvolume(name="@home", mountpoint="/home")])

    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute, \
         patch("dasik.lib.actions.disk_partition_action._make_mountpoint"):
        execute.return_value = MagicMock(returncode=0)
        action._mount_btrfs_subvolumes(part)

    assert _calls(execute, "mount")[0].kwargs.get("check") is True


def test_the_unmount_is_still_best_effort(tmp_path):
    """A leftover mount is benign; aborting an install over it is not."""
    action = DiskPartitionAction({}, None)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute, \
         patch("pathlib.Path.mkdir"), patch("pathlib.Path.rmdir"):
        execute.return_value = MagicMock(returncode=0)
        action._create_btrfs_subvolumes("/dev/vda2", [])

    assert _calls(execute, "umount")[0].kwargs.get("check") is not True


def test_firewall_rules_and_the_enable_are_checked(tmp_path):
    action = FirewallAction({"firewall": {"backend": "ufw", "rules": ["22/tcp"]}},
                            ActionContext(target=Target(root=str(tmp_path))))

    with patch("dasik.lib.actions.firewall_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action._apply_ufw([Change("firewall", Op.INSTALL, "allow 22/tcp")])

    ufw = _calls(execute, "ufw")
    assert ufw, "expected ufw to be called"
    assert all(c.kwargs.get("check") is True for c in ufw)
