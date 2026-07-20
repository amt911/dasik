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
    # non-encrypted: no crypttab, so actual_value is a plain dasik.conf read.
    # No kernel in the target yet -> the image check has nothing to verify.
    b = _b(_cfg(fs="btrfs"))
    with patch("builtins.open", mock_open(read_data="add_dracutmodules+=\" btrfs \"\n")), \
         patch.object(type(b), "_target_kernels", lambda self: []):
        assert b.actual_value() == "add_dracutmodules+=\" btrfs \"\n"


def test_actual_value_none_when_absent():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _b(_cfg(encrypt=True)).actual_value() is None


def test_apply_regenerates_named_image_for_each_target_kernel():
    # dracut --regenerate-all names images by kver (initramfs-<kver>.img), but the
    # bootloader entry loads /initramfs-<pkgbase>.img. We must write THAT name,
    # using the TARGET's kver (not the chroot host's uname -r), or the boot loads
    # the stale mkinitcpio image and the encrypted root hangs.
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=True), \
         patch.object(type(a), "_target_kernels",
                      return_value=[("6.12.1-arch1-1", "linux")]), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert "systemd" in body
    assert run.call_args.args[0] == "dracut"
    assert run.call_args.args[1] == [
        "--force", "--fstab", "/boot/initramfs-linux.img", "6.12.1-arch1-1"]
    assert run.call_args.kwargs["target"].root == "/"
    assert run.call_args.kwargs.get("check") is True


def test_apply_regenerates_for_every_kernel():
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=True), \
         patch.object(type(a), "_target_kernels",
                      return_value=[("6.12-arch1", "linux"), ("6.6-lts", "linux-lts")]), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        a.apply()
    outs = [c.args[1][2] for c in run.call_args_list if c.args[0] == "dracut"]
    assert "/boot/initramfs-linux.img" in outs
    assert "/boot/initramfs-linux-lts.img" in outs


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


def test_apply_aborts_when_no_target_kernel_found():
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = _b(_cfg(encrypt=True), root="/")
    m = mock_open()
    with patch("builtins.open", m), \
         patch("dasik.lib.actions.initramfs.dracut.os.makedirs"), \
         patch("dasik.lib.actions.initramfs.dracut.os.path.exists", return_value=True), \
         patch.object(type(a), "_target_kernels", return_value=[]), \
         patch("dasik.lib.actions.initramfs.dracut.Command.execute") as run:
        import pytest
        with pytest.raises(CommandExecutionError):
            a.apply()
    run.assert_not_called()


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


# --- crypttab single-owner: dracut composes root (derived) + non-root (captured) --

_SWAP_LINE = "swap LABEL=cryptswap /dev/urandom swap,cipher=aes-xts-plain64,size=512"


def _cfg_with_captured_crypttab(captured, encrypt=True):
    cfg = _cfg_unlock(encrypt=encrypt)
    cfg["initramfs"] = "dracut"
    cfg["files"] = [{"path": "/etc/crypttab", "content": captured}]
    return cfg


def test_crypttab_composes_derived_root_and_captured_swap():
    captured = (
        "# Configuration for encrypted block devices.\n"
        "# NOTE: Do not list your root here.\n"
        f"{_SWAP_LINE}\n"
    )
    ct = _b(_cfg_with_captured_crypttab(captured)).crypttab()
    assert f"cryptroot UUID={luks_uuid('cryptroot')} none luks,x-initrd.attach" in ct
    assert "cryptswap" in ct                     # swap preserved
    assert ct.count("cryptroot") == 1            # root exactly once


def test_crypttab_derived_root_replaces_stale_captured_root_line():
    captured = (
        "cryptroot UUID=00000000-0000-0000-0000-000000000000 none luks\n"
        f"{_SWAP_LINE}\n"
    )
    ct = _b(_cfg_with_captured_crypttab(captured)).crypttab()
    # the stale UUID is gone; the derived one (with x-initrd.attach) wins, once
    assert "00000000-0000-0000-0000-000000000000" not in ct
    assert ct.count("cryptroot") == 1
    assert f"cryptroot UUID={luks_uuid('cryptroot')} none luks,x-initrd.attach" in ct
    assert "cryptswap" in ct


def test_crypttab_drops_comments_and_blank_lines_from_captured():
    captured = "# just a comment\n\n   \n" + _SWAP_LINE + "\n"
    ct = _b(_cfg_with_captured_crypttab(captured)).crypttab()
    assert "# just a comment" not in ct.split("# Managed by dasik")[-1]
    assert "cryptswap" in ct


# --- InitramfsAction drift: a crypttab change must trigger regeneration ---- #

def test_actual_value_returns_none_when_crypttab_drifts():
    b = _b(_cfg_unlock(encrypt=True), root="/")
    desired_conf = b.desired_value()

    def fake_open(path, *a, **k):
        from unittest.mock import mock_open
        if path.endswith("dasik.conf"):
            return mock_open(read_data=desired_conf)()
        # crypttab on disk differs from what the backend would compose
        return mock_open(read_data="cryptroot UUID=stale none luks\n")()

    with patch("builtins.open", side_effect=fake_open):
        assert b.actual_value() is None   # drift -> force MODIFY/regen


def test_target_kernels_reads_pkgbase_from_modules(tmp_path):
    # /usr/lib/modules/<kver>/pkgbase carries the image basename (Arch convention)
    mods = tmp_path / "usr/lib/modules/6.12.1-arch1-1"
    mods.mkdir(parents=True)
    (mods / "pkgbase").write_text("linux\n")
    b = _b(_cfg(encrypt=True), root=str(tmp_path))
    assert b._target_kernels() == [("6.12.1-arch1-1", "linux")]


def test_target_kernels_skips_dirs_without_pkgbase(tmp_path):
    (tmp_path / "usr/lib/modules/orphan").mkdir(parents=True)
    good = tmp_path / "usr/lib/modules/6.12-arch1"
    good.mkdir(parents=True)
    (good / "pkgbase").write_text("linux")
    b = _b(_cfg(encrypt=True), root=str(tmp_path))
    assert b._target_kernels() == [("6.12-arch1", "linux")]


def test_actual_value_returns_conf_when_crypttab_matches():
    b = _b(_cfg_unlock(encrypt=True), root="/")
    desired_conf = b.desired_value()
    desired_ct = b.crypttab()

    def fake_open(path, *a, **k):
        from unittest.mock import mock_open
        if path.endswith("dasik.conf"):
            return mock_open(read_data=desired_conf)()
        return mock_open(read_data=desired_ct)()

    with patch("builtins.open", side_effect=fake_open), \
         patch.object(type(b), "_target_kernels", lambda self: []):
        assert b.actual_value() == desired_conf


def test_subvol_root_detects_btrfs_and_forces_it():
    # end-to-end shape: subvol-mounted encrypted btrfs root forces the whole stack.
    b = _b(_cfg_unlock(encrypt=True, subvol_root=True, fs="btrfs"))
    assert b.root_fs == "btrfs"
    conf = b.desired_value()
    for mod in ("crypt", "systemd", "systemd-cryptsetup", "btrfs"):
        assert mod in conf, mod


# --- convergence must include the produced image (F-09) -------------------- #
#
# actual_value() used to read only dasik.conf/crypttab — pure intent. If dracut
# failed AFTER those files were written, the next plan saw the backend satisfied
# and the target kept booting a stale (or absent) initramfs.

import os


def _target_tree(tmp_path, *, kver="6.9.1-arch1-1", pkgbase="linux", image=True):
    (tmp_path / "etc" / "dracut.conf.d").mkdir(parents=True)
    (tmp_path / "boot").mkdir()
    mods = tmp_path / "usr" / "lib" / "modules" / kver
    mods.mkdir(parents=True)
    (mods / "pkgbase").write_text(pkgbase + "\n")
    if image:
        (tmp_path / "boot" / f"initramfs-{pkgbase}.img").write_text("IMG")
    return tmp_path


def _converged_backend(tmp_path, cfg=None):
    """Backend whose conf is on disk and whose image is newer than it — the
    state a successful dracut run leaves behind."""
    b = _b(cfg or _cfg(fs="btrfs"), root=str(tmp_path))
    (tmp_path / "etc" / "dracut.conf.d" / "dasik.conf").write_text(b.desired_value())
    for img in (tmp_path / "boot").glob("initramfs-*.img"):
        os.utime(img, None)
    return b


def test_actual_value_none_when_image_missing(tmp_path):
    _target_tree(tmp_path, image=False)
    b = _converged_backend(tmp_path)
    assert b.actual_value() is None


def test_actual_value_returns_conf_when_image_present(tmp_path):
    _target_tree(tmp_path)
    b = _converged_backend(tmp_path)
    assert b.actual_value() == b.desired_value()


def test_actual_value_none_when_image_older_than_config(tmp_path):
    """dracut wrote the conf, then failed: the image on disk predates it."""
    _target_tree(tmp_path)
    b = _converged_backend(tmp_path)
    conf = tmp_path / "etc" / "dracut.conf.d" / "dasik.conf"
    img = tmp_path / "boot" / "initramfs-linux.img"
    os.utime(img, (1000, 1000))
    os.utime(conf, (2000, 2000))
    assert b.actual_value() is None


def test_actual_value_none_when_a_second_kernel_has_no_image(tmp_path):
    _target_tree(tmp_path)
    mods = tmp_path / "usr" / "lib" / "modules" / "6.9.1-lts"
    mods.mkdir(parents=True)
    (mods / "pkgbase").write_text("linux-lts\n")
    b = _converged_backend(tmp_path)
    assert b.actual_value() is None
