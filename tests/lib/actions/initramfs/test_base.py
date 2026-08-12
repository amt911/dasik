"""Tests for initramfs.base detection helpers.

The load-bearing one is ``detect_root_fs``: a synced btrfs root has
``mountpoint: null`` with ``/`` living on the ``@`` subvolume. If the backend
fails to see that as the root it never forces btrfs into the initramfs, and the
encrypted-root boot hangs. It must use the shared ``mounts_root`` predicate.
"""
from __future__ import annotations

from dasik.lib.actions.initramfs.base import detect_hibernation, detect_root_fs


def _cfg(part):
    return {"disks": {"disks": [{"partitions": [part]}]}}


def test_direct_root_mountpoint_returns_fs():
    assert detect_root_fs(_cfg({"mountpoint": "/", "filesystem": "btrfs"})) == "btrfs"


def test_subvol_mounted_root_is_detected():
    part = {
        "mountpoint": None,
        "filesystem": "btrfs",
        "btrfs_subvolumes": [
            {"name": "@", "mountpoint": "/"},
            {"name": "@home", "mountpoint": "/home"},
        ],
    }
    assert detect_root_fs(_cfg(part)) == "btrfs"


def test_subvols_without_root_returns_none():
    part = {
        "mountpoint": None,
        "filesystem": "btrfs",
        "btrfs_subvolumes": [{"name": "@home", "mountpoint": "/home"}],
    }
    assert detect_root_fs(_cfg(part)) is None


def test_missing_or_empty_subvolumes_do_not_raise():
    assert detect_root_fs(_cfg({"mountpoint": None, "filesystem": "btrfs"})) is None
    assert detect_root_fs(_cfg({"mountpoint": None, "btrfs_subvolumes": []})) is None
    assert detect_root_fs(_cfg({"mountpoint": None, "btrfs_subvolumes": None})) is None


# --- a random-key swap is not a hibernation device ------------------------- #
#
# The key is drawn fresh on every boot and discarded at shutdown, so a resume
# image written with the previous key can never be read back. Pulling the resume
# module in for it only costs boot time looking for an image that cannot exist.

def test_a_random_key_swap_is_not_a_hibernation_device():
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    assert detect_hibernation(cfg) is False


def test_a_plain_swap_still_asks_for_the_resume_module():
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "swap", "filesystem": "swap"}]}]}}
    assert detect_hibernation(cfg) is True


def test_a_resume_parameter_still_wins_even_next_to_a_random_swap():
    # A synced config can name the resume device on the cmdline while the swap
    # itself is described elsewhere; that declaration is what preflight refuses,
    # and until it does the initramfs must still carry the module.
    cfg = {"kernel_cmdline": ["resume=/dev/mapper/swap"],
           "disks": {"disks": [{"partitions": [
               {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    assert detect_hibernation(cfg) is True
