"""The `pam` block: three independent sub-blocks, each optional.

An absent sub-block is not the empty one — the distinction the reconciler leans
on when a previous generation owned a domain the config no longer declares.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.pam_model import PamModel


def test_every_sub_block_is_optional():
    model = PamModel()
    assert model.faillock is None
    assert model.limits is None
    assert model.pwquality is None


def test_faillock_defaults():
    faillock = PamModel(faillock={}).faillock
    assert (faillock.deny, faillock.fail_interval, faillock.unlock_time) == (5, 900, 600)
    assert faillock.persistent is True


def test_deny_zero_is_refused_because_it_disables_the_lockout():
    with pytest.raises(ValidationError, match="disable the lockout"):
        PamModel(faillock={"deny": 0})


def test_negative_times_are_refused():
    with pytest.raises(ValidationError, match="non-negative"):
        PamModel(faillock={"unlock_time": -1})


def test_limits_must_be_positive():
    with pytest.raises(ValidationError, match="positive"):
        PamModel(limits={"nproc_soft": 0})


def test_pwquality_defaults_require_one_of_each_class():
    pwq = PamModel(pwquality={}).pwquality
    assert pwq.enable is True
    assert (pwq.minlen, pwq.difok, pwq.retry) == (10, 6, 2)
    # Negative credits are pwquality's spelling for "require at least one".
    assert (pwq.dcredit, pwq.ucredit, pwq.lcredit, pwq.ocredit) == (-1, -1, -1, -1)
    assert pwq.enforce_for_root is False


def test_a_minlen_below_pwqualitys_own_floor_is_refused():
    with pytest.raises(ValidationError, match="minlen"):
        PamModel(pwquality={"minlen": 4})


def test_sub_blocks_are_independent():
    model = PamModel(limits={"nproc_soft": 50, "nproc_hard": 100})
    assert model.faillock is None
    assert model.limits.nproc_hard == 100
