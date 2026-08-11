from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _cfg(bootloader="sd-boot", root_label="root"):
    return {
        "bootloader": bootloader,
        "enable_microcode": False,
        "disks": {"disks": [{
            "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
            "partitions": [
                {"label": "boot", "size": "512MiB", "filesystem": "fat32",
                 "partition_type": "esp", "mountpoint": "/boot", "format": True},
                {"label": root_label, "size": "rest", "filesystem": "ext4",
                 "partition_type": "linux", "mountpoint": "/", "format": True},
            ],
        }]},
    }


def _mark_sdboot(tmp_path):
    d = tmp_path / "boot" / "EFI" / "systemd"
    d.mkdir(parents=True, exist_ok=True)
    (d / "systemd-bootx64.efi").write_text("")


def _mark_grub(tmp_path):
    d = tmp_path / "boot" / "grub"
    d.mkdir(parents=True, exist_ok=True)
    (d / "grub.cfg").write_text("")


def test_is_v3_true():
    assert BootloaderAction.is_v3() is True


def test_root_label_from_disks():
    a = BootloaderAction(_cfg(root_label="myroot"))
    assert a._root_label() == "myroot"


def test_root_label_default_when_no_disks():
    a = BootloaderAction({"bootloader": "sd-boot"})
    assert a._root_label() == "root"


def test_actual_sdboot_absent(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_sdboot_present(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.actual() == {"sd-boot"}


def test_actual_grub_present(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    assert a.actual() == {"grub"}


def test_plan_install_when_absent(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "sd-boot"


def test_plan_empty_when_present(tmp_path):
    _mark_sdboot(tmp_path)
    # Converged means BOTH entries: the rescue entry is a domain item of its
    # own (see test_bootloader_fallback_entry.py), so sd-boot alone still has
    # work left to do.
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "arch-fallback.conf").write_text("title Arch Linux (fallback initramfs)\n")
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch.object(BootloaderAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch.object(BootloaderAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_managed_keys(tmp_path):
    # Ownership is what dasik INTENDS, not what it happens to find: sd-boot
    # always brings its rescue entry, so both items are owned whether or not
    # the entry is on the ESP yet.
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.managed_keys() == {"bootloader": ["fallback-entry", "sd-boot"]}


def test_import_state_empty(tmp_path):
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = BootloaderAction(_cfg())
    assert a.name == "Bootloader"
    assert a.is_optional is False


# --- import_state (sync capture) ----------------------------------------- #

def test_import_state_detects_sdboot(tmp_path):
    _mark_sdboot(tmp_path)
    # seed says grub, but the installed marker is systemd-boot -> capture sd-boot
    a = BootloaderAction({"bootloader": "grub"}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {"bootloader": "sd-boot"}


def test_import_state_detects_grub(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction({"bootloader": "sd-boot"}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {"bootloader": "grub"}


def test_import_state_empty_when_no_bootloader(tmp_path):
    a = BootloaderAction({"bootloader": "grub"}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


# --- microcode initrd: only list the image that actually exists ----------- #

def _mark_ucode(tmp_path, name):
    b = tmp_path / "boot"
    b.mkdir(parents=True, exist_ok=True)
    (b / name).write_text("")


def test_ucode_initrds_only_lists_installed_image(tmp_path):
    # AMD host: only /boot/amd-ucode.img exists. The boot entry must NOT list
    # /intel-ucode.img — systemd-boot errors "preparing initrd: Not found" on a
    # missing initrd.
    _mark_ucode(tmp_path, "amd-ucode.img")
    a = BootloaderAction({"bootloader": "sd-boot", "enable_microcode": True}, _ctx(tmp_path))
    assert a._ucode_initrds() == ["/amd-ucode.img"]


def test_ucode_initrds_intel_only(tmp_path):
    _mark_ucode(tmp_path, "intel-ucode.img")
    a = BootloaderAction({"bootloader": "sd-boot", "enable_microcode": True}, _ctx(tmp_path))
    assert a._ucode_initrds() == ["/intel-ucode.img"]


def test_ucode_initrds_none_when_disabled(tmp_path):
    _mark_ucode(tmp_path, "amd-ucode.img")
    a = BootloaderAction({"bootloader": "sd-boot", "enable_microcode": False}, _ctx(tmp_path))
    assert a._ucode_initrds() == []


def test_ucode_initrds_empty_when_no_image_present(tmp_path):
    # microcode enabled but no ucode img on the ESP -> list nothing (don't
    # reference a file that isn't there).
    a = BootloaderAction({"bootloader": "sd-boot", "enable_microcode": True}, _ctx(tmp_path))
    assert a._ucode_initrds() == []


# --- subvol-mounted root (partition mountpoint null) ----------------------- #

def _subvol_root_cfg():
    return {"bootloader": "sd-boot", "disks": {"disks": [{
        "device": "/dev/vda", "partitions": [
            {"label": "esp", "mountpoint": "/boot", "filesystem": "fat32"},
            {"label": "root", "filesystem": "btrfs", "encrypt": True,
             "luks_name": "cryptroot",
             "btrfs_subvolumes": [{"name": "@", "mountpoint": "/"}]}]}]}}


def test_root_param_finds_subvol_mounted_encrypted_root():
    a = BootloaderAction(_subvol_root_cfg())
    assert a._root_param() == "root=/dev/mapper/cryptroot"


def test_root_label_finds_subvol_mounted_root():
    a = BootloaderAction(_subvol_root_cfg())
    assert a._root_label() == "root"


# --- mutating boot commands must fail loud (F-18) -------------------------- #

def test_sdboot_install_aborts_before_writing_loader_when_bootctl_fails(tmp_path):
    """A failed `bootctl install` must not be followed by loader.conf/arch.conf:
    those files make the action look applied on an ESP with no bootloader."""
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = BootloaderAction(_cfg(), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute",
               side_effect=CommandExecutionError("bootctl failed")):
        with pytest.raises(CommandExecutionError):
            a._install()
    assert not (tmp_path / "boot" / "loader" / "loader.conf").exists()
    assert not (tmp_path / "boot" / "loader" / "entries" / "arch.conf").exists()


def test_sdboot_install_uses_check_true(tmp_path):
    a = BootloaderAction(_cfg(), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute") as run:
        a._install()
    assert run.call_args_list[0].args[0] == "bootctl"
    assert run.call_args_list[0].kwargs.get("check") is True


def test_grub_install_uses_check_true(tmp_path):
    a = BootloaderAction(_cfg(bootloader="grub"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute") as run:
        a._install()
    cmds = [c.args[0] for c in run.call_args_list]
    assert cmds == ["pacman", "grub-install", "grub-mkconfig"]
    assert all(c.kwargs.get("check") is True for c in run.call_args_list)


# --- switching bootloader: the stale one must go -------------------------- #
#
# actual() used to probe only the marker of the CONFIGURED loader, so a leftover
# GRUB on a machine declaring sd-boot (or the reverse) was invisible: no REMOVE
# was ever planned and both loaders survived on the ESP and in NVRAM.

def _entries(tmp_path, *names):
    d = tmp_path / "boot/loader/entries"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("title x\n")
    return d


def _ops(action, managed=()):
    return {(c.op.name, c.item) for c in action.plan(managed=list(managed))}


def test_actual_reports_both_loaders_when_both_markers_exist(tmp_path):
    _mark_sdboot(tmp_path)
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert {"sd-boot", "grub"} <= a.actual()


def test_plan_removes_grub_when_switching_to_sdboot(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert ("REMOVE", "grub") in _ops(a)
    assert ("INSTALL", "sd-boot") in _ops(a)


def test_plan_removes_sdboot_and_its_rescue_entry_when_switching_to_grub(tmp_path):
    _mark_sdboot(tmp_path)
    _entries(tmp_path, "arch.conf", "arch-fallback.conf")
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    assert _ops(a) == {("INSTALL", "grub"), ("REMOVE", "sd-boot"),
                       ("REMOVE", "fallback-entry")}


def test_plan_removes_the_stale_loader_even_when_unowned(tmp_path):
    """Two loaders on one ESP is not a state anyone wants, so the removal does
    not wait for manifest ownership — after a `sync` the manifest is empty and
    an ownership-gated removal would never fire."""
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert ("REMOVE", "grub") in _ops(a, managed=[])


def test_plan_removes_nothing_when_only_the_declared_loader_is_installed(tmp_path):
    _mark_sdboot(tmp_path)
    _entries(tmp_path, "arch-fallback.conf")
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert _ops(a) == set()


def test_the_systemd_boot_alias_is_not_a_switch(tmp_path):
    """`systemd-boot` is an accepted alias of `sd-boot`; reading it as a
    different loader would plan a removal of the very loader being kept."""
    _mark_sdboot(tmp_path)
    _entries(tmp_path, "arch-fallback.conf")
    a = BootloaderAction(_cfg("systemd-boot"), _ctx(tmp_path))
    assert _ops(a) == set()


def test_removing_a_bootloader_is_flagged_destructive(tmp_path):
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    removal = next(c for c in a.plan(managed=[]) if c.op is Op.REMOVE)
    assert removal.destructive is True


def test_managed_keys_is_the_desired_set_not_what_is_installed(tmp_path):
    """Ownership is intent, like every other domain: a stale loader on the
    machine must never be recorded as something dasik wants."""
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    assert a.managed_keys() == {"bootloader": ["grub"]}


# --- apply: uninstall, then install --------------------------------------- #

def test_apply_uninstalls_the_stale_loader_before_installing(tmp_path):
    """Installing first would leave two loaders fighting over the ESP mid-apply."""
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    order = []
    with patch.object(BootloaderAction, "_uninstall",
                      side_effect=lambda loader: order.append(f"uninstall:{loader}")), \
         patch.object(BootloaderAction, "_install",
                      side_effect=lambda: order.append("install")):
        a.apply(a.plan(managed=[]))
    assert order == ["uninstall:grub", "install"]


def test_apply_does_not_uninstall_when_nothing_is_stale(tmp_path):
    _mark_sdboot(tmp_path)
    _entries(tmp_path, "arch-fallback.conf")
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    with patch.object(BootloaderAction, "_uninstall") as un:
        a.apply(a.plan(managed=[]))
    un.assert_not_called()


def test_uninstall_grub_removes_its_files(tmp_path):
    _mark_grub(tmp_path)
    efi_grub = tmp_path / "boot/EFI/GRUB"
    efi_grub.mkdir(parents=True)
    (efi_grub / "grubx64.efi").write_text("")
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute") as run:
        run.return_value = MagicMock(returncode=0, stdout=b"")
        a._uninstall("grub")
    assert not (tmp_path / "boot/grub").exists()
    assert not efi_grub.exists()


_EFIBOOTMGR_OUT = (
    b"BootCurrent: 0001\nTimeout: 1 seconds\nBootOrder: 0001,0003,0000\n"
    b"Boot0000* Windows Boot Manager\tHD(1,GPT,...)\n"
    b"Boot0001* Linux Boot Manager\tHD(1,GPT,...)\n"
    b"Boot0003* GRUB\tHD(1,GPT,...)\n"
)


def test_uninstall_grub_deletes_its_nvram_entry(tmp_path):
    """A dead boot entry can still be picked from the firmware menu."""
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute") as run:
        run.return_value = MagicMock(returncode=0, stdout=_EFIBOOTMGR_OUT)
        a._uninstall("grub")
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert ("efibootmgr", ["-b", "0003", "-B"]) in calls
    assert not any(args[:2] == ["-b", "0001"] for cmd, args in calls
                   if cmd == "efibootmgr")     # never the OTHER loader's entry


def test_uninstall_grub_survives_a_firmware_without_efivars(tmp_path):
    """No efivars in a container/VM chroot must not abort an otherwise-good
    install — the files are gone either way."""
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute",
               side_effect=CommandExecutionError("EFI variables are not supported")), \
         patch("dasik.lib.actions.bootloader_action.run_logger.get", MagicMock()):
        a._uninstall("grub")
    assert not (tmp_path / "boot/grub").exists()


def test_uninstall_sdboot_calls_bootctl_remove_and_clears_the_loader_dir(tmp_path):
    _mark_sdboot(tmp_path)
    _entries(tmp_path, "arch.conf", "arch-fallback.conf")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/random-seed").write_text("x")
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute") as run:
        run.return_value = MagicMock(returncode=0, stdout=b"")
        a._uninstall("sd-boot")
    assert ("bootctl", ["remove"]) in [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert not (tmp_path / "boot/loader/entries").exists()
    assert not (tmp_path / "boot/loader/loader.conf").exists()
    assert not (tmp_path / "boot/loader/random-seed").exists()
    assert not (tmp_path / "boot/EFI/systemd").exists()


def test_uninstall_sdboot_survives_a_failing_bootctl(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("grub"), _ctx(tmp_path))
    with patch("dasik.lib.actions.bootloader_action.Command.execute",
               side_effect=CommandExecutionError("Failed to access EFI variables")), \
         patch("dasik.lib.actions.bootloader_action.run_logger.get", MagicMock()):
        a._uninstall("sd-boot")
    assert not (tmp_path / "boot/EFI/systemd").exists()


def test_verify_is_false_while_a_stale_loader_remains(tmp_path):
    _mark_sdboot(tmp_path)
    _mark_grub(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.verify() is False


def test_verify_is_true_once_only_the_declared_loader_is_left(tmp_path):
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.verify() is True
