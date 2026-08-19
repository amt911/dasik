"""Several FIDO2 keys on one volume.

``unlock_fido2`` was a boolean, and a boolean can only say "there is a token in
the header" — which is exactly what a machine with two keys and one enrolled
also says. The second key had to be added by hand with `systemd-cryptenroll`,
and dasik would neither plan it nor ever notice it was there.

A COUNT is the honest widening: the LUKS header can be asked how many
``systemd-fido2`` tokens it carries, and nothing more. It cannot say WHICH key
each one is — systemd stores a credential, not a label — so a list of names
would promise an identity no probe can confirm and no `sync` can read back.

``true`` stays exactly one key, so every config written before this keeps
meaning what it meant.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.disk_model import Partition, fido2_count


def _part(**kw):
    base = dict(label="root", size="rest", filesystem="ext4", mountpoint="/",
                encrypt=True, luks_name="cryptroot")
    base.update(kw)
    return Partition(**base)


def test_it_defaults_to_no_key():
    assert _part().unlock_fido2 is False


def test_true_still_means_one_key():
    """The back-compatibility promise: every existing config keeps working."""
    assert _part(unlock_fido2=True).unlock_fido2 is True
    assert fido2_count({"unlock_fido2": True}) == 1


@pytest.mark.parametrize("n", [1, 2, 3, 8])
def test_a_count_is_accepted(n):
    assert _part(unlock_fido2=n).unlock_fido2 == n
    assert fido2_count({"unlock_fido2": n}) == n


def test_zero_is_the_same_as_off():
    assert fido2_count({"unlock_fido2": 0}) == 0
    assert fido2_count({"unlock_fido2": False}) == 0
    assert fido2_count({}) == 0


@pytest.mark.parametrize("bad", [-1, -3])
def test_a_negative_count_is_refused(bad):
    with pytest.raises(ValidationError):
        _part(unlock_fido2=bad)


def test_more_keys_than_the_header_holds_is_refused():
    """LUKS2 has 32 keyslots, and one of them has to stay a passphrase."""
    with pytest.raises(ValidationError):
        _part(unlock_fido2=32)


def test_the_count_survives_a_round_trip():
    """`sync` writes the model back out; a count must not degrade to a bool."""
    dumped = _part(unlock_fido2=3).model_dump(mode="json")
    assert dumped["unlock_fido2"] == 3
    assert Partition(**dumped).unlock_fido2 == 3
