"""The key device's filesystem: the initramfs needs it by name.

Arch wiki (dm-crypt/System configuration#rd.luks.key): "If the type of file
system is different than your root file system, you must include the kernel
module for it in the initramfs." The pendrive is not necessarily plugged in when
`plan` runs, so the config — not a probe — is the source of truth.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.disk_model import Partition


def _part(**kw):
    base = dict(label="root", size="rest", filesystem="ext4", mountpoint="/",
                encrypt=True, luks_name="cryptroot")
    base.update(kw)
    return Partition(**base)


def test_the_key_device_filesystem_defaults_to_unset():
    assert _part().unlock_keydev_fs is None


@pytest.mark.parametrize("fs", ["vfat", "exfat", "ext4", "btrfs", "xfs"])
def test_a_supported_key_device_filesystem_is_accepted(fs):
    assert _part(unlock_keydev_fs=fs).unlock_keydev_fs == fs


@pytest.mark.parametrize("bad", ["reiserfs4; rm -rf /", "vfat nls_cp437", "ntfs", ""])
def test_an_unsupported_key_device_filesystem_is_rejected(bad):
    """The value becomes a kernel module name inside the initramfs."""
    with pytest.raises(ValidationError):
        _part(unlock_keydev_fs=bad)
