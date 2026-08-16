"""An enrollment that failed must not pass for one that worked.

`unlock_fido2` / `unlock_tpm2` run `systemd-cryptenroll`, and the call once had
no `check=True`, so every way it can fail was silent:

  * the key is not plugged in (the common one: it is a fresh install and the
    token is in a drawer);
  * the user never touched the key and it timed out;
  * the key needs a PIN;
  * there is no TPM in the machine.

The install then reported success, wrote `rd.luks.options=…fido2-device=auto`
into the boot entry, and left a LUKS header with no token in it. Verified on a
VM: `unlock_fido2: true` with no key present installs rc=0 and the machine boots
straight to a passphrase prompt.

The enrollment now lives in `LuksTokenAction` (issue #242) — inside the disk
action it only ran while FORMATTING, so a failure was not merely silent, it was
also never retried. These are the assertions that survived the move, plus the
one behaviour that deliberately changed: with no passphrase to authorise it, the
enrollment used to be skipped with a note, and now refuses out loud, because a
plan that announced the change and then quietly did nothing is the worse of the
two.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.luks_token_action import LuksTokenAction
from dasik.lib.exceptions.exceptions import CommandExecutionError

_DUMP_BARE = "LUKS header information\nKeyslots:\n  0: luks2\nTokens:\n"


def _action(password="hunter2", kind="fido2"):
    part = {"label": "ROOT", "encrypt": True, "luks_name": "cryptroot",
            "mountpoint": "/", f"unlock_{kind}": True}
    if password is not None:
        part["luks_password"] = password
    action = LuksTokenAction(
        {"disks": {"disks": [{"device": "/dev/vda", "partitions": [part]}]}}, None)
    action._luks_device = lambda name: "/dev/vda2"
    action._dump = lambda dev: _DUMP_BARE
    return action


def test_the_enroll_is_checked():
    action = _action()
    with patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    (cmd, _args), kwargs = execute.call_args
    assert cmd == "systemd-cryptenroll"
    assert kwargs.get("check") is True, "a failed enrollment must not be silent"


def test_a_failed_enroll_reaches_the_caller():
    """`check=True` makes Command.execute raise; the apply must not swallow it."""
    action = _action()
    changes = action.plan(managed=[])

    with patch("dasik.lib.actions.luks_token_action.Command.execute",
               side_effect=CommandExecutionError(
                   "systemd-cryptenroll failed: no FIDO2 device found")):
        with pytest.raises(CommandExecutionError):
            action.apply(changes)


def test_without_a_passphrase_it_refuses_instead_of_skipping():
    """Changed on purpose. cryptenroll needs an existing key to authorise the
    new one; without it the command would sit waiting on a tty. Refusing names
    the fix, and a plan that promised the token is not left lying."""
    action = _action(password=None)
    changes = action.plan(managed=[])

    with pytest.raises(CommandExecutionError) as e:
        action.apply(changes)

    assert "luks_password" in str(e.value)


def test_the_missing_passphrase_is_visible_in_the_plan_too():
    assert "luks_password" in _action(password=None).plan(managed=[])[0].reason


def test_the_passphrase_still_goes_in_by_env_not_argv():
    """It is the LUKS passphrase: argv is world-readable, the environment is not."""
    action = _action(kind="tpm2")
    with patch("dasik.lib.actions.luks_token_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(action.plan(managed=[]))

    (_cmd, args), kwargs = execute.call_args
    assert "hunter2" not in " ".join(args)
    assert kwargs["env"]["PASSWORD"] == "hunter2"
