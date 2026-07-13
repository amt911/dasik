"""Property-based idempotency for TimezoneAction (CLAUDE.md § Quality).

Exercises the real /etc/localtime symlink parsing: for any region/city, when the
symlink already points at zoneinfo/<region>/<city>, the plan is empty (no-op);
when it points elsewhere, exactly one MODIFY. Proves the scalar idempotency core
holds through timezone's actual concrete I/O parsing, not just the abstract base.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.timezone_action import TimezoneAction
from dasik.lib.state.change import Op

# zoneinfo path segments: letters/underscore, no '/'.
_seg = st.text(alphabet="ABCDEFGHIJKLMabcdefghijkl_", min_size=1, max_size=6)


def _localtime_symlink(target_str):
    """A fake Path whose readlink() points at target_str."""
    link = MagicMock()
    link.exists.return_value = True
    link.is_symlink.return_value = True
    link.readlink.return_value = SimpleNamespace(as_posix=lambda: target_str)
    return link


def _action(region, city):
    return TimezoneAction(
        {"region": region, "city": city},
        context=SimpleNamespace(target=None),
    )


@given(region=_seg, city=_seg)
def test_timezone_converged_is_a_noop(region, city):
    target = f"/usr/share/zoneinfo/{region}/{city}"
    a = _action(region, city)
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_localtime_symlink(target)):
        assert a._actual_value() == f"{region}/{city}"
        assert a.plan(managed=[]) == []
        assert a.is_needed() is False
        assert a.verify() is True


@given(region=_seg, city=_seg, other=_seg)
def test_timezone_drift_yields_one_modify(region, city, other):
    """When the current zone differs from the declared one, exactly one MODIFY."""
    if other == city:
        return
    current = f"/usr/share/zoneinfo/{region}/{other}"
    a = _action(region, city)
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_localtime_symlink(current)):
        changes = a.plan(managed=[])
        assert len(changes) == 1
        assert changes[0].op is Op.MODIFY
        assert changes[0].item == f"{region}/{city}"
        assert a.is_needed() is True


@given(region=_seg, city=_seg)
def test_timezone_relative_symlink_is_parsed(region, city):
    """Relative symlink targets (../usr/share/zoneinfo/...) parse the same way."""
    target = f"../usr/share/zoneinfo/{region}/{city}"
    a = _action(region, city)
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_localtime_symlink(target)):
        assert a._actual_value() == f"{region}/{city}"
        assert a.plan(managed=[]) == []
