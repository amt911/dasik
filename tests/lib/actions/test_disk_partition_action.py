from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root="/mnt"):
    return ActionContext(target=Target(root=root))


def _cfg(device="/dev/vda", wipe=False):
    return {"disks": [{
        "device": device,
        "partition_table": "gpt",
        "wipe_disk": wipe,
        "partitions": [
            {"label": "boot", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot", "format": True},
            {"label": "root", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/", "format": True},
        ],
    }]}


def test_is_v3_true():
    assert DiskPartitionAction.is_v3() is True


def test_empty_config_is_dict():
    assert DiskPartitionAction.empty_config() == {}


def test_no_disks_plan_empty():
    a = DiskPartitionAction({}, _ctx())
    assert a.plan(managed=[]) == []
    assert a.actual() == set()


def test_actual_converged_when_labels_present():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root", "swap"}):
        assert a.actual() == {"/dev/vda"}


def test_actual_empty_when_labels_missing():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()):
        assert a.actual() == set()


def test_plan_empty_when_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert a.plan(managed=[]) == []


def test_plan_install_when_empty_disk():
    a = DiskPartitionAction(_cfg(wipe=False), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False):
        changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "/dev/vda"


def test_plan_install_when_wipe():
    a = DiskPartitionAction(_cfg(wipe=True), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"old"}), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=True):
        changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL


def test_plan_skips_populated_disk_without_wipe(capsys):
    a = DiskPartitionAction(_cfg(wipe=False), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"old"}), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=True):
        changes = a.plan(managed=[])
    assert changes == []                       # refuse to clobber
    assert "wipe_disk" in capsys.readouterr().out


def test_apply_processes_changed_disks():
    a = DiskPartitionAction(_cfg(wipe=True), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False), \
         patch.object(DiskPartitionAction, "_process_disk") as proc:
        a.apply(a.plan(managed=[]))
    proc.assert_called_once()


def test_apply_noop_when_no_changes_and_not_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set()), \
         patch.object(DiskPartitionAction, "_process_disk") as proc, \
         patch.object(DiskPartitionAction, "_mount_existing") as me:
        a.apply([])
    proc.assert_not_called()
    me.assert_not_called()


def test_apply_mounts_existing_on_converged_rerun():
    # converged disk (all labels present) + install target + no plan changes
    # -> mount the existing partitions, never re-format
    a = DiskPartitionAction(_cfg(), _ctx("/mnt"))
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}), \
         patch.object(DiskPartitionAction, "_process_disk") as proc, \
         patch.object(DiskPartitionAction, "_mount_existing") as me:
        a.apply([])
    proc.assert_not_called()        # NEVER re-partition/format a converged disk
    me.assert_called_once()


def test_apply_does_not_mount_existing_on_live_host():
    a = DiskPartitionAction(_cfg(), _ctx("/"))      # root="/" not an install target
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}), \
         patch.object(DiskPartitionAction, "_mount_existing") as me:
        a.apply([])
    me.assert_not_called()


def test_mount_existing_rebuilds_map_by_config_order():
    a = DiskPartitionAction(_cfg(device="/dev/vda"), _ctx("/mnt"))
    with patch.object(DiskPartitionAction, "_mount_partitions") as mp:
        a._mount_existing(a.disks[0])
    assert a.partition_map["boot"] == "/dev/vda1"   # config order: boot=1, root=2
    assert a.partition_map["root"] == "/dev/vda2"
    mp.assert_called_once()


def test_mount_partitions_root_before_boot():
    # / must mount before /boot so the ESP isn't shadowed under the root fs
    a = DiskPartitionAction(_cfg(), _ctx("/mnt"))
    a.partition_map = {"boot": "/dev/vda1", "root": "/dev/vda2"}
    order = []
    with patch.object(DiskPartitionAction, "_mount_partition",
                      side_effect=lambda p: order.append(p.mountpoint)), \
         patch("dasik.lib.actions.disk_partition_action.Command.execute"):
        a._mount_partitions(a.disks[0])
    assert order[0] == "/"          # root first


def test_managed_keys_lists_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert a.managed_keys() == {"disks": ["/dev/vda"]}


def test_import_state_empty():
    a = DiskPartitionAction(_cfg(), _ctx())
    assert a.import_state(managed=[]) == {}


def test_device_labels_parses_lsblk():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               return_value=MagicMock(stdout=b"boot\nroot\n\n")):
        assert a._device_labels("/dev/vda") == {"boot", "root"}


def _cfg_sizes(*sizes, device="/dev/vda"):
    parts = [{"label": f"p{i}", "size": s, "filesystem": "ext4",
              "partition_type": "linux", "mountpoint": "/", "format": True}
             for i, s in enumerate(sizes)]
    return {"disks": [{"device": device, "partition_table": "gpt",
                       "wipe_disk": True, "partitions": parts}]}


def test_size_to_mib():
    assert DiskPartitionAction._size_to_mib("512MiB") == 512
    assert DiskPartitionAction._size_to_mib("4GiB") == 4096
    assert round(DiskPartitionAction._size_to_mib("1GB")) == 954   # decimal GB


def test_validate_sizes_raises_when_layout_exceeds_disk():
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = DiskPartitionAction(_cfg_sizes("512MiB", "4GiB", "25GiB"), _ctx())  # ~29.5GiB
    with patch.object(DiskPartitionAction, "_get_disk_size_mib", return_value=20480):  # 20GiB
        with pytest.raises(CommandExecutionError) as e:
            a._validate_sizes(a.disks[0])
    assert "bigger disk" in str(e.value)


def test_validate_sizes_ok_when_fits():
    a = DiskPartitionAction(_cfg_sizes("512MiB", "4GiB", "rest"), _ctx())
    with patch.object(DiskPartitionAction, "_get_disk_size_mib", return_value=20480):
        a._validate_sizes(a.disks[0])  # no raise (rest skipped)


def test_process_disk_validates_before_wiping():
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch("dasik.lib.actions.disk_partition_action.Path") as P, \
         patch.object(DiskPartitionAction, "_validate_sizes",
                      side_effect=CommandExecutionError("too big")), \
         patch.object(DiskPartitionAction, "_wipe_disk") as wipe:
        P.return_value.exists.return_value = True
        with pytest.raises(CommandExecutionError):
            a._process_disk(a.disks[0])
    wipe.assert_not_called()   # validation aborts BEFORE any destructive op


def test_name_and_optional():
    a = DiskPartitionAction({})
    assert a.name == "Disk Partitioning"
    assert a.is_optional is True
