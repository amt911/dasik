"""The unlock keyfile has ONE owner, and re-running is a no-op.

Enrollment used to live inside `_setup_encryption`, which only runs on a fresh
format: an already-installed machine could never gain a pendrive, and the
keyfile itself was assumed to exist (the old imperative installer created it
with dd). This action creates it, enrolls it, and — crucially — knows when it
is already enrolled, because `cryptsetup open --test-passphrase` says so.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.luks_keyfile_action import LuksKeyfileAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op

_CFG = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot", "luks_password": "hunter2",
     "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD",
     "unlock_keydev_fs": "vfat"}]}]}}

_EMBEDDED = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
    {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
     "encrypt": True, "luks_name": "cryptroot", "luks_password": "hunter2",
     "unlock_keyfile": "/etc/keyfile"}]}]}}


def _action(cfg=_CFG):
    return LuksKeyfileAction(cfg, None)


def _mounted(part, read_only=False):        # noqa: ARG001 - patch stand-in
    """What _mount_keydev returns: a mountpoint for a key device, None for a
    keyfile that lives inside the target root."""
    return "/run/dasik-key" if part.get("unlock_keydev") else None


def _plan(action, managed=(), key_works=False, keyfile_exists=True):
    """plan() with the key device stubbed — it mounts read-only for real."""
    with patch.object(LuksKeyfileAction, "_mount_keydev", side_effect=_mounted), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch.object(LuksKeyfileAction, "_luks_device", return_value="/dev/vda2"), \
         patch.object(LuksKeyfileAction, "_key_works", return_value=key_works), \
         patch("os.path.exists", return_value=keyfile_exists):
        return action.plan(managed=list(managed))


def _planned(action, **kw):
    return [(c.op.name, c.item) for c in _plan(action, **kw)]


# --- plan ------------------------------------------------------------------ #

def test_no_keyfile_declared_plans_nothing():
    assert _plan(_action({"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "rest", "filesystem": "ext4",
         "encrypt": True, "luks_name": "cryptroot"}]}]}})) == []


def test_a_config_without_disks_plans_nothing():
    assert _plan(_action({})) == []


def test_a_keyfile_that_does_not_unlock_yet_is_planned():
    assert _planned(_action(), key_works=False) == [("INSTALL", "cryptroot:/keyfile")]


def test_a_keyfile_that_does_not_exist_yet_is_planned():
    """The pendrive is attached but still empty — this is the fresh-install
    case, where dasik generates the key itself."""
    with patch.object(LuksKeyfileAction, "_key_device_present", return_value=True):
        changes = _plan(_action(), keyfile_exists=False)
    assert [c.item for c in changes] == ["cryptroot:/keyfile"]
    assert "does not exist" in changes[0].reason


def test_an_enrolled_keyfile_plans_nothing():
    """Idempotency: `cryptsetup open --test-passphrase` already succeeds, so a
    re-run of the same config changes nothing."""
    assert _plan(_action(), key_works=True) == []


def test_an_absent_key_device_is_still_announced():
    """Fail loudly, not silently: a plan that skipped the item would leave a
    machine whose declared unlock simply does not exist."""
    with patch.object(LuksKeyfileAction, "_key_device_present", return_value=False):
        changes = _plan(_action(), key_works=True)
    assert [c.item for c in changes] == ["cryptroot:/keyfile"]
    assert "key device" in changes[0].reason


def test_an_unreadable_key_device_is_announced_rather_than_skipped():
    with patch.object(LuksKeyfileAction, "_mount_keydev", side_effect=OSError("no mount")), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch("os.path.exists", return_value=True):
        changes = _action().plan(managed=[])
    assert [c.item for c in changes] == ["cryptroot:/keyfile"]
    assert "could not be read" in changes[0].reason


def test_an_undeclared_keyfile_dasik_owns_is_reported_but_not_killed():
    """Removing a keyslot with luksKillSlot can destroy access to the volume, so
    the parameter goes and the slot stays — said out loud in the plan."""
    changes = _plan(_action({}), managed=["cryptroot:/keyfile"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "cryptroot:/keyfile")]
    assert "luksRemoveKey" in changes[0].reason


def test_the_domain_is_owned_so_the_manifest_can_reason_about_it():
    assert _action().managed_keys() == {"luks_keyfile": ["cryptroot:/keyfile"]}


# --- apply ----------------------------------------------------------------- #

def _apply(action, exists, changes=None):
    """Run apply() with the key device mounted at a fake path."""
    if changes is None:
        changes = _plan(action, key_works=False, keyfile_exists=exists)
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch.object(LuksKeyfileAction, "_mount_keydev", side_effect=_mounted), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch.object(LuksKeyfileAction, "_luks_device", return_value="/dev/vda2"), \
         patch("os.path.exists", return_value=exists), \
         patch("os.makedirs"), \
         patch("os.chmod"):
        action.apply(changes)
    return run.call_args_list


def test_apply_generates_the_keyfile_and_enrolls_it():
    with patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        calls = _apply(_action(), exists=False)

    commands = [c.args[0] for c in calls]
    assert "dd" in commands

    add = next(c for c in calls if c.args[0] == "cryptsetup" and c.args[1][0] == "luksAddKey")
    assert add.args[1] == ["luksAddKey", "--key-file", "-", "/dev/vda2",
                           "/run/dasik-key/keyfile"]
    assert add.kwargs["input"] == b"hunter2"      # existing passphrase authorises it


def test_apply_does_not_regenerate_an_existing_keyfile():
    """The pendrive may already hold the key from a previous machine — dd-ing
    over it would revoke that machine's unlock."""
    with patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        calls = _apply(_action(), exists=True)

    assert "dd" not in [c.args[0] for c in calls]
    assert any(c.args[1][0] == "luksAddKey" for c in calls if c.args[0] == "cryptsetup")


def test_apply_of_an_embedded_keyfile_uses_the_target_path():
    action = LuksKeyfileAction(_EMBEDDED, None)
    with patch.object(LuksKeyfileAction, "_key_works", return_value=False):
        calls = _apply(action, exists=True)

    add = next(c for c in calls if c.args[0] == "cryptsetup" and c.args[1][0] == "luksAddKey")
    assert add.args[1][-1] == "/mnt/etc/keyfile"


def test_apply_of_a_removal_touches_no_keyslot():
    calls = _apply(_action({}), exists=True,
                   changes=[Change("luks_keyfile", Op.REMOVE, "cryptroot:/keyfile")])

    assert calls == []


def test_enrollment_without_an_existing_key_is_refused():
    """luksAddKey needs an existing key, and prompting is not an option in an
    unattended installer — fail loudly instead of hanging on a password prompt."""
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/",
         "encrypt": True, "luks_name": "cryptroot",
         "unlock_keyfile": "/keyfile", "unlock_keydev": "1234-ABCD"}]}]}}
    action = LuksKeyfileAction(cfg, None)

    with patch.object(LuksKeyfileAction, "_mount_keydev", return_value="/run/dasik-key"), \
         patch.object(LuksKeyfileAction, "_umount_keydev"), \
         patch.object(LuksKeyfileAction, "_luks_device", return_value="/dev/vda2"), \
         patch("dasik.lib.actions.luks_keyfile_action.Command.execute"), \
         patch("os.path.exists", return_value=True), \
         pytest.raises(CommandExecutionError):
        action.apply([Change("luks_keyfile", Op.INSTALL, "cryptroot:/keyfile")])


def test_apply_fails_loudly_when_the_luks_device_cannot_be_resolved():
    action = _action()
    with patch.object(LuksKeyfileAction, "_luks_device", return_value=None), \
         patch("dasik.lib.actions.luks_keyfile_action.Command.execute"), \
         pytest.raises(CommandExecutionError):
        action.apply([Change("luks_keyfile", Op.INSTALL, "cryptroot:/keyfile")])


# --- probes ---------------------------------------------------------------- #

def test_the_key_device_path_normalizes_a_bare_uuid():
    assert LuksKeyfileAction._keydev_path("1234-ABCD") == "/dev/disk/by-uuid/1234-ABCD"
    assert LuksKeyfileAction._keydev_path("UUID=1234-ABCD") == "/dev/disk/by-uuid/1234-ABCD"
    assert LuksKeyfileAction._keydev_path("LABEL=pen") == "/dev/disk/by-label/pen"
    assert LuksKeyfileAction._keydev_path("PARTUUID=ab-01") == "/dev/disk/by-partuuid/ab-01"
    assert LuksKeyfileAction._keydev_path("/dev/sdb1") == "/dev/sdb1"


def test_key_works_asks_cryptsetup_without_opening_anything():
    """--test-passphrase creates no mapping: the probe cannot disturb a running
    system, which is what makes it safe to run from plan()."""
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute",
               return_value=MagicMock(returncode=0)) as run:
        assert LuksKeyfileAction._key_works("/dev/vda2", "/run/k/keyfile") is True

    assert run.call_args.args[1] == ["open", "--test-passphrase", "--key-file",
                                     "/run/k/keyfile", "/dev/vda2"]


def test_key_works_is_false_when_cryptsetup_rejects_the_key():
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute",
               return_value=MagicMock(returncode=2)):
        assert LuksKeyfileAction._key_works("/dev/vda2", "/run/k/keyfile") is False


def test_a_probe_that_blows_up_reads_as_not_enrolled():
    """No cryptsetup, no device: plan the enrollment and let apply say why."""
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute",
               side_effect=OSError("boom")):
        assert LuksKeyfileAction._key_works("/dev/vda2", "/run/k/keyfile") is False


# --- review fixes: a mount that silently fails writes the key to a tmpfs ---- #

def test_the_key_device_mount_is_checked():
    """Without check=True a failed mount looks like success: the keyfile lands
    on the /run tmpfs, is enrolled as a real keyslot, and vanishes on reboot —
    leaving a machine that trusts a key nobody has."""
    action = _action()
    part = action._declared()[0][0]
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch("os.makedirs"):
        action._mount_keydev(part)

    assert run.call_args.kwargs.get("check") is True


def test_a_fat_key_device_is_mounted_with_a_root_only_umask():
    """vfat has no permission bits: chmod 600 cannot work there, so the mode has
    to come from the mount instead — otherwise the LUKS key is world-readable."""
    action = _action()
    part = action._declared()[0][0]
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch("os.makedirs"):
        action._mount_keydev(part)

    assert "umask=0077" in ",".join(run.call_args.args[1])


def test_a_read_only_probe_mount_says_so():
    action = _action()
    part = action._declared()[0][0]
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute") as run, \
         patch("os.makedirs"):
        action._mount_keydev(part, read_only=True)

    assert "ro" in ",".join(run.call_args.args[1]).split(",")


def test_chmod_failing_on_a_filesystem_without_modes_is_not_fatal():
    """The kernel returns EPERM for chmod on vfat. Aborting there would leave a
    freshly created keyfile that was never enrolled."""
    action = _action()
    with patch("dasik.lib.actions.luks_keyfile_action.Command.execute"), \
         patch("os.path.exists", return_value=False), \
         patch("os.makedirs"), \
         patch("os.chmod", side_effect=PermissionError("fat")):
        action._create_keyfile("/run/dasik-key/keyfile")     # must not raise
