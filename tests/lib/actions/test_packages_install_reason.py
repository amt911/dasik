"""Install reasons must be right when THIS apply finishes (issue #188).

The plan is computed before anything runs, so it cannot know that pacman will
mark a declared package as a dependency — `audit` arrives as a dependency of
`apparmor`, and the plan that could have said so was written minutes earlier.
The result was an `apply` that exits 0 and leaves work for the next one:

    ~ [packages] modify audit  (install reason)

It converges, which is exactly why nobody looked: two applies in a row were not
a no-op, and "apply twice changes nothing" is the promise the whole tool rests
on.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(config, installed, explicit):
    a = PackagesAction(config, ActionContext(target=Target(root="/")))
    a._installed_all = MagicMock(return_value=set(installed))     # type: ignore
    a.actual = MagicMock(return_value=set(explicit))              # type: ignore
    return a


def _pacman_calls(fake):
    return [c.args[1] for c in fake.call_args_list if c.args[0] == "pacman"]


def _reason_calls(fake):
    return [args for args in _pacman_calls(fake) if args[:1] == ["-D"]]


def _apply(action, changes):
    fake = MagicMock(return_value=MagicMock(returncode=0, stdout=b"", stderr=b""))
    with patch("dasik.lib.actions.packages_action.Command.execute", fake), \
         patch.object(PackagesAction, "_resolve_sources") as resolve:
        resolve.return_value = MagicMock(
            unavailable=[], unknown=[], groups=[], aur=[], git=[],
            repo=[c.item for c in changes if c.op is Op.INSTALL])
        action.apply(changes)
    return fake


def test_a_declared_package_pacman_marked_as_a_dep_is_fixed_in_this_apply():
    """The bug: `audit` comes in as a dependency of `apparmor`, and the plan
    written before the transaction could not have said so."""
    action = _action({"packages": ["apparmor", "audit"]},
                     installed={"apparmor", "audit"}, explicit={"apparmor"})

    fake = _apply(action, [Change("packages", Op.INSTALL, "apparmor")])

    assert _reason_calls(fake) == [["-D", "--asexplicit", "audit"]]


def test_a_second_apply_would_have_nothing_left_to_do():
    """The property that was broken: apply -> apply is a no-op."""
    action = _action({"packages": ["apparmor", "audit"]},
                     installed={"apparmor", "audit"}, explicit={"apparmor", "audit"})

    assert [c for c in action.plan(managed=["apparmor", "audit"])] == []


def test_nothing_is_touched_when_the_reasons_already_match():
    action = _action({"packages": ["apparmor", "audit"]},
                     installed={"apparmor", "audit"}, explicit={"apparmor", "audit"})

    fake = _apply(action, [Change("packages", Op.INSTALL, "apparmor")])

    assert _reason_calls(fake) == []


def test_a_package_declared_as_a_dependency_that_is_explicit_is_corrected():
    action = _action({"packages": ["firefox", {"name": "linux-headers", "reason": "dep"}]},
                     installed={"firefox", "linux-headers"},
                     explicit={"firefox", "linux-headers"})

    fake = _apply(action, [Change("packages", Op.INSTALL, "firefox")])

    assert _reason_calls(fake) == [["-D", "--asdeps", "linux-headers"]]


def test_a_package_nobody_declared_is_left_alone():
    """Somebody else's explicit package is not dasik's reason to change."""
    action = _action({"packages": ["firefox"]},
                     installed={"firefox", "htop"}, explicit={"firefox"})

    fake = _apply(action, [Change("packages", Op.INSTALL, "firefox")])

    assert _reason_calls(fake) == []


def test_a_declared_package_that_is_not_installed_is_not_marked():
    """An optional package whose install failed must not be claimed."""
    action = _action({"packages": ["firefox", "sunshine"]},
                     installed={"firefox"}, explicit={"firefox"})

    fake = _apply(action, [Change("packages", Op.INSTALL, "firefox")])

    assert _reason_calls(fake) == []


def test_the_plans_own_reason_modify_is_still_honoured():
    """The pre-existing path: a MODIFY the plan did see."""
    action = _action({"packages": [{"name": "htop", "reason": "dep"}]},
                     installed={"htop"}, explicit={"htop"})

    fake = _apply(action, [Change("packages", Op.MODIFY, "htop", reason="install reason")])

    assert _reason_calls(fake) == [["-D", "--asdeps", "htop"]]


@pytest.mark.parametrize("op,item", [(Op.REMOVE, "htop")])
def test_reasons_are_reconciled_even_when_the_apply_only_removed(op, item):
    """A removal can orphan nothing, but it can free a name whose reason then
    matters; the check is cheap and reality-based either way."""
    action = _action({"packages": ["firefox"]},
                     installed={"firefox"}, explicit=set())

    fake = _apply(action, [Change("packages", op, item)])

    assert _reason_calls(fake) == [["-D", "--asexplicit", "firefox"]]
