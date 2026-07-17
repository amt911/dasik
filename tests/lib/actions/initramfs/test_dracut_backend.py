from unittest.mock import mock_open, patch

from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.target.target import Target


def _cfg(encrypt=False, fs="ext4"):
    part = {"mountpoint": "/", "filesystem": fs}
    if encrypt:
        part["encrypt"] = True
    return {"disks": {"disks": [{"partitions": [part]}]}}


def _b(cfg, root="/"):
    return DracutBackend(cfg, Target(root=root))


# --- forced modules: an encrypted root must ship the systemd-cryptsetup opener -- #
#
# Empirically (dracut 111): 71systemd-cryptsetup depends on `crypt`, and its
# check() returns non-zero in hostonly mode unless crypto_LUKS is in
# host_fs_types[]. When dracut runs inside arch-chroot at install the target's
# LUKS root is NOT in that list, so the opener (systemd-cryptsetup-generator +
# binary) is silently omitted and the boot hangs on /dev/mapper/<name>. We FORCE
# the modules so check() is bypassed. The old "crypt competes with systemd"
# comment was wrong for dracut 111 — crypt is a *dependency* of the systemd path.


def test_desired_encrypted_forces_crypt_systemd_and_cryptsetup():
    conf = _b(_cfg(encrypt=True)).desired_value()
    assert 'hostonly="yes"' in conf
    assert "force_add_dracutmodules" in conf
    for mod in ("crypt", "systemd", "systemd-cryptsetup"):
        assert mod in conf, mod


def test_desired_encrypted_disables_hostonly_cmdline():
    # dracut runs from the live ISO; without this it can bake the ISO's cmdline
    # into the image. dasik's bootloader entry is the single source of truth.
    conf = _b(_cfg(encrypt=True)).desired_value()
    assert 'hostonly_cmdline="no"' in conf


def test_desired_includes_btrfs_when_btrfs_root():
    assert "btrfs" in _b(_cfg(fs="btrfs")).desired_value()


def test_desired_forces_btrfs_for_encrypted_btrfs_root():
    # hostonly-from-chroot may not detect the btrfs-on-LUKS root; force it.
    conf = _b(_cfg(encrypt=True, fs="btrfs")).desired_value()
    assert "force_add_dracutmodules" in conf and "btrfs" in conf


def test_desired_empty_when_nothing_to_add():
    assert _b(_cfg()).desired_value() == ""


def test_desired_is_deterministic_and_has_no_duplicate_modules():
    b = _b(_cfg(encrypt=True, fs="btrfs"))
    assert b.desired_value() == b.desired_value()
    conf = b.desired_value()
    # each forced module appears exactly once
    import re
    m = re.search(r'force_add_dracutmodules\+="\s*(.*?)\s*"', conf)
    assert m, conf
    mods = m.group(1).split()
    assert len(mods) == len(set(mods)), mods


def test_actual_value_reads_conf():
    with patch("builtins.open", mock_open(read_data="add_dracutmodules+=\" crypt \"\n")):
        assert _b(_cfg(encrypt=True)).actual_value() == "add_dracutmodules+=\" crypt \"\n"


def test_actual_value_none_when_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b(_cfg(encrypt=True)).actual_value() is None


def test_apply_writes_conf_and_regenerates_with_fstab():
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=True), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    assert m.call_args_list[0].args[0] == "/etc/dracut.conf.d/dasik.conf"
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "systemd" in body
    assert run.call_args.args[0] == "dracut"
    assert run.call_args.args[1] == ["--regenerate-all", "--force", "--fstab"]
    assert run.call_args.kwargs["target"].root == "/"
    assert run.call_args.kwargs.get("check") is True


def test_apply_aborts_when_no_fstab_in_target():
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=False), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        import pytest
        with pytest.raises(CommandExecutionError):
            a.apply()
    run.assert_not_called()   # never regenerate an initramfs from a bad target


# --- FIDO2 / TPM2 / bluetooth-in-initramfs (mirrors a real dracut setup) --- #

def _cfg_unlock(*, fido2=False, tpm2=False, bt_initramfs=False, encrypt=True, fs="btrfs",
                subvol_root=False):
    if subvol_root:
        part = {"mountpoint": None, "filesystem": fs,
                "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"},
                                     {"name": "@home", "mountpoint": "/home"}]}
    else:
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
    assert "add_dracutmodules" in conf


def test_hostonly_set_for_any_encryption():
    assert 'hostonly="yes"' in _b(_cfg_unlock(encrypt=True, fido2=False)).desired_value()


def test_no_hostonly_without_encryption():
    conf = _b(_cfg_unlock(encrypt=False, fido2=False, tpm2=False, fs="btrfs")).desired_value()
    assert "hostonly" not in conf


def test_combined_fido2_bluetooth_btrfs():
    conf = _b(_cfg_unlock(fido2=True, bt_initramfs=True, fs="btrfs")).desired_value()
    assert 'hostonly="yes"' in conf
    for tok in ("crypt", "systemd", "systemd-cryptsetup", "fido2", "bluetooth"):
        assert tok in conf, tok


def test_encryption_alone_forces_the_cryptsetup_stack():
    conf = _b(_cfg_unlock(fido2=False, tpm2=False, encrypt=True)).desired_value()
    assert "force_add_dracutmodules" in conf
    for mod in ("crypt", "systemd", "systemd-cryptsetup"):
        assert mod in conf, mod


# --- crypttab: root gets x-initrd.attach, data disks do not ----------------- #

def test_crypttab_root_entry_has_x_initrd_attach():
    ct = _b(_cfg_unlock(encrypt=True)).crypttab()
    assert f"cryptroot UUID={luks_uuid('cryptroot')} none luks,x-initrd.attach" in ct


def test_crypttab_subvol_mounted_root_gets_x_initrd_attach():
    # the exact synced shape: mountpoint=null, / on the @ subvolume.
    ct = _b(_cfg_unlock(encrypt=True, subvol_root=True)).crypttab()
    assert "x-initrd.attach" in ct
    assert f"cryptroot UUID={luks_uuid('cryptroot')}" in ct


def test_crypttab_non_root_encrypted_data_disk_has_no_x_initrd_attach():
    # a second encrypted partition that is NOT the root must not force initrd attach.
    part_root = {"mountpoint": "/", "filesystem": "btrfs",
                 "encrypt": True, "luks_name": "cryptroot"}
    part_data = {"mountpoint": "/data", "filesystem": "ext4",
                 "encrypt": True, "luks_name": "cryptdata"}
    cfg = {"disks": {"disks": [{"partitions": [part_root, part_data]}]}}
    ct = _b(cfg).crypttab()
    data_line = [l for l in ct.splitlines() if l.startswith("cryptdata")][0]
    assert "x-initrd.attach" not in data_line
    root_line = [l for l in ct.splitlines() if l.startswith("cryptroot")][0]
    assert "x-initrd.attach" in root_line


def test_no_crypttab_without_encryption():
    assert _b(_cfg_unlock(encrypt=False)).crypttab() == ""


def test_subvol_root_detects_btrfs_and_forces_it():
    # end-to-end shape: subvol-mounted encrypted btrfs root forces the whole stack.
    b = _b(_cfg_unlock(encrypt=True, subvol_root=True, fs="btrfs"))
    assert b.root_fs == "btrfs"
    conf = b.desired_value()
    for mod in ("crypt", "systemd", "systemd-cryptsetup", "btrfs"):
        assert mod in conf, mod
