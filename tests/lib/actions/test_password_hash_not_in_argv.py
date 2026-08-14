"""A password hash must not travel on the command line.

`usermod -p <hash> <user>` puts the hash in the process's argv, where every local
user can read it out of `ps` or /proc for as long as the call runs. usermod's own
man page says so:

    Note: This option is not recommended because the password (or encrypted
    password) will be visible by users listing the processes.

It is a hash and not a plaintext password, which is why this is hardening rather
than a breach — but it is a hash of the user's real password, handed to anyone
with a shell on the machine during an install or a day-2 apply.

`chpasswd -e` takes `user:hash` on stdin instead, which dasik's Command already
supports (it feeds cryptsetup the same way).
"""
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.users_action import UsersAction
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

_HASH = "$6$rounds=656000$dasik$IgNoReMe"


def _action(tmp_path, users):
    return UsersAction({"users": users}, ActionContext(target=Target(root=str(tmp_path))))


def _calls(tmp_path, users, changes):
    action = _action(tmp_path, users)
    with patch("dasik.lib.actions.users_action.Command.execute") as execute:
        execute.return_value = MagicMock(returncode=0)
        action.apply(changes)
    return execute.call_args_list


def _argv_blob(calls):
    return " ".join(str(c.args) for c in calls)


def test_creating_a_user_keeps_the_hash_off_the_command_line(tmp_path):
    users = [{"username": "test", "hashed_password": _HASH, "groups": [], "shell": "/bin/bash"}]

    calls = _calls(tmp_path, users, [Change("users", Op.CREATE, "test")])

    assert _HASH not in _argv_blob(calls), "the hash is visible in `ps`"
    fed = [c for c in calls if c.kwargs.get("input")]
    assert fed, "the hash has to reach the system somehow"
    assert b"test:" + _HASH.encode() in fed[0].kwargs["input"]


def test_changing_a_password_keeps_it_off_too(tmp_path):
    users = [{"username": "test", "hashed_password": _HASH, "groups": [], "shell": "/bin/bash"}]

    calls = _calls(tmp_path, users, [Change("users", Op.MODIFY, "test")])

    assert _HASH not in _argv_blob(calls)


def test_the_root_password_is_no_different(tmp_path):
    users = [{"username": "root", "hashed_password": _HASH}]

    calls = _calls(tmp_path, users, [Change("users", Op.MODIFY, "root")])

    assert _HASH not in _argv_blob(calls)


def test_the_user_is_still_created_with_shell_and_groups(tmp_path):
    users = [{"username": "test", "hashed_password": _HASH,
              "groups": ["wheel"], "shell": "/bin/zsh"}]

    calls = _calls(tmp_path, users, [Change("users", Op.CREATE, "test")])

    blob = _argv_blob(calls)
    assert "useradd" in blob and "/bin/zsh" in blob and "wheel" in blob
