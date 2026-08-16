"""Hardware tokens as a domain, not as a side effect of formatting.

`_process_disk` enrolls right after `luksFormat`, and that code is only reached
when the disk needs INSTALL — a fresh or wiped one. So on a working machine,
adding `unlock_fido2: true` did nothing at all except put
`fido2-device=auto` on the kernel command line, pointing at a token nobody
enrolled; a failed enrolment was never retried; and dropping the flag left the
keyslot in the header for good.

Here the state of the LUKS header decides, exactly as `LuksKeyfileAction` does
for the pendrive keyfile. The REMOVE half is guarded: wiping the last thing that
opens a volume is how you lose a disk.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.luks_token_action import LuksTokenAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Op

# A LUKS2 header with a passphrase in slot 0 and a TPM2 token bound to slot 1.
_DUMP_TPM2 = """LUKS header information
Version:        2
Keyslots:
  0: luks2
        Key:        512 bits
  1: luks2
        Key:        512 bits
Tokens:
  0: systemd-tpm2
        Keyslot:    1
Digests:
  0: pbkdf2
"""

# Only a passphrase.
_DUMP_BARE = """LUKS header information
Version:        2
Keyslots:
  0: luks2
Tokens:
Digests:
  0: pbkdf2
"""

# The dangerous one: the ONLY keyslot is the token's.
_DUMP_TOKEN_ONLY = """LUKS header information
Version:        2
Keyslots:
  1: luks2
Tokens:
  0: systemd-tpm2
        Keyslot:    1
Digests:
  0: pbkdf2
"""


def _config(tpm2=False, fido2=False, password="hunter2"):
    part = {"label": "ROOT", "encrypt": True, "luks_name": "cryptroot",
            "mountpoint": "/"}
    if password is not None:
        part["luks_password"] = password
    if tpm2:
        part["unlock_tpm2"] = True
    if fido2:
        part["unlock_fido2"] = True
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}


def _action(config, dump=_DUMP_BARE, device="/dev/vda2"):
    action = LuksTokenAction(config, None)
    action._luks_device = lambda name: device
    action._dump = lambda dev: dump
    return action


def _items(changes):
    return [(c.op.name, c.item) for c in changes]


# --- plan ------------------------------------------------------------------ #

def test_a_declared_token_missing_from_the_header_is_planned():
    action = _action(_config(tpm2=True), dump=_DUMP_BARE)

    assert _items(action.plan(managed=[])) == [("INSTALL", "cryptroot:tpm2")]


def test_a_token_already_in_the_header_plans_nothing():
    action = _action(_config(tpm2=True), dump=_DUMP_TPM2)

    assert action.plan(managed=[]) == []


def test_both_kinds_are_separate_items():
    action = _action(_config(tpm2=True, fido2=True), dump=_DUMP_BARE)

    assert _items(action.plan(managed=[])) == [
        ("INSTALL", "cryptroot:tpm2"), ("INSTALL", "cryptroot:fido2")]


def test_dropping_a_flag_dasik_owns_removes_the_keyslot():
    action = _action(_config(), dump=_DUMP_TPM2)

    assert _items(action.plan(managed=["cryptroot:tpm2"])) == [
        ("REMOVE", "cryptroot:tpm2")]


def test_a_token_nobody_declared_and_dasik_does_not_own_is_left_alone():
    """Somebody else's enrolment is not dasik's to wipe."""
    action = _action(_config(), dump=_DUMP_TPM2)

    assert action.plan(managed=[]) == []


def test_an_unencrypted_config_plans_nothing():
    action = _action({"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "ROOT", "mountpoint": "/"}]}]}})

    assert action.plan(managed=[]) == []


def test_a_volume_that_is_not_open_still_reports_the_divergence():
    """No mapping means dasik cannot read the header — saying "converged"
    there would hide a token that was never enrolled."""
    action = _action(_config(tpm2=True))
    action._luks_device = lambda name: None

    changes = action.plan(managed=[])

    assert _items(changes) == [("INSTALL", "cryptroot:tpm2")]
    assert "not open" in changes[0].reason


def test_the_plan_says_when_the_passphrase_is_missing():
    """sync drops luks_password, so a captured config cannot authorise an
    enrolment — the plan has to say that rather than fail at apply."""
    action = _action(_config(tpm2=True, password=None), dump=_DUMP_BARE)

    changes = action.plan(managed=[])

    assert _items(changes) == [("INSTALL", "cryptroot:tpm2")]
    assert "luks_password" in changes[0].reason


# --- the removal guard ------------------------------------------------------ #

def test_removal_is_refused_when_the_token_is_the_only_way_in():
    """The whole point of the guard: wiping the last keyslot loses the disk."""
    action = _action(_config(), dump=_DUMP_TOKEN_ONLY)

    changes = action.plan(managed=["cryptroot:tpm2"])

    assert _items(changes) == []


def test_the_refusal_is_not_silent(capsys):
    action = _action(_config(), dump=_DUMP_TOKEN_ONLY)
    action.plan(managed=["cryptroot:tpm2"])

    assert "passphrase" in capsys.readouterr().out


# --- managed_keys ----------------------------------------------------------- #

def test_managed_keys_names_what_is_declared():
    action = _action(_config(tpm2=True, fido2=True))

    assert action.managed_keys() == {
        "luks_token": ["cryptroot:tpm2", "cryptroot:fido2"]}


# --- apply ------------------------------------------------------------------ #

def test_apply_enrolls_with_the_passphrase_from_the_config():
    action = _action(_config(tpm2=True), dump=_DUMP_BARE)
    changes = action.plan(managed=[])

    with patch("dasik.lib.actions.luks_token_action.Command.execute") as run:
        run.return_value = MagicMock(stdout=b"", returncode=0)
        action.apply(changes)

    cmd, args = run.call_args[0][0], run.call_args[0][1]
    assert cmd == "systemd-cryptenroll"
    assert args == ["--tpm2-device=auto", "/dev/vda2"]
    assert run.call_args[1]["env"] == {"PASSWORD": "hunter2"}
    assert run.call_args[1]["check"] is True


def test_apply_without_a_passphrase_fails_loudly_instead_of_hanging():
    """systemd-cryptenroll would prompt on a tty and block forever in a run
    nobody is watching."""
    action = _action(_config(tpm2=True, password=None), dump=_DUMP_BARE)
    changes = action.plan(managed=[])

    with pytest.raises(CommandExecutionError) as e:
        action.apply(changes)

    assert "luks_password" in str(e.value)


def test_apply_wipes_the_slot_on_a_removal():
    action = _action(_config(), dump=_DUMP_TPM2)
    changes = action.plan(managed=["cryptroot:tpm2"])

    with patch("dasik.lib.actions.luks_token_action.Command.execute") as run:
        run.return_value = MagicMock(stdout=b"", returncode=0)
        action.apply(changes)

    assert run.call_args[0][1] == ["--wipe-slot=tpm2", "/dev/vda2"]


def test_apply_does_nothing_when_the_plan_is_empty():
    action = _action(_config(tpm2=True), dump=_DUMP_TPM2)

    with patch("dasik.lib.actions.luks_token_action.Command.execute") as run:
        action.apply(action.plan(managed=[]))

    run.assert_not_called()


# --- capture ---------------------------------------------------------------- #

def test_import_state_is_empty_because_the_disk_action_owns_the_flags():
    """`unlock_tpm2`/`unlock_fido2` live inside the partition, and
    DiskPartitionAction.import_state already reads them out of the header.
    Capturing them here too would write the same fact twice."""
    assert _action(_config(tpm2=True), dump=_DUMP_TPM2).import_state() == {}


# --- ownership must survive a sync ------------------------------------------ #
#
# `Reconciler._owned_after_sync` records `actual & (claimable | declared)`. An
# action that does not implement `actual()` inherits the base's empty set, so
# the intersection is empty and EVERY sync silently disowns the domain — after
# which dropping the flag plans nothing and the keyslot stays for ever. Found on
# a real VM: enrol day-2 (works), sync, drop the flag, and the plan is mute.

def test_actual_reports_the_tokens_the_header_carries():
    action = _action(_config(tpm2=True), dump=_DUMP_TPM2)

    assert action.actual() == {"cryptroot:tpm2"}


def test_actual_is_empty_when_the_header_has_no_token():
    action = _action(_config(tpm2=True), dump=_DUMP_BARE)

    assert action.actual() == set()


def test_actual_sees_a_volume_the_config_no_longer_flags():
    """The drop case, which is the one that matters: the partition still
    declares `encrypt`, the flag is gone, and the token is still enrolled."""
    action = _action(_config(), dump=_DUMP_TPM2)

    assert action.actual() == {"cryptroot:tpm2"}


def test_ownership_survives_a_sync():
    """The regression this file exists for, at the seam that broke it."""
    from dasik.lib.reconciler.reconciler import Reconciler

    action = _action(_config(tpm2=True), dump=_DUMP_TPM2)
    managed_all = {"luks_token": ["cryptroot:tpm2"]}

    kept = Reconciler._owned_after_sync(
        Reconciler(config={}, target=None, manifest=None, action_metas=[]),
        action, "luks_token", managed_all)

    assert kept == ["cryptroot:tpm2"]
