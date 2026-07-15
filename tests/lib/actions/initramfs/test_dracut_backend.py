from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.target.target import Target


def _cfg(encrypt=False, fs="ext4"):
    part = {"mountpoint": "/", "filesystem": fs}
    if encrypt:
        part["encrypt"] = True
    return {"disks": {"disks": [{"partitions": [part]}]}}


def _b(cfg, root="/"):
    return DracutBackend(cfg, Target(root=root))


def test_desired_encrypted_uses_hostonly_and_systemd():
    # crypt is auto-detected in hostonly; we must NOT add it explicitly (its
    # non-systemd handler competes with systemd-cryptsetup and breaks unlock).
    conf = _b(_cfg(encrypt=True)).desired_value()
    assert 'hostonly="yes"' in conf and "systemd" in conf
    assert "add_dracutmodules" not in conf or "crypt" not in conf


def test_desired_includes_btrfs_when_btrfs_root():
    assert "btrfs" in _b(_cfg(fs="btrfs")).desired_value()


def test_desired_empty_when_nothing_to_add():
    assert _b(_cfg()).desired_value() == ""


def test_desired_is_deterministic():
    b = _b(_cfg(encrypt=True, fs="btrfs"))
    assert b.desired_value() == b.desired_value()
    assert 'hostonly="yes"' in b.desired_value() and "systemd" in b.desired_value()


def test_actual_value_reads_conf():
    with patch("builtins.open", mock_open(read_data="add_dracutmodules+=\" crypt \"\n")):
        assert _b(_cfg(encrypt=True)).actual_value() == "add_dracutmodules+=\" crypt \"\n"


def test_actual_value_none_when_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b(_cfg(encrypt=True)).actual_value() is None


def test_apply_writes_conf_and_regenerates():
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    assert m.call_args_list[0].args[0] == "/etc/dracut.conf.d/dasik.conf"
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "systemd" in body
    assert (run.call_args.args[0], run.call_args.args[1]) == (
        "dracut", ["--regenerate-all", "--force"])
    assert run.call_args.kwargs["target"].root == "/"


# --- FIDO2 / TPM2 / bluetooth-in-initramfs (mirrors a real dracut setup) --- #

def _cfg_unlock(*, fido2=False, tpm2=False, bt_initramfs=False, encrypt=True, fs="btrfs"):
    part = {"mountpoint": "/", "filesystem": fs}
    if encrypt:
        part["encrypt"] = True
        part["luks_name"] = "cryptroot"
    if fido2:
        part["unlock_fido2"] = True
    if tpm2:
        part["unlock_tpm2"] = True
    cfg = {"disks": {"disks": [{"partitions": [part]}]}}
    if bt_initramfs:
        cfg["bluetooth"] = {"enable": True, "in_initramfs": True}
    return cfg


def test_fido2_forces_systemd_and_fido2_modules_with_hostonly():
    conf = _b(_cfg_unlock(fido2=True)).desired_value()
    assert 'hostonly="yes"' in conf
    assert "force_add_dracutmodules" in conf
    assert "systemd" in conf and "fido2" in conf


def test_tpm2_forces_systemd_and_tpm2_modules_with_hostonly():
    conf = _b(_cfg_unlock(tpm2=True)).desired_value()
    assert 'hostonly="yes"' in conf
    assert "tpm2-tss" in conf and "systemd" in conf


def test_bluetooth_in_initramfs_adds_bluetooth_module():
    conf = _b(_cfg_unlock(bt_initramfs=True)).desired_value()
    assert "bluetooth" in conf
    # bluetooth is a regular add module, not a forced one
    assert "add_dracutmodules" in conf


def test_hostonly_set_for_any_encryption():
    # hostonly bakes the crypt device in so systemd-cryptsetup can open it at boot.
    assert 'hostonly="yes"' in _b(_cfg_unlock(encrypt=True, fido2=False)).desired_value()


def test_no_hostonly_without_encryption():
    conf = _b(_cfg_unlock(encrypt=False, fido2=False, tpm2=False, fs="btrfs")).desired_value()
    assert "hostonly" not in conf


def test_combined_fido2_bluetooth_btrfs():
    conf = _b(_cfg_unlock(fido2=True, bt_initramfs=True, fs="btrfs")).desired_value()
    assert 'hostonly="yes"' in conf
    for tok in ("systemd", "fido2", "bluetooth"):
        assert tok in conf, tok
    assert "crypt" not in conf   # auto-detected in hostonly


def test_encryption_alone_forces_systemd_module():
    # rd.luks.name=<uuid>=<name> (dasik's cmdline) is only honored by the systemd
    # dracut module; without it the boot hangs on /dev/mapper/<name>. So ANY
    # encrypted root must force `systemd`, not just fido2/tpm2.
    conf = _b(_cfg_unlock(fido2=False, tpm2=False, encrypt=True)).desired_value()
    assert "force_add_dracutmodules" in conf and "systemd" in conf
    assert "crypt" not in conf   # auto-detected in hostonly, never added explicitly


def test_crypttab_entry_for_encrypted_root():
    from dasik.lib.actions.luks_uuid import luks_uuid
    ct = _b(_cfg_unlock(encrypt=True)).crypttab()
    assert f"cryptroot UUID={luks_uuid('cryptroot')} none luks" in ct


def test_no_crypttab_without_encryption():
    assert _b(_cfg_unlock(encrypt=False)).crypttab() == ""
