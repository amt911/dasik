"""A lock left behind by a crash must be named, not just hit.

Found by pulling the plug on a VM in the middle of an apply. The machine came
back with /var/lib/pacman/db.lck still there — pacman never got to remove it —
and every apply after that died with pacman's own line:

    error: could not lock database: File exists
    error: apply failed: pacman failed (exit 1)

which says nothing about which file, why it is there, or that removing it is
the fix. On a machine that has just crashed mid-install, that is exactly the
moment the message has to be legible.

dasik does not delete it: a lock can also mean a pacman is genuinely running,
and guessing wrong there corrupts a package database.
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.packages_action import PackagesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _action(tmp_path, locked, running):
    action = PackagesAction({"packages": ["htop"]},
                            ActionContext(target=Target(root=str(tmp_path))))
    (tmp_path / "var/lib/pacman").mkdir(parents=True, exist_ok=True)
    if locked:
        (tmp_path / "var/lib/pacman/db.lck").write_text("")
    action._pacman_is_running = lambda: running
    action._installed_all = MagicMock(return_value=set())
    action.actual = MagicMock(return_value=set())
    return action


def _apply(action, changes=(Change("packages", Op.INSTALL, "htop"),)):
    with patch("dasik.lib.actions.packages_action.Command.execute",
               MagicMock(return_value=MagicMock(returncode=0, stdout=b""))), \
         patch.object(PackagesAction, "_resolve_sources") as resolve:
        resolve.return_value = MagicMock(unavailable=[], unknown=[], groups=[],
                                         aur=[], git=[], repo=["htop"])
        action.apply(list(changes))


def test_a_stale_lock_is_named_and_explained(tmp_path):
    action = _action(tmp_path, locked=True, running=False)

    with pytest.raises(CommandExecutionError) as err:
        _apply(action)

    message = str(err.value)
    assert "/var/lib/pacman/db.lck" in message
    assert "interrupted" in message.lower()


def test_a_lock_a_running_pacman_holds_says_that_instead(tmp_path):
    action = _action(tmp_path, locked=True, running=True)

    with pytest.raises(CommandExecutionError, match="already running"):
        _apply(action)


def test_dasik_never_deletes_the_lock_itself(tmp_path):
    """Guessing wrong about a live pacman corrupts a package database."""
    action = _action(tmp_path, locked=True, running=False)

    with pytest.raises(CommandExecutionError):
        _apply(action)

    assert (tmp_path / "var/lib/pacman/db.lck").exists()


def test_an_unlocked_machine_installs_as_before(tmp_path):
    action = _action(tmp_path, locked=False, running=False)

    _apply(action)      # no exception


def test_no_package_work_means_no_lock_check(tmp_path):
    """A plan with nothing for pacman must not fail on somebody else's lock."""
    action = _action(tmp_path, locked=True, running=True)

    _apply(action, changes=())
