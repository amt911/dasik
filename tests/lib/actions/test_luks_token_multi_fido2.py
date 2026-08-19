"""More than one FIDO2 key on the same volume.

The header can be COUNTED, not named: two keys are two ``systemd-fido2`` tokens
and nothing distinguishes them, so the domain carries one item per keyslot
(``cryptroot:fido2``, ``cryptroot:fido2#2``, …) and the first keeps the name it
had, so a manifest written before this still owns what it owned.

Two things this had to get right, both learnt the hard way elsewhere in dasik:

* ``systemd-cryptenroll --fido2-device=auto`` needs EXACTLY ONE key plugged in,
  so keys are enrolled one at a time with a human asked in between. A human who
  declared three keys and owns two must be able to say so and carry on — the
  install must not die on the third, and the third must still show up in the
  next ``plan``.
* ``--wipe-slot=fido2`` wipes EVERY fido2 keyslot. Going from three keys to two
  with it would take all three, so a removal names the keyslot NUMBER.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.luks_token_action import LuksTokenAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Op

_HEADER = "LUKS header information\nVersion:        2\n"


def _dump(passphrase_slots=(0,), fido2_slots=(), tpm2_slots=()):
    """A luksDump with the given keyslots and tokens."""
    slots = sorted(set(passphrase_slots) | set(fido2_slots) | set(tpm2_slots))
    out = [_HEADER, "Keyslots:\n"]
    for s in slots:
        out.append(f"  {s}: luks2\n        Key:        512 bits\n")
    out.append("Tokens:\n")
    n = 0
    for s in fido2_slots:
        out.append(f"  {n}: systemd-fido2\n        fido2-credential: xxx\n"
                   f"        Keyslot:    {s}\n")
        n += 1
    for s in tpm2_slots:
        out.append(f"  {n}: systemd-tpm2\n        Keyslot:    {s}\n")
        n += 1
    out.append("Digests:\n  0: pbkdf2\n")
    return "".join(out)


def _action(keys=2, dump=None, password="hunter2", policy=None):
    part = {"label": "ROOT", "encrypt": True, "luks_name": "cryptroot",
            "mountpoint": "/", "unlock_fido2": keys}
    if password is not None:
        part["luks_password"] = password
    config = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}
    if policy is not None:
        config["luks_token_policy"] = {"enroll_failure": policy}
    action = LuksTokenAction(config, None)
    action._luks_device = lambda name: "/dev/vda2"
    action._dump = lambda dev: dump if dump is not None else _dump()
    return action


def _items(changes, op=None):
    return [c.item for c in changes if op is None or c.op is op]


# --- plan: the count is the whole comparison ---------------------------- #

def test_two_declared_and_none_enrolled_plans_two():
    changes = _action(keys=2).plan(managed=[])
    assert _items(changes, Op.INSTALL) == ["cryptroot:fido2", "cryptroot:fido2#2"]


def test_the_first_key_keeps_the_old_item_name():
    """A manifest written when this was a boolean still owns its keyslot."""
    assert _items(_action(keys=1).plan(managed=[]))[0] == "cryptroot:fido2"


def test_one_of_two_enrolled_plans_only_the_missing_one():
    changes = _action(keys=2, dump=_dump((0,), fido2_slots=(1,))).plan(managed=[])
    assert _items(changes, Op.INSTALL) == ["cryptroot:fido2#2"]


def test_both_enrolled_plans_nothing():
    action = _action(keys=2, dump=_dump((0,), fido2_slots=(1, 2)))
    assert action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2"]) == []


def test_true_is_still_exactly_one_key():
    action = _action(keys=True, dump=_dump((0,), fido2_slots=(1,)))
    assert action.plan(managed=["cryptroot:fido2"]) == []


def test_dropping_from_three_to_two_removes_one():
    action = _action(keys=2, dump=_dump((0,), fido2_slots=(1, 2, 3)))
    managed = ["cryptroot:fido2", "cryptroot:fido2#2", "cryptroot:fido2#3"]
    changes = action.plan(managed=managed)
    assert _items(changes, Op.REMOVE) == ["cryptroot:fido2#3"]
    assert all(c.destructive for c in changes if c.op is Op.REMOVE)


def test_the_last_way_in_is_never_wiped():
    """Every keyslot is a token: wiping one more leaves nobody able to open it."""
    action = _action(keys=0, dump=_dump((), fido2_slots=(0,)))
    assert action.plan(managed=["cryptroot:fido2"]) == []


# --- actual(): one item per token in the header -------------------------- #

def test_actual_counts_the_tokens():
    action = _action(keys=2, dump=_dump((0,), fido2_slots=(1, 2)))
    assert action.actual() == {"cryptroot:fido2", "cryptroot:fido2#2"}


def test_actual_reports_what_is_there_not_what_is_declared():
    action = _action(keys=3, dump=_dump((0,), fido2_slots=(1,)))
    assert action.actual() == {"cryptroot:fido2"}


# --- apply: one key at a time, and the human decides --------------------- #

def _tty(answers):
    """Patch a terminal whose `input()` returns `answers` in order."""
    return patch("builtins.input", side_effect=list(answers))


def _stdin_isatty(value):
    stdin = MagicMock()
    stdin.isatty.return_value = value
    return patch("dasik.lib.actions.luks_token_action.sys.stdin", stdin)


def test_each_key_is_enrolled_after_asking_for_it():
    action = _action(keys=2)
    changes = action.plan(managed=[])
    with _stdin_isatty(True), _tty(["", ""]) as ask, \
            patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(changes)

    assert execute.call_count == 2, "one systemd-cryptenroll per declared key"
    assert ask.call_count == 2, "the human is asked to swap keys between them"
    prompt = ask.call_args_list[1].args[0]
    assert "2" in prompt and "cryptroot" in prompt


def test_one_key_alone_is_not_prompted_for():
    """The old, non-interactive behaviour is untouched for a single key."""
    action = _action(keys=1)
    with _stdin_isatty(True), patch("builtins.input") as ask, \
            patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))
    ask.assert_not_called()
    assert execute.call_count == 1


def test_answering_skip_stops_enrolling_and_does_not_raise():
    """Declared three, owns two: the third is skipped, the apply carries on."""
    action = _action(keys=3)
    changes = action.plan(managed=[])
    with _stdin_isatty(True), _tty(["", "", "s"]), \
            patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(changes)      # must NOT raise

    assert execute.call_count == 2, "the skipped key is never enrolled"


def test_a_skipped_key_is_still_missing_on_the_next_plan():
    """Skipping records nothing: the divergence has to survive into the plan."""
    action = _action(keys=3, dump=_dump((0,), fido2_slots=(1, 2)))
    assert _items(action.plan(managed=[]), Op.INSTALL) == ["cryptroot:fido2#3"]


def test_a_failed_enrolment_can_be_skipped_at_the_prompt():
    """The key is plugged in but never touched: still not a dead install."""
    action = _action(keys=2)
    changes = action.plan(managed=[])
    with _stdin_isatty(True), _tty(["", "s", ""]), \
            patch("dasik.lib.actions.luks_token_action.Command.execute",
                  side_effect=CommandExecutionError("no FIDO2 device found")):
        action.apply(changes)      # must NOT raise


def test_without_a_terminal_nobody_is_asked():
    """A scripted install must never block on a question nobody can answer."""
    action = _action(keys=2)
    with _stdin_isatty(False), patch("builtins.input") as ask, \
            patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))
    ask.assert_not_called()
    assert execute.call_count == 2


def test_without_a_terminal_a_failure_still_aborts_by_default():
    """Unchanged: a silent failure leaves fido2-device=auto pointing at nothing."""
    action = _action(keys=2)
    changes = action.plan(managed=[])
    with _stdin_isatty(False), \
            patch("dasik.lib.actions.luks_token_action.Command.execute",
                  side_effect=CommandExecutionError("no FIDO2 device found")):
        with pytest.raises(CommandExecutionError):
            action.apply(changes)


def test_the_policy_can_turn_that_failure_into_a_warning():
    action = _action(keys=2, policy="warn-and-continue")
    changes = action.plan(managed=[])
    with _stdin_isatty(False), \
            patch("dasik.lib.actions.luks_token_action.Command.execute",
                  side_effect=CommandExecutionError("no FIDO2 device found")):
        action.apply(changes)      # must NOT raise


# --- removal names the keyslot, never the token kind --------------------- #

def test_a_removal_wipes_one_numbered_keyslot():
    action = _action(keys=2, dump=_dump((0,), fido2_slots=(1, 2, 3)))
    changes = action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2",
                                   "cryptroot:fido2#3"])
    with patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(changes)

    (cmd, args), kwargs = execute.call_args
    assert cmd == "systemd-cryptenroll"
    assert "--wipe-slot=3" in args, "the highest fido2 keyslot, by number"
    assert "--wipe-slot=fido2" not in args, "that would wipe all three"
    assert kwargs.get("check") is True


def test_dropping_the_whole_block_wipes_every_fido2_slot():
    action = _action(keys=0, dump=_dump((0,), fido2_slots=(1, 2)))
    changes = action.plan(managed=["cryptroot:fido2", "cryptroot:fido2#2"])
    wiped = []
    with patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        execute.side_effect = lambda cmd, args, **kw: wiped.append(args) or MagicMock(returncode=0)
        action.apply(changes)

    slots = sorted(a for args in wiped for a in args if a.startswith("--wipe-slot="))
    assert slots == ["--wipe-slot=1", "--wipe-slot=2"]


# --- tpm2 is untouched by all of this ------------------------------------ #

def test_tpm2_is_still_a_single_boolean_slot():
    part = {"label": "ROOT", "encrypt": True, "luks_name": "cryptroot",
            "unlock_tpm2": True, "luks_password": "hunter2"}
    action = LuksTokenAction(
        {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}, None)
    action._luks_device = lambda name: "/dev/vda2"
    action._dump = lambda dev: _dump()
    assert _items(action.plan(managed=[])) == ["cryptroot:tpm2"]


# --- `--yes` means "do not ask me" --------------------------------------- #
#
# Found in a VM: the guest installer runs on a serial console, so stdin IS a
# terminal, and `dasik apply --yes` sat forever at "plug in FIDO2 key 1 of 2".
# An unattended install that declares two keys must not deadlock on a question
# nobody is there to answer — which is exactly what --yes already promises for
# the destructive-changes prompt.

def _action_yes(keys=2, policy=None):
    action = _action(keys=keys, policy=policy)
    action.context = MagicMock(target=None, assume_yes=True)
    return action


def test_yes_skips_the_swap_prompt_even_on_a_terminal():
    action = _action_yes()
    with _stdin_isatty(True), patch("builtins.input") as ask, \
            patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    ask.assert_not_called()
    assert execute.call_count == 2


def test_yes_hands_a_failure_to_the_policy_not_to_a_question():
    action = _action_yes(policy="warn-and-continue")
    changes = action.plan(managed=[])
    with _stdin_isatty(True), patch("builtins.input") as ask, \
            patch("dasik.lib.actions.luks_token_action.Command.execute",
                  side_effect=CommandExecutionError("no FIDO2 device found")):
        action.apply(changes)      # must NOT raise, must NOT ask

    ask.assert_not_called()


def test_yes_with_the_default_policy_still_aborts_loudly():
    action = _action_yes()
    changes = action.plan(managed=[])
    with _stdin_isatty(True), patch("builtins.input"), \
            patch("dasik.lib.actions.luks_token_action.Command.execute",
                  side_effect=CommandExecutionError("no FIDO2 device found")):
        with pytest.raises(CommandExecutionError):
            action.apply(changes)
