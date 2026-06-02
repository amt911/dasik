from unittest.mock import MagicMock, patch

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


def test_apply_noop_when_no_changes():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_process_disk") as proc:
        a.apply([])
    proc.assert_not_called()


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


def test_name_and_optional():
    a = DiskPartitionAction({})
    assert a.name == "Disk Partitioning"
    assert a.is_optional is True
