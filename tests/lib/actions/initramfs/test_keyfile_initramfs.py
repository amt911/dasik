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
    assert "MODULES=(vfat)" in MkinitcpioBackend(_PEN).desired_value()


def test_mkinitcpio_embeds_a_keyfile_that_has_no_key_device():
    assert "FILES=(/etc/keyfile)" in MkinitcpioBackend(_EMBEDDED).desired_value()


def test_mkinitcpio_without_a_keyfile_declares_neither():
    value = MkinitcpioBackend({}).desired_value()
    assert "MODULES=" not in value
    assert "FILES=" not in value


def test_mkinitcpio_keeps_the_modules_already_in_the_file(tmp_path):
    """A user's own MODULES list is not dasik's to drop."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/mkinitcpio.conf").write_text(
        "MODULES=(i915)\nHOOKS=(base udev autodetect block filesystems fsck)\n")
    value = MkinitcpioBackend(_PEN, Target(root=str(tmp_path))).desired_value()
    modules = [line for line in value.splitlines() if line.startswith("MODULES=")][0]
    assert "i915" in modules and "vfat" in modules


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
