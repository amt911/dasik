"""Tests for initramfs.base detection helpers.

The load-bearing one is ``detect_root_fs``: a synced btrfs root has
``mountpoint: null`` with ``/`` living on the ``@`` subvolume. If the backend
fails to see that as the root it never forces btrfs into the initramfs, and the
encrypted-root boot hangs. It must use the shared ``mounts_root`` predicate.
"""
from __future__ import annotations

from dasik.lib.actions.initramfs.base import detect_root_fs


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
