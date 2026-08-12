"""What a user can leave in their own $HOME, and dasik writes there as root.

`~/.config` belongs to the user; dasik writes into it with root's privileges.
So a symlink planted where dasik is about to write is not a curiosity, it is a
way to have root put content into a file the user could never touch. Found by
the VM gauntlet, together with its quieter sibling: a parent path that is a
regular file, which aborted the whole apply with a raw

    [Errno 17] File exists: '/home/test/.config/dasik'
"""
import os

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.home_files_action import HomeFilesAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.target.target import Target


_CFG = {"home_files": [{"user": "u", "path": ".config/x.conf", "content": "mine\n"}]}


def _action(tmp_path):
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        f"u:x:{os.getuid()}:{os.getgid()}::/home/u:/bin/bash\n")
    return HomeFilesAction(_CFG, ActionContext(target=Target(root=str(tmp_path))))


def test_a_planted_symlink_is_replaced_not_followed(tmp_path):
    action = _action(tmp_path)
    (tmp_path / "home/u/.config").mkdir(parents=True)
    victim = tmp_path / "etc/shadow"
    victim.write_text("root:$6$hash:20000:::::\n")
    os.symlink(victim, tmp_path / "home/u/.config/x.conf")

    action.apply(action.plan(managed=[]))

    assert victim.read_text() == "root:$6$hash:20000:::::\n"   # untouched
    written = tmp_path / "home/u/.config/x.conf"
    assert not written.is_symlink()
    assert written.read_text() == "mine\n"


def test_the_symlink_shows_up_in_the_plan(tmp_path):
    """Even pointing at a file whose content already matches: a link is not the
    file, and writing through it would never converge."""
    action = _action(tmp_path)
    (tmp_path / "home/u/.config").mkdir(parents=True)
    (tmp_path / "decoy").write_text("mine\n")
    os.symlink(tmp_path / "decoy", tmp_path / "home/u/.config/x.conf")

    changes = action.plan(managed=[])

    assert [c.op.name for c in changes] == ["MODIFY"]
    assert "symlink" in changes[0].reason


def test_and_then_it_converges(tmp_path):
    action = _action(tmp_path)
    (tmp_path / "home/u/.config").mkdir(parents=True)
    os.symlink("/dev/null", tmp_path / "home/u/.config/x.conf")

    action.apply(action.plan(managed=[]))

    assert action.plan(managed=[]) == []


def test_a_symlinked_DIRECTORY_on_the_way_is_refused(tmp_path):
    """`~/.config -> /etc` would have every home file land in /etc."""
    action = _action(tmp_path)
    (tmp_path / "home/u").mkdir(parents=True)
    os.symlink(tmp_path / "etc", tmp_path / "home/u/.config")

    with pytest.raises(CommandExecutionError, match=r"\.config"):
        action.apply(action.plan(managed=[]))
    assert not (tmp_path / "etc/x.conf").exists()


def test_a_parent_that_is_a_regular_file_says_which_one(tmp_path):
    action = _action(tmp_path)
    (tmp_path / "home/u").mkdir(parents=True)
    (tmp_path / "home/u/.config").write_text("not a directory\n")

    with pytest.raises(CommandExecutionError, match=r"\.config"):
        action.apply(action.plan(managed=[]))
