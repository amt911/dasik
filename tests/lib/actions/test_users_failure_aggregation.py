"""One impossible user must not hide the others.

Same shape as the systemd-unit fix: `apply` walked the list with check=True, so
the first `useradd`/`usermod` failure aborted and every later user stayed
unknown — one broken user per apply, and an apply is a whole install.

The sequence WITHIN a user stays atomic: a failed `useradd` must still stop
before `usermod -p` sets a password on a user that was never created.
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.users_action import UsersAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target


def _users(*names):
    return {"users": [{"username": n, "hashed_password": f"$y$hash-{n}",
                       "shell": "/bin/bash", "groups": []} for n in names]}


def _action(*names):
    return UsersAction(_users(*names), ActionContext(target=Target(root="/mnt")))


def _fail_for(*bad):
    def side(cmd, args, **kw):
        if args and args[-1] in bad:
            raise CommandExecutionError(f"{cmd} failed (exit 1)")
        return None
    return side


def test_a_failing_user_does_not_stop_the_next_one():
    a = _action("alice", "broken", "zoe")
    changes = [Change("users", Op.CREATE, n) for n in ("alice", "broken", "zoe")]
    with patch("dasik.lib.actions.users_action.Command.execute",
               side_effect=_fail_for("broken")) as run:
        with pytest.raises(CommandExecutionError):
            a.apply(changes)
    attempted = {c.args[1][-1] for c in run.call_args_list}
    assert {"alice", "broken", "zoe"} <= attempted


def test_the_error_names_every_user_that_failed():
    a = _action("alice", "broken", "alsobroken")
    changes = [Change("users", Op.CREATE, n)
               for n in ("alice", "broken", "alsobroken")]
    with patch("dasik.lib.actions.users_action.Command.execute",
               side_effect=_fail_for("broken", "alsobroken")):
        with pytest.raises(CommandExecutionError) as excinfo:
            a.apply(changes)
    message = str(excinfo.value)
    assert "broken" in message and "alsobroken" in message
    assert "alice" not in message


def test_a_failed_useradd_still_skips_that_user_s_usermod():
    """No password may be set on a user that was never created."""
    a = _action("broken")
    with patch("dasik.lib.actions.users_action.Command.execute",
               side_effect=_fail_for("broken")) as run:
        with pytest.raises(CommandExecutionError):
            a.apply([Change("users", Op.CREATE, "broken")])
    commands = [c.args[0] for c in run.call_args_list]
    assert commands == ["useradd"], commands


def test_nothing_raised_when_every_user_works():
    a = _action("alice", "zoe")
    with patch("dasik.lib.actions.users_action.Command.execute"):
        a.apply([Change("users", Op.CREATE, n) for n in ("alice", "zoe")])


def test_deletes_are_aggregated_too():
    a = _action("alice")
    changes = [Change("users", Op.DELETE, n) for n in ("gone", "alsogone")]
    with patch("dasik.lib.actions.users_action.Command.execute",
               side_effect=_fail_for("gone", "alsogone")):
        with pytest.raises(CommandExecutionError) as excinfo:
            a.apply(changes)
    assert "gone" in str(excinfo.value) and "alsogone" in str(excinfo.value)
