"""The key device's umask must not depend on the user remembering a field.

vfat has no permission bits, so `chmod 600` on a keyfile there returns EPERM —
the "only root may read this" part has to come from the MOUNT (`umask=0077`).
dasik added that option only when the config declared `unlock_keydev_fs: vfat`,
and that field is optional:

    "unlock_keydev": "/dev/disk/by-uuid/1234-ABCD"      <- no fs declared

A vfat pendrive mounted without the umask exposes the LUKS keyfile to every
local user for as long as it is mounted, and the chmod that would have fixed it
cannot work on that filesystem. The declaration is a hint, not a fact: ask the
device.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction
from dasik.lib.target.target import Target


def _action(tmp_path):
    return LuksKeyfileAction({}, ActionContext(target=Target(root=str(tmp_path))))


def _mount_options(tmp_path, part, fstype_out=b"", fstype_rc=0):
    """The options `mount` was called with, given what the probe reports."""
    action = _action(tmp_path)
    calls = []

    def execute(cmd, args, *a, **kw):
        calls.append((cmd, args))
        if cmd == "lsblk":
            return MagicMock(returncode=fstype_rc, stdout=fstype_out)
        return MagicMock(returncode=0, stdout=b"")

    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute", side_effect=execute), \
         patch("os.makedirs"):
        action._mount_keydev(part)

    mount = [args for cmd, args in calls if cmd == "mount"][0]
    return ",".join(mount[1].split(",")) if "-o" in mount else ""


_KEYDEV = {"unlock_keydev": "/dev/disk/by-uuid/1234-ABCD"}


def test_a_declared_vfat_still_gets_the_umask(tmp_path):
    options = _mount_options(tmp_path, {**_KEYDEV, "unlock_keydev_fs": "vfat"})

    assert "umask=0077" in options


def test_an_undeclared_vfat_is_detected_and_gets_it_too(tmp_path):
    options = _mount_options(tmp_path, _KEYDEV, fstype_out=b"vfat\n")

    assert "umask=0077" in options


def test_exfat_counts_as_well(tmp_path):
    options = _mount_options(tmp_path, _KEYDEV, fstype_out=b"exfat\n")

    assert "umask=0077" in options


def test_a_real_filesystem_must_not_get_it(tmp_path):
    """ext4 rejects `umask=` outright: adding it would fail the mount."""
    options = _mount_options(tmp_path, _KEYDEV, fstype_out=b"ext4\n")

    assert "umask" not in options


def test_a_probe_that_fails_falls_back_to_the_declaration(tmp_path):
    options = _mount_options(tmp_path, {**_KEYDEV, "unlock_keydev_fs": "vfat"},
                             fstype_out=b"", fstype_rc=1)

    assert "umask=0077" in options


def test_a_probe_that_fails_with_nothing_declared_stays_as_it_was(tmp_path):
    options = _mount_options(tmp_path, _KEYDEV, fstype_out=b"", fstype_rc=1)

    assert "umask" not in options


def test_the_read_only_probe_keeps_both(tmp_path):
    action = _action(tmp_path)
    calls = []

    def execute(cmd, args, *a, **kw):
        calls.append((cmd, args))
        return MagicMock(returncode=0, stdout=b"vfat\n" if cmd == "lsblk" else b"")

    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute", side_effect=execute), \
         patch("os.makedirs"):
        action._mount_keydev(_KEYDEV, read_only=True)

    mount = [args for cmd, args in calls if cmd == "mount"][0]
    options = mount[1]
    assert "ro" in options.split(",") and "umask=0077" in options
