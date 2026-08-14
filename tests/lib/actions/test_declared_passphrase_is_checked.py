"""A passphrase in the config that does not open the disk is worth knowing.

`luks_password` is only ever used while FORMATTING. Change it on an installed
machine and dasik does nothing and says nothing: the disk keeps its old
passphrase, the config claims the new one, and you find out at the next reboot —
which is the worst possible moment to find out.

dasik can simply ask. `cryptsetup open --test-passphrase` creates no mapping, so
it is safe to run from `plan()` (the keyfile domain already probes exactly this
way). A warning, never a change: dasik must not rewrite keyslots behind your
back, and the fix is a `cryptsetup luksChangeKey` you run yourself.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.target.target import Target

_CFG = {"disks": {"disks": [{
    "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
    "partitions": [
        {"label": "ESP", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot"},
        {"label": "ROOT", "size": "rest", "filesystem": "ext4",
         "partition_type": "linux", "mountpoint": "/", "encrypt": True,
         "luks_name": "cryptroot", "luks_password": "hunter2"},
    ]}]}}


def _action(tmp_path):
    action = DiskPartitionAction(_CFG["disks"], ActionContext(target=Target(root=str(tmp_path))))
    action._disk_converged = lambda disk: True          # an installed machine
    action._luks_backing_device = lambda name: "/dev/vda2"
    return action


def _warnings(action, passphrase_works):
    seen = []
    def execute(cmd, args, *a, **kw):
        if cmd == "cryptsetup" and "--test-passphrase" in args:
            return MagicMock(returncode=0 if passphrase_works else 2)
        return MagicMock(returncode=0, stdout=b"")
    logger = MagicMock()
    logger.warning.side_effect = lambda msg, detail=None: seen.append(msg)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=execute), \
         patch("dasik.lib.actions.disk_partition_action.run_logger.get", return_value=logger):
        action.plan(managed=[])
    return seen


def test_a_passphrase_that_does_not_open_the_disk_is_named(tmp_path):
    warnings = _warnings(_action(tmp_path), passphrase_works=False)

    assert any("ROOT" in w and "passphrase" in w for w in warnings), warnings


def test_the_right_passphrase_says_nothing(tmp_path):
    assert _warnings(_action(tmp_path), passphrase_works=True) == []


def test_it_never_becomes_a_change(tmp_path):
    """Rewriting a keyslot behind someone's back is how a disk is lost."""
    action = _action(tmp_path)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               return_value=MagicMock(returncode=2, stdout=b"")), \
         patch("dasik.lib.actions.disk_partition_action.run_logger.get", return_value=MagicMock()):
        assert action.plan(managed=[]) == []


def test_a_disk_that_is_not_converged_is_not_probed(tmp_path):
    """A fresh disk is about to be formatted with that very passphrase."""
    action = _action(tmp_path)
    action._disk_converged = lambda disk: False
    calls = []
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute, \
         patch("dasik.lib.actions.disk_partition_action.run_logger.get", return_value=MagicMock()):
        execute.side_effect = lambda cmd, args, *a, **kw: calls.append((cmd, args)) or MagicMock(
            returncode=0, stdout=b"")
        action.plan(managed=[])

    assert not any("--test-passphrase" in a for _c, a in calls)


def test_a_probe_that_cannot_run_says_nothing(tmp_path):
    """No cryptsetup, no device, a locked-out target: unknown is not a warning."""
    action = _action(tmp_path)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=OSError("no cryptsetup")), \
         patch("dasik.lib.actions.disk_partition_action.run_logger.get") as logger:
        action.plan(managed=[])

    logger.return_value.warning.assert_not_called()
