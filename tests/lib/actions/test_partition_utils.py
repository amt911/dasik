from dasik.lib.actions.partition_utils import mounts_root


def test_partition_mountpoint_root():
    assert mounts_root({"mountpoint": "/"}) is True


def test_subvolume_mounts_root():
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": [
        {"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"}]}) is True


def test_no_root_partition_or_subvol():
    assert mounts_root({"mountpoint": "/boot"}) is False
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": [
        {"name": "@home", "mountpoint": "/home"}]}) is False


def test_empty_or_missing_subvolumes():
    assert mounts_root({"mountpoint": None}) is False
    assert mounts_root({"mountpoint": None, "btrfs_subvolumes": []}) is False
    assert mounts_root({}) is False
