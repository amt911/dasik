"""Property-based idempotency for CompositeV3Action (CLAUDE.md § Quality).

CompositeV3Action.plan is the shared multi-field idempotency core — locale,
network, and pacman reconcile through it. The NixOS invariant: when the actual
record equals the desired record, planning is empty; otherwise exactly one
MODIFY listing precisely the fields that differ. Proven here over generated
state dicts via a tiny test subclass, independent of any one domain's I/O.
"""
from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.composite_action import CompositeV3Action
from dasik.lib.state.change import Op


class _Composite(CompositeV3Action):
    _DOMAIN = "loc"

    @property
    def name(self):
        return "test composite"

    def __init__(self, desired, actual):
        super().__init__({}, context=None)
        self._d = desired
        self._a = actual

    def _desired_state(self):
        return self._d

    def _actual_state(self):
        return self._a

    def _set_value(self):
        pass

    def _import_fragment(self, value):
        return {self._DOMAIN: value}


_key = st.text(alphabet="abcde", min_size=1, max_size=4)
_val = st.one_of(st.text(max_size=4), st.booleans(), st.integers(-3, 3))
_state = st.dictionaries(_key, _val, max_size=5)


@given(state=_state)
def test_converged_composite_is_a_noop(state):
    """actual record == desired record ⇒ no plan (re-run is a no-op)."""
    a = _Composite(desired=state, actual=dict(state))
    assert a.plan(managed=[]) == []
    assert a.is_needed() is False
    assert a.verify() is True


@given(state=_state)
def test_absent_record_modifies_all_declared_fields(state):
    """No record on disk (actual None) ⇒ one MODIFY listing all desired fields."""
    if not state:
        # An empty desired record is guarded at the subclass level (e.g.
        # NetworkAction.plan returns [] when nothing is declared) before the base
        # plan runs, so the base's {}-vs-None behaviour is never reached in
        # practice — not part of this property.
        return
    a = _Composite(desired=state, actual=None)
    changes = a.plan(managed=[])
    assert len(changes) == 1
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == ",".join(sorted(state))
    assert changes[0].domain == "loc"
    assert changes[0].reason == "config"


@given(data=st.data(), state=_state)
def test_modify_lists_exactly_the_changed_fields(data, state):
    """A differing record ⇒ exactly one MODIFY whose item names precisely the
    fields where desired != actual — the change is scoped, not a blanket rewrite."""
    if not state:
        return
    # Build an actual that differs on a chosen non-empty subset of keys.
    keys = sorted(state)
    to_change = data.draw(st.lists(st.sampled_from(keys), min_size=1, unique=True))
    actual = dict(state)
    for k in to_change:
        actual[k] = ("CHANGED", actual[k])  # guaranteed different value

    a = _Composite(desired=state, actual=actual)
    changes = a.plan(managed=[])
    assert len(changes) == 1
    assert changes[0].op is Op.MODIFY
    assert changes[0].item == ",".join(sorted(to_change))
    assert changes[0].domain == "loc"
    assert changes[0].reason == "config"


@given(state=_state)
def test_actual_value_serialization_is_canonical(state):
    """The value view is a canonical (sorted-key) JSON of the record, so key
    ordering never causes a spurious change."""
    import json

    a = _Composite(desired=state, actual=dict(state))
    assert a._actual_value() == json.dumps(state, sort_keys=True)
    assert a.actual() == {json.dumps(state, sort_keys=True)}
