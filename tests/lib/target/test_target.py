import pytest

from dasik.lib.target.target import Target


def test_root_host_is_not_chroot():
    assert Target(root="/").is_chroot is False


def test_root_mnt_is_chroot():
    assert Target(root="/mnt").is_chroot is True


def test_default_root_is_mnt():
    assert Target().root == "/mnt"


def test_path_maps_into_mnt():
    assert Target(root="/mnt").path("/etc/hostname") == "/mnt/etc/hostname"


def test_path_unchanged_for_host_root():
    assert Target(root="/").path("/etc/hostname") == "/etc/hostname"


def test_path_rejects_relative():
    with pytest.raises(ValueError):
        Target(root="/mnt").path("etc/hostname")
