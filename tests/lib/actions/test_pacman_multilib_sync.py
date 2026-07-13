"""Enabling [multilib] must sync the pacman DB, or lib32 installs fail.

When PacmanAction enables the [multilib] repo in pacman.conf, the new repo has
no synced database yet — a later `pacman -S lib32-...` (e.g. Steam / the 32-bit
driver libs added by expand_drivers) aborts with "could not find database".
PacmanAction.apply() therefore runs `pacman -Sy` in the target, but ONLY when
the conf actually drifted (non-empty changes) — the reconciler calls apply() for
every action even with no changes, so an unconditional -Sy would hit the network
on every idempotent re-run.
"""
from unittest.mock import patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(tmp_path, multilib, root=None):
    cfg = {"options": {"Parallel": True, "Color": True, "VerbosePkgLists": False},
           "multilib": multilib}
    ctx = ActionContext(target=Target(root=str(root or tmp_path)))
    return PacmanAction(cfg, ctx)


_CHANGE = [Change("pacman", Op.MODIFY, "multilib", reason="config")]


def _sy_calls(mock):
    return [c for c in mock.call_args_list
            if c.args[:2] == ("pacman", ["-Sy"])]


def _apply(action, changes):
    # Isolate the -Sy logic: stub the conf write (_set_value) so no real
    # /etc/pacman.conf is ever touched, and capture Command.execute calls.
    with patch.object(PacmanAction, "_set_value"), \
         patch("dasik.lib.actions.pacman_action.Command.execute") as ex:
        action.apply(changes)
    return ex


def test_multilib_enable_syncs_db(tmp_path):
    ex = _apply(_action(tmp_path, multilib=True), _CHANGE)
    assert _sy_calls(ex), "expected `pacman -Sy` after enabling multilib"


def test_no_changes_no_sync(tmp_path):
    # Idempotent re-run: reconciler still calls apply([]), must not hit -Sy.
    ex = _apply(_action(tmp_path, multilib=True), [])
    assert not _sy_calls(ex)


def test_multilib_off_no_sync(tmp_path):
    ex = _apply(_action(tmp_path, multilib=False), _CHANGE)
    assert not _sy_calls(ex)


def test_host_target_no_sync(tmp_path):
    # On the live host (root="/", not a chroot install target) we don't -Sy.
    ex = _apply(_action(tmp_path, multilib=True, root="/"), _CHANGE)
    assert not _sy_calls(ex)
