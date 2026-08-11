"""The initramfs must be able to READ the key it is told to unlock with.

Arch wiki (dm-crypt/System configuration#rd.luks.key): "If the type of file
system is different than your root file system, you must include the kernel
module for it in the initramfs." And a keyfile with no key device lives inside
the target root, so it only exists at boot if it was baked into the image —
otherwise dasik writes an rd.luks.key pointing at a path nothing can open.
"""
from dasik.lib.actions.initramfs.base import (detect_embedded_keyfiles,
                                              detect_keydev_filesystems)
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.initramfs.mkinitcpio import MkinitcpioBackend
from dasik.lib.target.target import Target

_PEN = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "btrfs", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot",
     "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD",
     "unlock_keydev_fs": "vfat"}]}]}}

_EMBEDDED = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot",
     "unlock_keyfile": "/etc/keyfile"}]}]}}


def test_the_key_device_filesystem_is_detected():
    assert detect_keydev_filesystems(_PEN) == ["vfat"]
    assert detect_keydev_filesystems({}) == []


def test_a_key_device_without_a_declared_filesystem_detects_nothing():
    """Nothing to force: preflight warns about it, the backend cannot guess."""
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
         "encrypt": True, "luks_name": "cryptroot",
         "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD"}]}]}}
    assert detect_keydev_filesystems(cfg) == []


def test_only_a_keyfile_without_a_key_device_counts_as_embedded():
    assert detect_embedded_keyfiles(_EMBEDDED) == ["/etc/keyfile"]
    assert detect_embedded_keyfiles(_PEN) == []
    assert detect_embedded_keyfiles({}) == []


# --- mkinitcpio ------------------------------------------------------------ #

def test_mkinitcpio_declares_the_module_for_the_key_device():
    assert "MODULES+=(vfat nls_cp437 nls_iso8859-1)" in MkinitcpioBackend(_PEN).desired_value()


def test_fat_gets_its_nls_charset_modules():
    """Mounting vfat without them fails with "IO charset cp437 not found", so
    the key on the commonest pendrive filesystem would be unreadable."""
    value = MkinitcpioBackend(_PEN).desired_value()
    assert "nls_cp437" in value


def test_mkinitcpio_embeds_a_keyfile_that_has_no_key_device():
    assert "FILES+=(/etc/keyfile)" in MkinitcpioBackend(_EMBEDDED).desired_value()


def test_mkinitcpio_without_a_keyfile_declares_neither():
    value = MkinitcpioBackend({}).desired_value()
    assert "MODULES" not in value
    assert "FILES" not in value


def test_the_additions_live_in_a_dasik_owned_dropin(tmp_path):
    """A user's own MODULES/FILES arrays are never touched, and dasik's own
    additions can therefore be taken BACK — merging into the main conf leaves a
    FILES=(/keyfile) baking a LUKS key into every image forever."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "MODULES=(i915)\nHOOKS=(base udev autodetect block filesystems fsck)\n")
    from unittest.mock import patch
    target = Target(root=str(tmp_path))
    with patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute"):
        MkinitcpioBackend(_PEN, target).apply()

    assert "MODULES=(i915)" in (tmp_path / "etc/mkinitcpio.conf").read_text()
    dropin = tmp_path / "etc/mkinitcpio.conf.d/dasik.conf"
    assert "MODULES+=(vfat" in dropin.read_text()


def test_un_declaring_the_unlock_removes_the_dropin(tmp_path):
    """The REMOVE direction: an embedded keyfile must stop being baked into the
    image the moment the config stops asking for it."""
    (tmp_path / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "HOOKS=(base udev autodetect block filesystems fsck)\n")
    dropin = tmp_path / "etc/mkinitcpio.conf.d/dasik.conf"
    dropin.write_text("# Managed by dasik\nFILES+=(/etc/keyfile)\n")
    from unittest.mock import patch
    target = Target(root=str(tmp_path))

    assert MkinitcpioBackend({}, target).actual_value() != \
        MkinitcpioBackend({}, target).desired_value()          # the drift is visible
    with patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute"):
        MkinitcpioBackend({}, target).apply()

    assert not dropin.exists()


def test_a_theme_change_is_visible_to_mkinitcpio(tmp_path):
    """mkinitcpio compares its own directives, so a theme change (which only
    rewrites plymouthd.conf) would otherwise be invisible and the image would
    keep the old splash."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "HOOKS=(base udev autodetect block filesystems fsck)\n")
    target = Target(root=str(tmp_path))
    from unittest.mock import patch
    with patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute"):
        MkinitcpioBackend({"plymouth": {"theme": "bgrt"}}, target).apply()

    later = MkinitcpioBackend({"plymouth": {"theme": "spinner"}}, target)
    assert later.actual_value() != later.desired_value()


def test_mkinitcpio_is_idempotent_against_what_it_wrote(tmp_path):
    """desired_value and actual_value must agree once applied, or every plan
    re-runs mkinitcpio forever."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "MODULES=()\nHOOKS=(base udev autodetect block filesystems fsck)\n")
    target = Target(root=str(tmp_path))
    backend = MkinitcpioBackend(_PEN, target)
    from unittest.mock import patch
    with patch("dasik.lib.actions.initramfs.mkinitcpio.Command.execute"):
        backend.apply()
    assert MkinitcpioBackend(_PEN, target).actual_value() == \
        MkinitcpioBackend(_PEN, target).desired_value()


# --- dracut ---------------------------------------------------------------- #

def test_dracut_declares_the_key_device_filesystem():
    assert 'filesystems+=" vfat "' in DracutBackend(_PEN).desired_value()


def test_dracut_installs_an_embedded_keyfile():
    assert 'install_items+=" /etc/keyfile "' in DracutBackend(_EMBEDDED).desired_value()


def test_dracut_without_a_keyfile_declares_neither():
    conf = DracutBackend({}).desired_value()
    assert "filesystems+=" not in conf
    assert "install_items+=" not in conf
