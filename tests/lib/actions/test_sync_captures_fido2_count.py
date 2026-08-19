"""`sync` has to read the keys back as a COUNT, not as a yes/no.

A machine with three FIDO2 keys captured as ``unlock_fido2: true``, and
re-applying that capture would plan nothing while two keyslots the admin relies
on go unrecorded — drop the block later and dasik would wipe one keyslot and
believe the volume converged. The rule dasik holds everywhere else applies here
too: what the machine has is what the capture says, and re-planning a capture
must be silent.

One token still captures as ``true``, because that is what it means and it
keeps every config written before this readable.
"""
from unittest.mock import MagicMock, patch

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.luks_token_action import LuksTokenAction


def _dump(n_fido2=0, tpm2=False):
    out = ["LUKS header information\n", "Keyslots:\n",
           "  0: luks2\n"]
    for i in range(1, n_fido2 + 1):
        out.append(f"  {i}: luks2\n")
    out.append("Tokens:\n")
    for i in range(n_fido2):
        out.append(f"  {i}: systemd-fido2\n        Keyslot:    {i + 1}\n")
    if tpm2:
        out.append(f"  {n_fido2}: systemd-tpm2\n        Keyslot:    {n_fido2 + 1}\n")
    return "".join(out)


def _action(dump):
    """A DiskPartitionAction whose only live probe is `cryptsetup luksDump`."""
    config = {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": False,
        "partitions": [{
            "label": "root", "size": "rest", "filesystem": "ext4",
            "mountpoint": "/", "encrypt": True, "luks_name": "cryptroot",
            "luks_password": "hunter2", "format": False,
        }],
    }]}
    action = DiskPartitionAction(config, None)
    action._luks_backing_device = lambda name: "/dev/vda2"
    action._read_luks_uuid = lambda name: "u-u-i-d"
    action._read_luks_options = lambda uuid: []
    action._capture_unlock_keyfile = lambda part, uuid: None
    action._live_subvol_options = lambda: {}
    action._decode = lambda raw: dump
    return action


def _captured(dump):
    action = _action(dump)
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        run.return_value = MagicMock(stdout=dump.encode(), returncode=0)
        frag = action.import_state()
    return frag["disks"]["disks"][0]["partitions"][0]


def test_one_key_captures_as_true():
    assert _captured(_dump(n_fido2=1))["unlock_fido2"] is True


def test_three_keys_capture_as_three():
    assert _captured(_dump(n_fido2=3))["unlock_fido2"] == 3


def test_no_key_invents_nothing():
    assert _captured(_dump(n_fido2=0)).get("unlock_fido2") in (False, None)


def test_tpm2_beside_the_keys_is_not_counted_as_one():
    part = _captured(_dump(n_fido2=2, tpm2=True))
    assert part["unlock_fido2"] == 2
    assert part["unlock_tpm2"] is True


def test_the_capture_replans_to_nothing():
    """The real invariant: sync -> plan is silent, at any count."""
    dump = _dump(n_fido2=3)
    part = _captured(dump)
    token_action = LuksTokenAction(
        {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}, None)
    token_action._luks_device = lambda name: "/dev/vda2"
    token_action._dump = lambda dev: dump

    managed = [f"cryptroot:fido2{'' if i == 1 else f'#{i}'}" for i in (1, 2, 3)]
    assert token_action.plan(managed=managed) == []


# --- reality overrides the declaration ------------------------------------- #
#
# Caught in a VM: a guest with ZERO tokens in its header, whose config declared
# `unlock_fido2: 2`, captured as `unlock_fido2: 2`. `import_state` only ever
# SET the flag when tokens were found and never cleared it when they were not,
# so the capture described the config instead of the machine — the one thing
# sync must never do. With a boolean it was just as wrong and just as invisible.
#
# The probe FAILING is a different answer from the probe saying "none": an
# unreadable header must leave the declaration alone, or a cryptsetup that is
# not there would silently disarm a config.

def _captured_declaring(dump, declared_fido2, declared_tpm2=False, readable=True):
    action = _action(dump)
    part = action.disks[0].partitions[0]
    part.unlock_fido2 = declared_fido2
    part.unlock_tpm2 = declared_tpm2
    if not readable:
        action._read_luks_tokens = lambda name: None
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        run.return_value = MagicMock(stdout=dump.encode(), returncode=0)
        frag = action.import_state()
    return frag["disks"]["disks"][0]["partitions"][0]


def test_a_declared_key_the_machine_does_not_have_is_cleared():
    part = _captured_declaring(_dump(n_fido2=0), declared_fido2=2)
    assert part["unlock_fido2"] in (False, 0)


def test_a_declared_count_higher_than_reality_comes_back_as_reality():
    part = _captured_declaring(_dump(n_fido2=1), declared_fido2=3)
    assert part["unlock_fido2"] is True


def test_a_declared_tpm2_the_machine_does_not_have_is_cleared():
    part = _captured_declaring(_dump(n_fido2=0), declared_fido2=False,
                               declared_tpm2=True)
    assert part["unlock_tpm2"] is False


def test_an_unreadable_header_leaves_the_declaration_alone():
    """No cryptsetup, no open mapping: an answer we cannot get is not a 'no'."""
    part = _captured_declaring(_dump(n_fido2=0), declared_fido2=2, readable=False)
    assert part["unlock_fido2"] == 2
