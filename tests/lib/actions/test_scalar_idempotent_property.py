"""Property-based idempotency for ScalarV3Action (CLAUDE.md § Quality).

ScalarV3Action.plan is the shared single-value idempotency core — timezone,
initramfs, and every other scalar domain reconcile through it. The NixOS
invariant: when the system's value already equals the desired value, planning
yields nothing; otherwise exactly one MODIFY. These properties assert that for
hundreds of generated (desired, actual) pairs via a tiny test subclass, so the
core that all scalar domains depend on is proven idempotent independently of any
one domain's I/O.
"""
from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.scalar_action import ScalarV3Action
from dasik.lib.state.change import Op


class _Scalar(ScalarV3Action):
    """Minimal concrete scalar action with injectable desired/actual values."""

    _DOMAIN = "tz"

    @property
    def name(self):
        return "test scalar"

    def __init__(self, desired, actual):
        super().__init__({}, context=None)
        self._d = desired
        self._a = actual
        self.set_calls = 0

    def _desired_value(self):
        return self._d

    def _actual_value(self):
        return self._a

    def _set_value(self):
        self.set_calls += 1

    def _import_fragment(self, value):
        return {self._DOMAIN: value}


# Meaningful values are non-empty (empty string is falsy → treated as "unset").
_value = st.text(min_size=1, max_size=8)
_maybe = st.none() | st.just("") | _value


@given(v=_value)
def test_converged_scalar_is_a_noop(v):
    """desired == actual (both set) ⇒ no plan, is_needed False, verify True."""
    a = _Scalar(desired=v, actual=v)
    assert a.plan(managed=[]) == []
    assert a.is_needed() is False
    assert a.verify() is True


@given(actual=_maybe)
def test_unset_desired_is_always_a_noop(actual):
    """No declared value (None or empty) ⇒ nothing to do, whatever the system is."""
    for desired in (None, ""):
        a = _Scalar(desired=desired, actual=actual)
        assert a.plan(managed=[]) == []
        assert a.is_needed() is False


@given(desired=_value, actual=_maybe)
def test_differing_value_yields_exactly_one_modify(desired, actual):
    """desired set and != actual ⇒ exactly one MODIFY carrying the desired value."""
    if actual == desired:
        return  # covered by the converged property
    a = _Scalar(desired=desired, actual=actual)
    changes = a.plan(managed=[])
    assert len(changes) == 1
    c = changes[0]
    assert c.op is Op.MODIFY
    assert c.item == desired
    assert c.domain == "tz"
    assert c.reason == "set"          # the MODIFY carries the documented reason
    assert a.is_needed() is True
    assert a.verify() is False


def test_apply_without_a_target_is_a_safe_noop():
    """apply() with a context that has no `target` attribute must be a no-op, not
    a crash (the getattr default guards legacy/partial contexts)."""
    a = _Scalar(desired="x", actual="y")
    a.context = object()  # truthy, but no `.target`
    a.apply(a.plan(managed=[]))  # must not raise
    assert a.set_calls == 0


@given(desired=_value, actual=_maybe)
def test_apply_only_sets_when_a_change_is_planned(desired, actual):
    """apply() invokes _set_value iff there is a change and a target — never on a
    converged domain (idempotent apply)."""
    from types import SimpleNamespace

    a = _Scalar(desired=desired, actual=actual)
    a.context = SimpleNamespace(target=object())
    a.apply(a.plan(managed=[]))
    if desired and desired != actual:
        assert a.set_calls == 1
    else:
        assert a.set_calls == 0


@given(v=_maybe)
def test_actual_and_managed_keys_reflect_the_value(v):
    a = _Scalar(desired=v, actual=v)
    assert a.actual() == ({v} if v else set())
    assert a.managed_keys() == {"tz": [v] if v else []}
