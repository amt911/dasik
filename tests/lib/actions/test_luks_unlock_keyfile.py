"""LUKS keyfile/pendrive unlock: extra key added + rd.luks.key derived.

An `unlock_keyfile` is added as an extra LUKS key (authorised by the existing
passphrase/keyfile), and kernel-cmdline emits `rd.luks.key=<uuid>=<keyfile>` so
the initramfs unlocks automatically (with a device UUID appended for a pendrive).
The passphrase keeps working. Boot behaviour needs QEMU+USB to fully verify;
these tests pin the command + cmdline construction.
"""
from types import SimpleNamespace
from unittest.mock import patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.models.disk_model import Partition


def _add_key(partition):
    action = DiskPartitionAction(config=None)
    with patch.object(DiskPartitionAction, "_add_unlock_keyfile", wraps=action._add_unlock_keyfile):
        with patch("dasik.lib.actions.disk_partition_action.Command.execute") as ex:
            action._add_unlock_keyfile("/dev/vda2", partition)
    return ex.call_args_list


def test_model_accepts_unlock_fields():
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", luks_password="pw",
                  unlock_keyfile="/key/root.key", unlock_keydev="AAAA-BBBB")
    assert p.unlock_keyfile == "/key/root.key" and p.unlock_keydev == "AAAA-BBBB"


def test_add_key_with_password_pipes_existing_over_stdin():
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", luks_password="pw", unlock_keyfile="/k.key")
    calls = _add_key(p)
    c = calls[0]
    assert c.args[0] == "cryptsetup"
    assert c.args[1] == ["luksAddKey", "--key-file", "-", "/dev/vda2", "/k.key"]
    assert c.kwargs["input"] == b"pw"          # existing passphrase authorises the add


def test_add_key_with_existing_keyfile():
    p = Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True,
                  luks_name="cryptroot", luks_keyfile="/old.key", unlock_keyfile="/k.key")
    calls = _add_key(p)
    assert calls[0].args[1] == ["luksAddKey", "--key-file", "/old.key", "/dev/vda2", "/k.key"]


def _cmdline(unlock_keyfile=None, unlock_keydev=None, luks_options=None):
    part = {"mountpoint": "/", "filesystem": "ext4", "encrypt": True,
            "luks_name": "cryptroot"}
    if unlock_keyfile:
        part["unlock_keyfile"] = unlock_keyfile
    if unlock_keydev:
        part["unlock_keydev"] = unlock_keydev
    if luks_options:
        part["luks_options"] = luks_options
    cfg = {"bootloader": "sd-boot", "disks": {"disks": [{"partitions": [part]}]}}
    a = KernelCmdlineAction(cfg, context=SimpleNamespace(target=None))
    return a._derive_from_disks()


def test_no_keyfile_no_rd_luks_key():
    assert not any(t.startswith("rd.luks.key=") for t in _cmdline())


def test_keyfile_emits_rd_luks_key():
    u = luks_uuid("cryptroot")
    tokens = _cmdline(unlock_keyfile="/key/root.key")
    assert f"rd.luks.key={u}=/key/root.key" in tokens


def test_pendrive_keyfile_appends_keydev_uuid():
    """A bare UUID is normalized to `UUID=<uuid>`.

    Arch wiki: `rd.luks.key=<luks-uuid>=/path:UUID=<fs-uuid>`. Emitting the raw
    value — which is exactly what the field documents you should write — gives
    systemd-cryptsetup a device spec it cannot resolve, so the machine waits for
    a key device it will never find.
    """
    u = luks_uuid("cryptroot")
    tokens = _cmdline(unlock_keyfile="/root.key", unlock_keydev="1234-ABCD")
    assert f"rd.luks.key={u}=/root.key:UUID=1234-ABCD" in tokens


def test_an_explicit_device_spec_is_passed_through():
    u = luks_uuid("cryptroot")
    for spec in ("UUID=1234-ABCD", "PARTUUID=abcd-0001", "LABEL=pen"):
        tokens = _cmdline(unlock_keyfile="/root.key", unlock_keydev=spec)
        assert f"rd.luks.key={u}=/root.key:{spec}" in tokens


def test_a_key_device_unlock_gets_a_keyfile_timeout():
    """Same wiki page: `rd.luks.key` with a keyfile on another device does NOT
    fall back to asking for a password when that device is absent. Without
    keyfile-timeout, booting without the pendrive hangs forever."""
    tokens = _cmdline(unlock_keyfile="/root.key", unlock_keydev="1234-ABCD")
    options = [t for t in tokens if t.startswith("rd.luks.options=")]
    assert options and "keyfile-timeout=10s" in options[0]


def test_an_explicit_keyfile_timeout_wins():
    tokens = _cmdline(unlock_keyfile="/root.key", unlock_keydev="1234-ABCD",
                      luks_options=["keyfile-timeout=30s"])
    options = [t for t in tokens if t.startswith("rd.luks.options=")]
    assert "keyfile-timeout=30s" in options[0]
    assert "keyfile-timeout=10s" not in options[0]


def test_an_embedded_keyfile_gets_no_timeout():
    """No key device to wait for: the keyfile travels inside the initramfs."""
    tokens = _cmdline(unlock_keyfile="/etc/keyfile")
    assert not any("keyfile-timeout" in t for t in tokens)


def test_a_partition_without_a_keyfile_gets_no_timeout():
    assert not any("keyfile-timeout" in t for t in _cmdline())
