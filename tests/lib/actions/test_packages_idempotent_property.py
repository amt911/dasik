"""Property-based idempotency for PackagesAction (CLAUDE.md § Quality).

set_math is proven idempotent in isolation (test_set_math_properties.py); this
proves the property survives the trip through a *real* v3 action. PackagesAction
.plan() reads reality via `pacman -Qq` / `pacman -Qqe` (mocked here) and runs the
same D/M/A set-math. The invariant: when the system already matches the config,
the plan is empty, and an undeclared-but-installed package is never removed.
"""
from types import SimpleNamespace
from unittest.mock import patch

from tests.support.pacman import pacman_double

from hypothesis import given
from hypothesis import strategies as st

from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op

# Lowercase names, none of which start with the ``aur-`` prefix, so every
# generated package is treated as a plain pacman package.
_names = st.text(alphabet="abcde", min_size=1, max_size=3)
_sets = st.sets(_names, max_size=6)


def _action_with_system(desired, installed, explicit):
    """Build a PackagesAction whose mocked target reports `installed` (pacman
    -Qq) and `explicit` (pacman -Qqe). Returns (action, patcher-context)."""
    action = PackagesAction(sorted(desired), context=SimpleNamespace(target=object()))

    # Nothing is satisfied through a provider here, and nothing is a group:
    # the double says so rather than leaving those questions to a catch-all.
    fake_execute = pacman_double(installed=sorted(installed),
                                 explicit=sorted(explicit))

    return action, patch(
        "dasik.lib.actions.packages_action.Command.execute",
        side_effect=fake_execute,
    )


@given(pkgs=_sets)
def test_converged_packages_plan_is_empty(pkgs):
    """desired == managed == installed(explicit) ⇒ plan() is empty (no-op re-run)."""
    action, patcher = _action_with_system(desired=pkgs, installed=pkgs, explicit=pkgs)
    with patcher:
        changes = action.plan(managed=sorted(pkgs))
    assert changes == []


@given(declared=_sets, extra=_sets)
def test_undeclared_installed_package_is_not_removed(declared, extra):
    """An installed package that is neither declared nor managed is drift —
    PackagesAction.plan() must never emit a REMOVE for it (the safety property)."""
    undeclared = set(extra) - set(declared)
    installed = set(declared) | undeclared
    action, patcher = _action_with_system(
        desired=declared, installed=installed, explicit=installed
    )
    with patcher:
        changes = action.plan(managed=sorted(declared))
    removed = {c.item for c in changes if c.op is Op.REMOVE}
    assert removed.isdisjoint(undeclared)


@given(declared=_sets, managed=_sets, installed=_sets)
def test_plan_only_removes_managed_and_undeclared(declared, managed, installed):
    """Every REMOVE the action emits targets an item in (managed \\ desired) — it
    never removes something dasik does not own, no matter the system state."""
    action, patcher = _action_with_system(
        desired=declared, installed=installed, explicit=installed
    )
    with patcher:
        changes = action.plan(managed=sorted(managed))
    removed = {c.item for c in changes if c.op is Op.REMOVE}
    assert removed <= (set(managed) - set(declared))
