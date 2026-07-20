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


def _cfg_format_false():
    # A synced-style layout: every partition format:false / wipe_disk:false, yet
    # applied to a FRESH disk (no partition table) so plan() -> INSTALL and
    # _process_disk (re)creates every partition empty.
    return {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
        "partitions": [
            {"label": "boot", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot", "format": False},
            {"label": "root", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/", "format": False},
        ],
    }]}


def test_process_disk_formats_every_freshly_created_partition_even_format_false():
    # Regression: a freshly-created partition must be formatted even when
    # format:false — reaching _process_disk means the disk was (re)partitioned,
    # so every partition is new/empty. Leaving one raw made /boot unmountable and
    # genfstab produced an empty fstab.
    a = DiskPartitionAction(_cfg_format_false(), _ctx())
    disk = a.disks[0]
    with patch("dasik.lib.actions.disk_partition_action.Path.exists", return_value=True), \
         patch.object(DiskPartitionAction, "_validate_sizes"), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False), \
         patch.object(DiskPartitionAction, "_create_partition_table"), \
         patch.object(DiskPartitionAction, "_create_partitions"), \
         patch.object(DiskPartitionAction, "_refresh_partition_table"), \
         patch.object(DiskPartitionAction, "_mount_partitions"), \
         patch.object(DiskPartitionAction, "_format_partition") as fmt:
        a._process_disk(disk)
    formatted = {call.args[1].label for call in fmt.call_args_list}
    assert formatted == {"boot", "root"}      # BOTH formatted, not just format:true ones


def test_managed_keys_lists_converged():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert a.managed_keys() == {"disks": ["/dev/vda"]}


def test_import_state_empty_when_no_disks_and_nothing_discoverable():
    # Nothing declared AND nothing discoverable (no block devices) -> empty.
    a = DiskPartitionAction({}, _ctx())
    with patch.object(DiskPartitionAction, "_lsblk_tree", return_value=[]):
        assert a.import_state(managed=[]) == {}


def test_import_state_reflects_disks_non_destructively():
    # Capturing the declared layout back into the config (like
    # nixos-generate-config) must force format/wipe OFF so a synced config can
    # never reformat on re-apply.
    a = DiskPartitionAction(_cfg(wipe=True), _ctx("/"))
    frag = a.import_state(managed=[])
    disks = frag["disks"]["disks"]
    assert len(disks) == 1
    assert disks[0]["wipe_disk"] is False
    assert all(p["format"] is False for p in disks[0]["partitions"])
    # round-trips through the model unchanged in shape
    from dasik.lib.models.disk_model import DiskLayout
    assert DiskLayout.model_validate(disks[0]).device == "/dev/vda"


def _luks_cfg():
    return {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": True,
        "partitions": [
            {"label": "boot", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot", "format": True},
            {"label": "root", "size": "rest", "filesystem": "ext4",
             "partition_type": "linux", "mountpoint": "/", "format": True,
             "encrypt": True, "luks_name": "cryptroot", "luks_password": "secret"},
        ],
    }]}


def test_import_state_captures_luks_uuid_and_drops_password():
    a = DiskPartitionAction(_luks_cfg(), _ctx("/"))

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"12345678-abcd-0000-1111-222233334444\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake):
        frag = a.import_state(managed=[])
    root = frag["disks"]["disks"][0]["partitions"][1]
    assert root["luks_uuid"] == "12345678-abcd-0000-1111-222233334444"
    assert "luks_password" not in root          # plaintext secret never captured
    assert root["format"] is False


def test_import_state_output_reapplies_as_noop():
    # The captured stanza, fed back as a config, must plan to nothing on a disk
    # whose labels already match (the sync -> apply idempotency promise).
    a = DiskPartitionAction(_cfg(wipe=True), _ctx("/"))
    frag = a.import_state(managed=[])
    b = DiskPartitionAction(frag["disks"], _ctx("/"))
    with patch.object(DiskPartitionAction, "_device_labels", return_value={"boot", "root"}):
        assert b.plan(managed=["/dev/vda"]) == []


def test_device_labels_parses_lsblk():
    a = DiskPartitionAction(_cfg(), _ctx())
    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               return_value=MagicMock(stdout=b"boot\nroot\n\n")):
        assert a._device_labels("/dev/vda") == {"boot", "root"}


def test_name_and_optional():
    a = DiskPartitionAction({})
    assert a.name == "Disk Partitioning"
    assert a.is_optional is True


def test_import_state_recovers_fido2_tpm2_from_luks_header():
    a = DiskPartitionAction(_luks_cfg(), _ctx("/"))

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"12345678-abcd-0000-1111-222233334444\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksDump":
            return MagicMock(stdout=b"Tokens:\n  0: systemd-fido2\n  1: systemd-tpm2\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake):
        frag = a.import_state(managed=[])
    root = frag["disks"]["disks"][0]["partitions"][1]
    assert root["unlock_fido2"] is True
    assert root["unlock_tpm2"] is True


def test_import_state_no_tokens_leaves_unlock_flags_false():
    a = DiskPartitionAction(_luks_cfg(), _ctx("/"))

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksDump":
            return MagicMock(stdout=b"Tokens:\n  (none)\n", returncode=0)
        return MagicMock(stdout=b"uuid\n", returncode=0)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake):
        frag = a.import_state(managed=[])
    root = frag["disks"]["disks"][0]["partitions"][1]
    assert root.get("unlock_fido2") is False and root.get("unlock_tpm2") is False


def test_import_state_recovers_luks_options_from_cmdline():
    a = DiskPartitionAction(_luks_cfg(), _ctx("/"))

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"THEUUID\n", returncode=0)
        return MagicMock(stdout=b"Tokens:\n", returncode=0)

    cmdline = "BOOT_IMAGE=/vmlinuz rd.luks.options=THEUUID=fido2-device=auto,token-timeout=10s rw"
    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(DiskPartitionAction, "_kernel_cmdline_text", return_value=cmdline):
        frag = a.import_state(managed=[])
    root = frag["disks"]["disks"][0]["partitions"][1]
    assert root["luks_options"] == ["token-timeout=10s"]   # auto fido2 token dropped


def test_import_state_no_luks_options_when_only_auto():
    a = DiskPartitionAction(_luks_cfg(), _ctx("/"))

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "cryptsetup" and args and args[0] == "status":
            return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
        if cmd == "cryptsetup" and args and args[0] == "luksUUID":
            return MagicMock(stdout=b"THEUUID\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(DiskPartitionAction, "_kernel_cmdline_text",
                      return_value="rd.luks.options=THEUUID=fido2-device=auto"):
        frag = a.import_state(managed=[])
    assert frag["disks"]["disks"][0]["partitions"][1]["luks_options"] == []


# --- mountpoint permissions (F-20) ---------------------------------------- #
#
# `mkdir` gives 0755, so /mnt/var/tmp existed as 0755 before pacstrap and pacman
# warned "directory permissions differ ... filesystem: 755 package: 1777".
# /var/tmp and /tmp must be world-writable + sticky from the moment they exist.

import os as _os
from dasik.lib.actions.disk_partition_action import _mountpoint_mode, _make_mountpoint


def test_var_tmp_and_tmp_get_sticky_world_writable_mode():
    assert _mountpoint_mode("/var/tmp") == 0o1777
    assert _mountpoint_mode("/tmp") == 0o1777


def test_ordinary_mountpoint_has_no_forced_mode():
    assert _mountpoint_mode("/home") is None
    assert _mountpoint_mode("/") is None


def test_make_mountpoint_applies_the_mode(tmp_path):
    host = tmp_path / "mnt" / "var" / "tmp"
    _make_mountpoint(str(host), "/var/tmp")
    assert _os.stat(host).st_mode & 0o7777 == 0o1777


def test_make_mountpoint_fixes_an_existing_wrong_mode(tmp_path):
    host = tmp_path / "mnt" / "var" / "tmp"
    host.mkdir(parents=True)
    _os.chmod(host, 0o755)
    _make_mountpoint(str(host), "/var/tmp")
    assert _os.stat(host).st_mode & 0o7777 == 0o1777


def test_make_mountpoint_leaves_ordinary_modes_alone(tmp_path):
    host = tmp_path / "mnt" / "home"
    _make_mountpoint(str(host), "/home")
    assert host.is_dir()
    assert _os.stat(host).st_mode & 0o7777 != 0o1777
