from unittest.mock import MagicMock, mock_open, patch

from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction


def _enc_cfg():
    return {"disks": {"disks": [{"partitions": [
        {"mountpoint": "/", "encrypt": True, "luks_name": "croot", "filesystem": "ext4"}]}]}}


def _fake_exec(mapping):
    """mapping: (cmd, args[0]) -> stdout bytes. Matches on first arg."""
    def run(cmd, args, *a, **k):
        key = (cmd, args[0] if args else "")
        return MagicMock(stdout=mapping.get(key, b""), returncode=0)
    return run


def test_luks_backing_device_parses_status():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"/dev/mapper/croot is active.\n  type:    LUKS2\n  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status})):
        assert a._luks_backing_device("croot") == "/dev/sda2"


def test_luks_backing_device_none_on_failure():
    a = KernelCmdlineAction(_enc_cfg())
    fail = MagicMock(return_value=MagicMock(stdout=b"", returncode=4))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute", fail):
        assert a._luks_backing_device("croot") is None


def test_resolve_luks_uuid_via_blkid():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status,
                           ("blkid", "-s"): b"DEAD-BEEF\n"})):
        assert a._resolve_luks_uuid("croot") == "DEAD-BEEF"


def test_derive_encryption_resolves_real_uuid():
    a = KernelCmdlineAction(_enc_cfg())
    status = b"  device:  /dev/sda2\n"
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute",
               _fake_exec({("cryptsetup", "status"): status,
                           ("blkid", "-s"): b"U1\n"})):
        derived = a._derive_from_disks()
    assert "rd.luks.name=U1=croot" in derived
    assert "root=/dev/mapper/croot rw" in derived


def test_derive_omits_luks_param_when_unresolved():
    a = KernelCmdlineAction(_enc_cfg())
    fail = MagicMock(return_value=MagicMock(stdout=b"", returncode=4))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute", fail):
        derived = a._derive_from_disks()
    assert not any(d.startswith("rd.luks.name=") for d in derived)
    assert "root=/dev/mapper/croot rw" in derived


def test_derive_btrfs_rootflags():
    cfg = {"disks": {"disks": [{"partitions": [{
        "mountpoint": "/", "filesystem": "btrfs",
        "btrfs_subvolumes": [{"mountpoint": "/", "name": "@", "mount_options": ["noatime"]}],
    }]}]}}
    a = KernelCmdlineAction(cfg)
    joined = " ".join(a._derive_from_disks())
    assert "rootflags=noatime,subvol=@" in joined


def test_btrfs_rootflags_default_subvol_and_options():
    cfg = {"disks": {"disks": [{"partitions": [{
        "mountpoint": "/", "filesystem": "btrfs", "btrfs_subvolumes": []}]}]}}
    a = KernelCmdlineAction(cfg)
    assert any("subvol=@" in p and "compress-force=zstd" in p for p in a._derive_from_disks())


def test_merge_explicit_wins_on_key_conflict():
    auto = ["root=/dev/mapper/x rw", "quiet"]
    explicit = ["root=/dev/sda2"]
    assert KernelCmdlineAction._merge(auto, explicit) == ["root=/dev/sda2", "quiet"]


def test_param_present_key_value_and_flag():
    a = KernelCmdlineAction({})
    assert a._param_present("root=/dev/sda2 quiet", "root=/dev/sdb") is True  # key match
    assert a._param_present("quiet splash", "splash") is True
    assert a._param_present("quiet", "splash") is False


def test_current_params_grub_reads_cmdline():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet"]})
    grub = 'GRUB_CMDLINE_LINUX="loglevel=3 quiet"\n'
    with patch("dasik.lib.actions.kernel_cmdline_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=grub)):
        assert a._current_params_grub() == "loglevel=3 quiet"


def test_is_needed_false_when_no_desired_params():
    a = KernelCmdlineAction({})
    assert a.desired_params == []
    assert a.is_needed() is False


def test_is_needed_true_when_param_missing_grub():
    a = KernelCmdlineAction({"bootloader": "grub", "kernel_cmdline": ["mitigations=off"]})
    with patch("dasik.lib.actions.kernel_cmdline_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data='GRUB_CMDLINE_LINUX="quiet"\n')):
        assert a.is_needed() is True


def test_not_needed_when_param_present_grub():
    a = KernelCmdlineAction({"bootloader": "grub", "kernel_cmdline": ["quiet"]})
    with patch("dasik.lib.actions.kernel_cmdline_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data='GRUB_CMDLINE_LINUX="quiet"\n')):
        assert a.is_needed() is False
        assert a.verify() is True


def test_sdboot_entries_lists_conf_files():
    a = KernelCmdlineAction({"bootloader": "systemd-boot"})
    with patch("dasik.lib.actions.kernel_cmdline_action.os.path.isdir", return_value=True), \
         patch("dasik.lib.actions.kernel_cmdline_action.os.listdir",
               return_value=["arch.conf", "readme.txt"]):
        entries = a._sdboot_entries()
    assert entries == ["/mnt/boot/loader/entries/arch.conf"]


def test_name_and_optional():
    a = KernelCmdlineAction({})
    assert a.name == "Kernel Command Line"
    assert a.is_optional is True
