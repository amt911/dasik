"""A swap partition can declare that it is re-encrypted on every boot.

`swap_encryption: random` is plain dm-crypt keyed from /dev/urandom — a
different mechanism from `encrypt: true` (LUKS, one persistent key). The model
is where the two are kept from being declared together, and where the mode is
kept off filesystems it means nothing for.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.disk_model import Partition, SwapEncryption


def _swap(**over):
    base = {"label": "swap", "size": "8GiB", "filesystem": "swap"}
    base.update(over)
    return base


def test_swap_encryption_defaults_to_none():
    assert Partition(**_swap()).swap_encryption is SwapEncryption.NONE


def test_swap_encryption_random_is_accepted_on_a_swap_partition():
    part = Partition(**_swap(swap_encryption="random"))
    assert part.swap_encryption is SwapEncryption.RANDOM


def test_swap_encryption_random_is_refused_on_a_non_swap_filesystem():
    with pytest.raises(ValidationError, match="swap_encryption"):
        Partition(**_swap(filesystem="ext4", swap_encryption="random"))


def test_swap_encryption_random_conflicts_with_luks():
    with pytest.raises(ValidationError, match="swap_encryption"):
        Partition(**_swap(swap_encryption="random", encrypt=True,
                          luks_name="cryptswap"))


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValidationError):
        Partition(**_swap(swap_encryption="sometimes"))
