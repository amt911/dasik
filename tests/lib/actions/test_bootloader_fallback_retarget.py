"""The rescue entry loads the fallback image dracut now builds.

Under dracut the image name is DECLARED, not discovered: InitramfsAction builds
/initramfs-linux-fallback.img in the same apply, before this action runs, so
deciding from the ESP would plan the entry one run late (plan -> apply -> plan
not silent). An entry that already exists and loads anything else — the main
image, or a months-old mkinitcpio fallback — is retargeted in place: only its
initramfs `initrd` line changes, never the options another domain maintains.
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.bootloader_action import BootloaderAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target

OPTIONS = "options rd.luks.name=u=cryptroot root=/dev/mapper/cryptroot rw quiet"


def _action(root, generator="dracut"):
    cfg = {"bootloader": "sd-boot", "enable_microcode": True, "initramfs": generator,
           "disks": {"disks": [{"device": "/dev/vda", "partitions": [
               {"label": "root", "mountpoint": "/"}]}]}}
    return BootloaderAction(cfg, ActionContext(target=Target(root=str(root))))


def _esp(root, fallback_initrd):
    marker = root / "boot/EFI/systemd"
    marker.mkdir(parents=True)
    (marker / "systemd-bootx64.efi").write_text("stub")
    entries = root / "boot/loader/entries"
    entries.mkdir(parents=True)
    entry = entries / "arch-fallback.conf"
    entry.write_text("title Arch Linux (fallback initramfs)\nlinux /vmlinuz-linux\n"
                     "initrd /amd-ucode.img\n"
                     f"initrd {fallback_initrd}\n{OPTIONS}\n")
    return entry


def test_dracut_entry_loading_the_main_image_is_retargeted(tmp_path):
    entry = _esp(tmp_path, "/initramfs-linux.img")
    action = _action(tmp_path)

    changes = action.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "fallback-entry")]

    action.apply(changes)
    assert entry.read_text() == (
        "title Arch Linux (fallback initramfs)\nlinux /vmlinuz-linux\n"
        "initrd /amd-ucode.img\ninitrd /initramfs-linux-fallback.img\n"
        f"{OPTIONS}\n")
    assert action.plan(managed=[]) == []            # converges


def test_dracut_entry_already_on_the_fallback_image_is_silent(tmp_path):
    _esp(tmp_path, "/initramfs-linux-fallback.img")
    assert _action(tmp_path).plan(managed=[]) == []


def test_a_new_dracut_entry_names_the_fallback_before_it_exists(tmp_path):
    """Fresh install: the image is built by InitramfsAction in this very apply."""
    marker = tmp_path / "boot/EFI/systemd"
    marker.mkdir(parents=True)
    (marker / "systemd-bootx64.efi").write_text("stub")
    action = _action(tmp_path)
    action.apply(action.plan(managed=[]))
    entry = (tmp_path / "boot/loader/entries/arch-fallback.conf").read_text()
    assert "initrd /initramfs-linux-fallback.img\n" in entry


def test_mkinitcpio_entry_is_left_on_the_main_image_without_a_fallback(tmp_path):
    # mkinitcpio decides from the ESP, as before: no fallback image, no retarget.
    _esp(tmp_path, "/initramfs-linux.img")
    assert _action(tmp_path, generator="mkinitcpio").plan(managed=[]) == []


def test_an_entry_without_an_initramfs_line_is_not_touched(tmp_path):
    marker = tmp_path / "boot/EFI/systemd"
    marker.mkdir(parents=True)
    (marker / "systemd-bootx64.efi").write_text("stub")
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True)
    (entries / "arch-fallback.conf").write_text("hand-edited\n")
    assert _action(tmp_path).plan(managed=[]) == []
