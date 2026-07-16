from unittest.mock import patch

from dasik.lib.actions.bootloader_action import BootloaderAction
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
    _mark_sdboot(tmp_path)
    a = BootloaderAction(_cfg("sd-boot"), _ctx(tmp_path))
    assert a.managed_keys() == {"bootloader": ["sd-boot"]}


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
