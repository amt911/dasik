"""Property-based idempotency for SystemdAction (CLAUDE.md § Quality).

SystemdAction.plan reconciles declared units against the set of enabled units
via the same D/M/A/F set-math as packages, with forced disables. Invariants:
when the enabled set already matches what's declared+owned, planning is empty; a
unit that is enabled but neither declared nor owned is drift and is never
DISABLEd; a forced (disable_units) unit that is enabled is DISABLEd.
"""
from types import SimpleNamespace

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.systemd_action import SystemdAction
from dasik.lib.state.change import Op

# unit names; ".socket" suffix is only meaningful to import_state, not plan.
_unit = st.builds(lambda s: s + ".service", st.text(alphabet="abcde", min_size=1, max_size=4))
_units = st.lists(_unit, max_size=5, unique=True)


def _action(enable=(), disable=(), enabled_on_system=()):
    a = SystemdAction(
        {"enable_units": list(enable), "disable_units": list(disable)},
        context=SimpleNamespace(target=object()),
    )
    a.actual = lambda: set(enabled_on_system)
    return a


@given(units=_units)
def test_converged_systemd_plan_is_empty(units):
    """Declared units all enabled, managed == declared, nothing forced ⇒ no-op."""
    a = _action(enable=units, enabled_on_system=units)
    assert a.plan(managed=list(units)) == []


@given(declared=_units, extra=_units)
def test_undeclared_enabled_unit_is_not_disabled(declared, extra):
    """An enabled unit that is neither declared nor owned is drift — never DISABLEd."""
    strangers = set(extra) - set(declared)
    a = _action(enable=declared, enabled_on_system=set(declared) | strangers)
    changes = a.plan(managed=list(declared))
    disabled = {c.item for c in changes if c.op is Op.DISABLE}
    assert disabled.isdisjoint(strangers)


@given(declared=_units, managed=_units, enabled=_units)
def test_plan_only_disables_owned_or_forced(declared, managed, enabled):
    """Every DISABLE targets an owned-but-undeclared unit (M\\D) or a forced one;
    dasik never disables a unit it does not own and did not declare off."""
    a = _action(enable=declared, enabled_on_system=enabled)
    changes = a.plan(managed=list(managed))
    disabled = {c.item for c in changes if c.op is Op.DISABLE}
    allowed = (set(managed) - set(declared))  # no forced in this scenario
    assert disabled <= allowed


@given(declared=_units, off=_units)
def test_forced_off_enabled_unit_is_disabled(declared, off):
    """A disable_units entry that is currently enabled is DISABLEd (forced),
    and declared-on units are excluded from the forced set by precondition."""
    off = [u for u in off if u not in declared]  # D ∩ F = ∅ precondition
    enabled = set(declared) | set(off)
    a = _action(enable=declared, disable=off, enabled_on_system=enabled)
    changes = a.plan(managed=list(declared))
    disabled = {c.item for c in changes if c.op is Op.DISABLE}
    for u in off:
        assert u in disabled
