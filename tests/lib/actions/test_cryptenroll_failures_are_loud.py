"""An enrollment that failed must not pass for one that worked.

`unlock_fido2` / `unlock_tpm2` run `systemd-cryptenroll` while the disk is being
formatted — and the call had no `check=True`, so every way it can fail was
silent:

  * the key is not plugged in (the common one: it is a fresh install and the
    token is in a drawer);
  * the user never touched the key and it timed out;
  * the key needs a PIN;
  * there is no TPM in the machine.

The install then reported success, wrote `rd.luks.options=…fido2-device=auto`
into the boot entry, and left a LUKS header with no token in it. Verified on a
VM: `unlock_fido2: true` with no key present installs rc=0 and the machine boots
straight to a passphrase prompt.

Nothing else notices afterwards, either: the enrollment only happens while
formatting, so a re-run does not retry it (see the issue linked from the PR).
The least dasik can do is say the enrollment failed.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.models.disk_model import Partition


def _partition(**kw):
    return Partition(label="ROOT", size="rest", filesystem="ext4",
                     partition_type="linux", encrypt=True,
                     luks_name="cryptroot", luks_password="hunter2", **kw)


def _action():
    return DiskPartitionAction({}, None)


def test_the_enroll_is_checked(monkeypatch):
    calls = []
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute:
        execute.side_effect = lambda *a, **kw: calls.append((a, kw)) or MagicMock(returncode=0)
        _action()._enroll_cryptenroll("/dev/vda2", _partition(unlock_fido2=True),
                                      "--fido2-device=auto")

    (cmd, args), kwargs = calls[0]
    assert cmd == "systemd-cryptenroll"
    assert kwargs.get("check") is True, "a failed enrollment must not be silent"


def test_a_failed_enroll_reaches_the_caller():
    """`check=True` makes Command.execute raise; the apply must not swallow it."""
    from dasik.lib.exceptions.exceptions import CommandExecutionError

    with patch("dasik.lib.actions.disk_partition_action.Command.execute",
               side_effect=CommandExecutionError("systemd-cryptenroll failed: no FIDO2 device found")):
        with pytest.raises(CommandExecutionError):
            _action()._enroll_cryptenroll("/dev/vda2", _partition(unlock_fido2=True),
                                          "--fido2-device=auto")


def test_without_a_passphrase_it_is_skipped_and_says_so(capsys):
    """Unchanged behaviour: cryptenroll needs an existing key to authorise the
    new one, and there is nothing to fail loudly about."""
    part = Partition(label="ROOT", size="rest", filesystem="ext4",
                     partition_type="linux", encrypt=True, luks_name="cryptroot",
                     unlock_fido2=True)

    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute:
        _action()._enroll_cryptenroll("/dev/vda2", part, "--fido2-device=auto")

    execute.assert_not_called()
    assert "skipped" in capsys.readouterr().out


def test_the_passphrase_still_goes_in_by_env_not_argv():
    """It is the LUKS passphrase: argv is world-readable, the environment is not."""
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        _action()._enroll_cryptenroll("/dev/vda2", _partition(unlock_tpm2=True),
                                      "--tpm2-device=auto")

    (_cmd, args), kwargs = execute.call_args
    assert "hunter2" not in " ".join(args)
    assert kwargs["env"]["PASSWORD"] == "hunter2"
