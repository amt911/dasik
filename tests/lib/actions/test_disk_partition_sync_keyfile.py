"""`sync` must read the pendrive unlock back into the partition.

Otherwise the keyfile is a one-way street: capture the machine, re-apply the
captured config, and `rd.luks.key` silently disappears — the machine keeps
booting only because the keyslot is still enrolled, until the next initramfs is
built without the module and it stops.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.target.target import Target


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _cfg():
    """The `disks` SECTION — this action is registered with config_key='disks'."""
    return {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": True,
        "partitions": [
            {"label": "boot", "size": "512MiB", "filesystem": "fat32",
             "partition_type": "esp", "mountpoint": "/boot"},
            {"label": "root", "size": "rest", "filesystem": "ext4",
             "mountpoint": "/", "encrypt": True, "luks_name": "cryptroot",
             "luks_password": "pw"},
        ],
    }]}


def _fake_cryptsetup(cmd, args=None, *_rest, **_kw):
    if cmd == "cryptsetup" and args and args[0] == "status":
        return MagicMock(stdout=b"  device:  /dev/vda2\n", returncode=0)
    if cmd == "cryptsetup" and args and args[0] == "luksUUID":
        return MagicMock(stdout=b"THEUUID\n", returncode=0)
    return MagicMock(stdout=b"Tokens:\n", returncode=0)


def _captured(cmdline, fstype="vfat"):
    action = DiskPartitionAction(_cfg(), _ctx())

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "lsblk":
            return MagicMock(stdout=f"{fstype}\n".encode(), returncode=0)
        return _fake_cryptsetup(cmd, args, *rest, **kw)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(DiskPartitionAction, "_kernel_cmdline_text", return_value=cmdline):
        frag = action.import_state(managed=[])
    return frag["disks"]["disks"][0]["partitions"][1]


def test_a_live_pendrive_unlock_is_captured():
    part = _captured("BOOT_IMAGE=/vmlinuz rd.luks.name=THEUUID=cryptroot "
                     "rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD rw")

    assert part["unlock_keyfile"] == "/keyfile"
    assert part["unlock_keydev"] == "UUID=1234-ABCD"
    assert part["unlock_keydev_fs"] == "vfat"


def test_an_embedded_keyfile_is_captured_without_a_key_device():
    part = _captured("rd.luks.key=THEUUID=/etc/keyfile rw")

    assert part["unlock_keyfile"] == "/etc/keyfile"
    assert part.get("unlock_keydev") is None
    assert part.get("unlock_keydev_fs") is None


def test_a_keyfile_for_another_volume_is_not_stolen():
    """The parameter is per-UUID: another encrypted device's key is not this
    partition's."""
    part = _captured("rd.luks.key=OTHERUUID=/keyfile:UUID=1234-ABCD rw")

    assert part.get("unlock_keyfile") is None


def test_a_machine_without_a_keyfile_invents_nothing():
    part = _captured("BOOT_IMAGE=/vmlinuz rd.luks.name=THEUUID=cryptroot rw")

    assert part.get("unlock_keyfile") is None
    assert part.get("unlock_keydev") is None


def test_an_unprobeable_key_device_still_captures_the_unlock():
    """No lsblk, no filesystem — but dropping the whole unlock because one
    detail could not be probed would lose the feature entirely."""
    action = DiskPartitionAction(_cfg(), _ctx())

    def fake(cmd, args=None, *rest, **kw):
        if cmd == "lsblk":
            raise OSError("no lsblk")
        return _fake_cryptsetup(cmd, args, *rest, **kw)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute", side_effect=fake), \
         patch.object(DiskPartitionAction, "_kernel_cmdline_text",
                      return_value="rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD"):
        frag = action.import_state(managed=[])
    part = frag["disks"]["disks"][0]["partitions"][1]

    assert part["unlock_keyfile"] == "/keyfile"
    assert part["unlock_keydev"] == "UUID=1234-ABCD"
    assert part.get("unlock_keydev_fs") is None


# --- the derived timeout must not be captured twice ------------------------ #

def test_the_derived_keyfile_timeout_is_not_captured_as_a_luks_option():
    """dasik re-derives keyfile-timeout=10s for a key-device unlock; capturing
    it as well would spell the same policy twice."""
    part = _captured("rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD "
                     "rd.luks.options=THEUUID=keyfile-timeout=10s")

    assert part.get("luks_options", []) == []


def test_a_non_default_timeout_is_kept():
    """30s is the user's, not dasik's default — dropping it would change the
    machine on the next apply."""
    part = _captured("rd.luks.key=THEUUID=/keyfile:UUID=1234-ABCD "
                     "rd.luks.options=THEUUID=keyfile-timeout=30s")

    assert part["luks_options"] == ["keyfile-timeout=30s"]


def test_a_timeout_without_a_keyfile_is_kept_verbatim():
    """Nothing derives it here, so it is somebody's explicit option."""
    part = _captured("rd.luks.options=THEUUID=keyfile-timeout=10s")

    assert part["luks_options"] == ["keyfile-timeout=10s"]
