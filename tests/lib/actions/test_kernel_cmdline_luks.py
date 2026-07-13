"""KernelCmdlineAction LUKS derivation — a single apply must emit rd.luks.name.

Regression from the encrypted VM install: the UUID was read via `blkid`, whose
/run cache is stale right after `luksFormat`, so `rd.luks.name` missed the first
apply → a non-bootable encrypted entry until a redundant second apply. The fix
reads the UUID from the LUKS header via `cryptsetup luksUUID`. These tests pin
that the derived cmdline carries both the mapper root and rd.luks.name.
"""
from types import SimpleNamespace
from unittest.mock import patch

from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction

_UUID = "1a7d8cb0-3c60-419a-bd8d-23bd93f0390e"


def _fake_cryptsetup(cmd, args, *a, **kw):
    if args[:1] == ["status"]:
        return SimpleNamespace(returncode=0, stdout=b"  device:  /dev/vda2\n")
    if args[:1] == ["luksUUID"]:
        return SimpleNamespace(returncode=0, stdout=(_UUID + "\n").encode())
    return SimpleNamespace(returncode=0, stdout=b"")


def _action():
    cfg = {"bootloader": "sd-boot", "disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"mountpoint": "/", "label": "ROOT", "filesystem": "ext4",
         "encrypt": True, "luks_name": "cryptroot"}]}]}}
    return KernelCmdlineAction(cfg, context=SimpleNamespace(target=SimpleNamespace(is_chroot=False, root="/")))


def test_encrypted_root_derives_mapper_and_rd_luks_name():
    from dasik.lib.actions.luks_uuid import luks_uuid
    a = _action()
    tokens = a._desired_tokens()          # deterministic — no device probe
    assert "root=/dev/mapper/cryptroot" in tokens
    assert f"rd.luks.name={luks_uuid('cryptroot')}=cryptroot" in tokens


def test_resolve_luks_uuid_reads_header_not_blkid():
    a = _action()
    calls = []

    def spy(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args[:1])))
        return _fake_cryptsetup(cmd, args, *aa, **kw)

    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute", side_effect=spy):
        uuid = a._resolve_luks_uuid("cryptroot")
    assert uuid == _UUID
    assert ("cryptsetup", ("luksUUID",)) in calls   # header read, not blkid
    assert all(c[0] != "blkid" for c in calls)
