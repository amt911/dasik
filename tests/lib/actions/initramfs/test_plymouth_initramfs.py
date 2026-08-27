"""Plymouth has to be INSIDE the image, or the declared splash is a lie.

The hook placement is not cosmetic: on an encrypted machine plymouth must come
after `systemd` (it needs the device manager) and before `sd-encrypt`, or it
never takes over the passphrase prompt and the machine cannot be unlocked at
all (Arch wiki, Plymouth#mkinitcpio).
"""
import os

from dasik.lib.actions.initramfs.base import detect_plymouth
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.target.target import Target

_ENCRYPTED = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot"}]}]}}


def test_detect_plymouth_follows_the_block():
    assert detect_plymouth({}) is False
    assert detect_plymouth({"plymouth": {}}) is True
    assert detect_plymouth({"plymouth": {"theme": "bgrt"}}) is True


def test_mkinitcpio_puts_plymouth_after_udev_when_there_is_no_encryption():
    hooks = MkinitcpioBackend({"plymouth": {}}).desired_value().split()
    assert "plymouth" in hooks
    assert hooks.index("udev") < hooks.index("plymouth")


def test_mkinitcpio_puts_plymouth_after_systemd_and_before_sd_encrypt():
    hooks = MkinitcpioBackend({**_ENCRYPTED, "plymouth": {}}).desired_value().split()
    assert hooks.index("systemd") < hooks.index("plymouth") < hooks.index("sd-encrypt")


def test_mkinitcpio_without_the_block_has_no_plymouth_hook():
    assert "plymouth" not in MkinitcpioBackend({}).desired_value().split()


def test_the_plymouth_hook_is_not_duplicated_on_a_second_pass(tmp_path):
    """The hooks already on disk are the base for the next computation."""
    conf = tmp_path / "etc"
    conf.mkdir()
    (conf / "mkinitcpio.conf").write_text(
        "HOOKS=(base udev plymouth autodetect modconf block filesystems fsck)\n")
    hooks = MkinitcpioBackend({"plymouth": {}}, Target(root=str(tmp_path))).desired_value().split()
    assert hooks.count("plymouth") == 1


def test_dracut_forces_the_plymouth_module():
    """Forced, not merely added: dracut runs under arch-chroot, where its own
    hostonly detection already silently dropped systemd-cryptsetup and resume."""
    conf = DracutBackend({"plymouth": {}}).desired_value()
    assert "force_add_dracutmodules" in conf
    assert "plymouth" in conf


def test_dracut_without_the_block_does_not_mention_plymouth():
    assert "plymouth" not in DracutBackend({}).desired_value()


# --- a theme change must rebuild the image -------------------------------- #

def _dracut_target(tmp_path):
    (tmp_path / "etc/dracut.conf.d").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0/pkgbase").write_text("linux\n")
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/initramfs-linux.img").write_text("image")
    return Target(root=str(tmp_path))


def _backdate_conf_d(tmp_path, when=1):
    """/etc/dracut.conf.d is an input in its own right — a deleted drop-in is
    invisible otherwise — so these tests have to age it along with the files
    they fake a timeline for. It must run AFTER dasik.conf is written: creating
    a file in a directory bumps that directory's mtime back to now, and the
    image (stamped in 1970 here) would always look stale, so the theme — the
    thing actually under test — would never get to decide anything."""
    os.utime(tmp_path / "etc/dracut.conf.d", (when, when))


def test_a_theme_newer_than_the_image_forces_a_rebuild(tmp_path):
    """Arch wiki: every theme change needs the initramfs regenerated. Without
    counting plymouthd.conf as an input the plan stays silent and the splash
    keeps the previous theme forever."""
    target = _dracut_target(tmp_path)
    backend = DracutBackend({"plymouth": {"theme": "bgrt"}}, target)
    conf = tmp_path / "etc/dracut.conf.d/dasik.conf"
    conf.write_text(backend.desired_value())
    theme_conf = tmp_path / "etc/plymouth/plymouthd.conf"
    theme_conf.parent.mkdir(parents=True, exist_ok=True)
    theme_conf.write_text("[Daemon]\nTheme=bgrt\n")

    os.utime(conf, (1, 1))                                  # config: old
    _backdate_conf_d(tmp_path)
    os.utime(tmp_path / "boot/initramfs-linux.img", (2, 2))  # image: built after it
    os.utime(theme_conf, (3, 3))                            # theme: changed since

    assert backend.actual_value() is None


def test_an_image_newer_than_the_theme_is_converged(tmp_path):
    target = _dracut_target(tmp_path)
    backend = DracutBackend({"plymouth": {"theme": "bgrt"}}, target)
    conf = tmp_path / "etc/dracut.conf.d/dasik.conf"
    conf.write_text(backend.desired_value())
    theme_conf = tmp_path / "etc/plymouth/plymouthd.conf"
    theme_conf.parent.mkdir(parents=True, exist_ok=True)
    theme_conf.write_text("[Daemon]\nTheme=bgrt\n")

    os.utime(conf, (1, 1))
    _backdate_conf_d(tmp_path)
    os.utime(theme_conf, (2, 2))
    os.utime(tmp_path / "boot/initramfs-linux.img", (3, 3))

    assert backend.actual_value() == backend.desired_value()
