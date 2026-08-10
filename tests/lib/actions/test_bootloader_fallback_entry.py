"""The sd-boot rescue entry (arch-fallback.conf).

The old imperative installer always shipped a second loader entry and kept
every kernel parameter on both. dasik wrote only arch.conf, so a broken entry
or a bad hostonly initramfs left nothing to boot from.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _sdboot_cfg():
    return {"bootloader": "sd-boot", "enable_microcode": False,
            "disks": {"disks": [{"device": "/dev/vda", "partitions": [
                {"label": "root", "mountpoint": "/"}]}]}}


def _mark_installed(root):
    esp = root / "boot/EFI/systemd"
    esp.mkdir(parents=True)
    (esp / "systemd-bootx64.efi").write_text("stub")


def test_plans_the_fallback_entry_on_an_already_installed_sdboot(tmp_path):
    _mark_installed(tmp_path)
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))
    assert [c.item for c in action.plan(managed=[])] == ["fallback-entry"]


def test_no_fallback_planned_for_grub(tmp_path):
    grub = tmp_path / "boot/grub"
    grub.mkdir(parents=True)
    (grub / "grub.cfg").write_text("stub")
    action = BootloaderAction({"bootloader": "grub"}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []


def test_writes_the_fallback_entry_using_the_fallback_image_when_present(tmp_path):
    _mark_installed(tmp_path)
    (tmp_path / "boot/initramfs-linux-fallback.img").write_text("img")
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert "title Arch Linux (fallback initramfs)" in entry
    assert "initrd /initramfs-linux-fallback.img" in entry
    assert "options root=LABEL=root rw" in entry


def test_falls_back_to_the_main_image_when_dracut_built_no_fallback(tmp_path):
    _mark_installed(tmp_path)
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert "initrd /initramfs-linux.img" in entry
    assert "fallback.img" not in entry


def test_existing_fallback_entry_is_not_rewritten(tmp_path):
    _mark_installed(tmp_path)
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch-fallback.conf").write_text("hand-edited\n")
    action = BootloaderAction(_sdboot_cfg(), _ctx(tmp_path))

    assert action.plan(managed=[]) == []                       # idempotency
    assert (entries / "arch-fallback.conf").read_text() == "hand-edited\n"


def test_microcode_initrds_are_repeated_on_the_fallback_entry(tmp_path):
    _mark_installed(tmp_path)
    (tmp_path / "boot/amd-ucode.img").write_text("img")
    cfg = dict(_sdboot_cfg(), enable_microcode=True)
    action = BootloaderAction(cfg, _ctx(tmp_path))

    action.apply(action.plan(managed=[]))

    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert entry.index("initrd /amd-ucode.img") < entry.index("initrd /initramfs-linux.img")
