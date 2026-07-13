"""Declarative LUKS2 encryption for DiskPartitionAction.

Before this, `_encrypt_partition` ran `cryptsetup luksFormat` with no key source,
so an encrypted install *prompted* for the passphrase — impossible to run
unattended. These tests pin the declarative, non-interactive behaviour: the
passphrase from the config is piped over stdin via `--key-file -` (never on
argv), or a key file is used; and the model accepts the new fields.
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.models.disk_model import Partition
from dasik.lib.command_worker.command_worker import Command


# --- model ---------------------------------------------------------------- #

def test_partition_accepts_luks_password_and_keyfile():
    p = Partition(label="ROOT", size="rest", filesystem="ext4",
                  encrypt=True, luks_name="cryptroot", luks_password="s3cret")
    assert p.luks_password == "s3cret"
    p2 = Partition(label="ROOT", size="rest", filesystem="ext4",
                   encrypt=True, luks_name="cryptroot", luks_keyfile="/root/key")
    assert p2.luks_keyfile == "/root/key"


def test_encrypt_requires_luks_name():
    with pytest.raises(ValueError):
        Partition(label="ROOT", size="rest", filesystem="ext4", encrypt=True)


# --- non-interactive cryptsetup ------------------------------------------- #

def _encrypt(partition):
    action = DiskPartitionAction(config=None)
    with patch.object(Command, "execute") as ex:
        mapper = action._encrypt_partition("/dev/loop0p1", partition)
    return mapper, ex.call_args_list


def test_passphrase_is_piped_over_stdin_not_argv():
    p = Partition(label="ROOT", size="rest", filesystem="ext4",
                  encrypt=True, luks_name="cryptroot", luks_password="hunter2")
    mapper, calls = _encrypt(p)
    assert mapper == "/dev/mapper/cryptroot"
    # luksFormat: batch-mode, key from stdin, passphrase via input=
    fmt = calls[0]
    assert fmt.args[0] == "cryptsetup"
    assert "--batch-mode" in fmt.args[1] and "--key-file" in fmt.args[1]
    assert fmt.args[1][fmt.args[1].index("--key-file") + 1] == "-"
    assert fmt.kwargs["input"] == b"hunter2"
    # the passphrase must NOT appear on argv
    assert "hunter2" not in fmt.args[1]
    # open: same key source + passphrase
    opn = calls[1]
    assert opn.args[1][:2] == ["open", "--key-file"] or "open" in opn.args[1]
    assert opn.kwargs["input"] == b"hunter2"


def test_keyfile_is_used_when_given():
    p = Partition(label="ROOT", size="rest", filesystem="ext4",
                  encrypt=True, luks_name="cryptroot", luks_keyfile="/root/luks.key")
    _mapper, calls = _encrypt(p)
    fmt = calls[0]
    assert "--key-file" in fmt.args[1]
    assert fmt.args[1][fmt.args[1].index("--key-file") + 1] == "/root/luks.key"
    assert fmt.kwargs.get("input") is None      # keyfile → no stdin passphrase


def test_no_passphrase_falls_back_to_interactive():
    """With neither password nor keyfile, cryptsetup runs without a key source
    (the legacy interactive prompt) — no stdin input is piped."""
    p = Partition(label="ROOT", size="rest", filesystem="ext4",
                  encrypt=True, luks_name="cryptroot")
    _mapper, calls = _encrypt(p)
    fmt = calls[0]
    assert "--key-file" not in fmt.args[1]
    assert fmt.kwargs.get("input") is None
