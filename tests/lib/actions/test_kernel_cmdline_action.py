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


# ---------------------------------------------------------------------- #
#  v3 contract (Plan 11)                                                  #
# ---------------------------------------------------------------------- #
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def _grub_action(cfg, current_cmdline):
    a = KernelCmdlineAction(cfg, _ctx("/"))
    a.actual = lambda: set(current_cmdline.split())
    return a


def test_desired_tokens_flattens_and_merges():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet", "loglevel=3"]}, _ctx("/"))
    toks = a._desired_tokens()
    assert "quiet" in toks and "loglevel=3" in toks


def test_is_v3_true():
    assert KernelCmdlineAction.is_v3() is True


def test_plan_installs_missing_explicit():
    a = _grub_action({"kernel_cmdline": ["mitigations=off"]}, "quiet")
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "mitigations=off")]


def test_plan_removes_owned_not_declared():
    a = _grub_action({"kernel_cmdline": []}, "quiet oldparam")
    changes = a.plan(managed=["oldparam"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "oldparam")]


def test_plan_empty_when_converged():
    a = _grub_action({"kernel_cmdline": ["quiet"]}, "quiet other")
    assert a.plan(managed=["quiet"]) == []


def test_managed_keys_lists_desired_tokens():
    a = KernelCmdlineAction({"kernel_cmdline": ["quiet"]}, _ctx("/"))
    assert a.managed_keys() == {"kernel_cmdline": ["quiet"]}


def test_apply_grub_rewrites_line_and_regens():
    a = KernelCmdlineAction({"bootloader": "grub"}, _ctx("/"))
    a._current_cmdline = lambda: "quiet old"
    grub_text = 'GRUB_CMDLINE_LINUX="quiet old"\n'
    changes = [Change("kernel_cmdline", Op.INSTALL, "new=1"),
               Change("kernel_cmdline", Op.REMOVE, "old")]
    with patch("builtins.open", mock_open(read_data=grub_text)) as m, \
         patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run:
        a.apply(changes)
    body = "".join(c.args[0] for c in m().write.call_args_list)
    assert 'GRUB_CMDLINE_LINUX="quiet new=1"' in body
    assert (run.call_args.args[0], run.call_args.args[1]) == (
        "grub-mkconfig", ["-o", "/boot/grub/grub.cfg"])


def test_apply_noop_without_target():
    a = KernelCmdlineAction({"bootloader": "grub"}, None)
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run, \
         patch("builtins.open") as op:
        a.apply([Change("kernel_cmdline", Op.INSTALL, "x")])
    run.assert_not_called()
    op.assert_not_called()


def test_apply_empty_changes_noop():
    a = KernelCmdlineAction({"bootloader": "grub"}, _ctx("/"))
    with patch("dasik.lib.actions.kernel_cmdline_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()
