"""dasik must not plan a removal pacman is going to refuse.

Found by driving the block-removal matrix in a VM: dropping the `apparmor`
block plans `- [packages] remove audit`, and `audit` cannot be removed from an
Arch system at all — `pam`, `systemd`, `shadow`, `dbus` and `networkmanager` all
require it. `pacman -Rns` fails the whole transaction, the apply aborts before
any other domain runs, and the same plan comes back forever:

    error: failed to prepare transaction (could not satisfy dependencies)

The machine kept `lsm=`, `audit=1` and a running apparmor.service through three
applies that all "planned" to remove them.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _qi(entries):
    """A `pacman -Qi` answer: [(name, required_by), …]."""
    blocks = []
    for name, required in entries:
        blocks.append(f"Name            : {name}\n"
                      f"Version         : 1-1\n"
                      f"Required By     : {required}\n")
    return MagicMock(stdout="\n".join(blocks).encode(), returncode=0)


def _plan(managed, desired, qi, installed=None):
    action = PackagesAction({"packages": list(desired)},
                            ActionContext(target=Target(root="/")))
    installed = set(installed if installed is not None else set(managed) | set(desired))
    action._installed_all = MagicMock(return_value=installed)
    action.actual = MagicMock(return_value=installed)
    with patch("dasik.lib.actions.packages_action.Command.execute", return_value=qi):
        return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


def test_a_package_nothing_needs_is_removed():
    assert ("REMOVE", "htop") in _plan(
        managed=["htop"], desired=[], qi=_qi([("htop", "None")]))


def test_a_package_the_system_still_needs_is_not_planned():
    """`audit` is required by pam. Planning its removal is planning a failure."""
    planned = _plan(managed=["audit"], desired=[],
                    qi=_qi([("audit", "pam  systemd  shadow")]))

    assert planned == []


def test_a_dependency_of_something_else_being_removed_is_still_planned():
    """apparmor requires audit; removing BOTH is a transaction pacman accepts."""
    planned = _plan(managed=["apparmor", "audit"], desired=[],
                    qi=_qi([("apparmor", "None"), ("audit", "apparmor")]))

    assert sorted(planned) == [("REMOVE", "apparmor"), ("REMOVE", "audit")]


def test_a_mixed_set_keeps_the_removable_half():
    """The failure mode this fixes: one impossible name took the whole
    transaction — and every other domain of the apply — down with it."""
    planned = _plan(managed=["apparmor", "audit", "tk"], desired=[],
                    qi=_qi([("apparmor", "None"), ("audit", "pam"), ("tk", "None")]))

    assert sorted(planned) == [("REMOVE", "apparmor"), ("REMOVE", "tk")]


def test_the_skipped_removal_is_reported_not_swallowed():
    from dasik.lib.logging import run_logger

    warnings = []
    logger = MagicMock()
    logger.warning = lambda msg, **kw: warnings.append(msg)
    with patch.object(run_logger, "get", return_value=logger):
        _plan(managed=["audit"], desired=[], qi=_qi([("audit", "pam")]))

    assert any("audit" in w and "pam" in w for w in warnings)


def test_a_probe_that_cannot_answer_leaves_the_plan_alone():
    """No pacman to ask: plan it and let the tool refuse, exactly as before."""
    action = PackagesAction({"packages": []}, ActionContext(target=Target(root="/")))
    action._installed_all = MagicMock(return_value={"htop"})
    action.actual = MagicMock(return_value={"htop"})
    with patch("dasik.lib.actions.packages_action.Command.execute",
               side_effect=OSError("no pacman")):
        planned = [(c.op.name, c.item) for c in action.plan(managed=["htop"])]

    assert planned == [("REMOVE", "htop")]


def test_a_package_that_is_not_installed_needs_no_probe():
    """Owned but already gone: there is nothing to refuse."""
    planned = _plan(managed=["ghost"], desired=[], qi=_qi([]), installed=set())

    assert planned == [("REMOVE", "ghost")]
