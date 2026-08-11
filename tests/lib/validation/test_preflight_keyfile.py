"""Preflight: a pendrive unlock that cannot possibly work is caught before the
first mutation — i.e. before the disk is wiped."""
from dasik.lib.validation.preflight import preflight


def _cfg(**part_kw):
    part = dict(label="root", size="rest", filesystem="ext4", mountpoint="/",
                encrypt=True, luks_name="cryptroot")
    part.update(part_kw)
    return {"bootloader": "sd-boot",
            "disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}


def _codes(issues, level):
    return [i.code for i in issues if i.level == level]


def test_a_key_device_without_a_keyfile_is_an_error():
    """`unlock_keydev` alone names a device holding nothing: no rd.luks.key is
    ever emitted, so the declaration silently does nothing."""
    issues = preflight(_cfg(unlock_keydev="1234-ABCD"), efi_boot=True)

    assert "keydev_without_keyfile" in _codes(issues, "error")


def test_a_key_device_without_its_filesystem_warns():
    """Without the module the initramfs cannot read the pendrive at all — but
    the root filesystem may already provide it, so this is not provable."""
    issues = preflight(_cfg(unlock_keyfile="/keyfile", unlock_keydev="1234-ABCD"),
                       efi_boot=True)

    assert "keydev_without_filesystem" in _codes(issues, "warning")


def test_a_fully_declared_pendrive_unlock_is_quiet():
    issues = preflight(_cfg(unlock_keyfile="/keyfile", unlock_keydev="1234-ABCD",
                            unlock_keydev_fs="vfat"), efi_boot=True)

    assert not [i for i in issues if i.code.startswith("keydev_")]


def test_an_embedded_keyfile_needs_no_key_device():  # noqa: D401
    """No `unlock_keydev` at all: the keyfile travels inside the initramfs."""
    issues = preflight(_cfg(unlock_keyfile="/etc/keyfile"), efi_boot=True)

    assert not [i for i in issues if i.code.startswith("keydev_")]


def test_a_config_without_disks_is_quiet():
    assert not [i for i in preflight({"bootloader": "sd-boot"}, efi_boot=True)
                if i.code.startswith("keydev_")]


def test_an_embedded_keyfile_warns_that_the_key_lands_on_the_esp():
    """No key device means the key is baked into the initramfs, which lives on
    the unencrypted ESP — full-disk encryption whose key ships next to it."""
    issues = preflight(_cfg(unlock_keyfile="/etc/keyfile"), efi_boot=True)

    assert "keyfile_embedded_in_initramfs" in _codes(issues, "warning")


def test_a_pendrive_unlock_does_not_get_the_esp_warning():
    issues = preflight(_cfg(unlock_keyfile="/keyfile", unlock_keydev="1234-ABCD",
                            unlock_keydev_fs="vfat"), efi_boot=True)

    assert "keyfile_embedded_in_initramfs" not in _codes(issues, "warning")
