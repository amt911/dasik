"""When the parent of a managed file cannot hold it, say which one and why.

`os.makedirs` fails with a bare errno when any component of the path is not a
usable directory, and the whole apply dies naming nothing:

    FileExistsError: [Errno 17] File exists: '/mnt/etc/sysctl.d'

That message is a lie by omission — the path exists, it just is not a directory
dasik can write into (a dangling symlink here; a regular file in the way in the
other common case). $HOME already got this treatment in the home_files domain;
/etc had not.

A symlinked parent that RESOLVES is left alone on purpose: pointing /etc/*.d at
another filesystem is a real thing people do (impermanence setups), and writing
through it is what they asked for.
"""
import os

from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.exceptions.exceptions import ConfigValidationError
from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(DropFilesAction, "_vendor_copy", lambda self, path: None)
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.run_logger.get",
                        lambda: MagicMock())


def _action(tmp_path):
    action = DropFilesAction({"sysctl_d": [{"name": "99-dasik.conf", "content": "x\n"}]},
                             ActionContext(target=Target(root=str(tmp_path))))
    action._pacman_owner = lambda path: None      # type: ignore[assignment]
    return action


def test_a_dangling_symlink_parent_is_explained(tmp_path):
    (tmp_path / "etc").mkdir()
    os.symlink("/nowhere", tmp_path / "etc/sysctl.d")
    action = _action(tmp_path)

    with pytest.raises(ConfigValidationError) as exc:
        action.apply(action.plan(managed=[]))

    message = str(exc.value)
    assert "/etc/sysctl.d" in message
    assert "symlink" in message and "/nowhere" in message


def test_a_regular_file_in_the_way_is_explained(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/sysctl.d").write_text("somebody put a file here")
    action = _action(tmp_path)

    with pytest.raises(ConfigValidationError) as exc:
        action.apply(action.plan(managed=[]))

    assert "/etc/sysctl.d" in str(exc.value)
    assert "not a directory" in str(exc.value)


def test_a_symlink_that_resolves_is_written_through(tmp_path):
    """Deliberate: /etc/*.d pointed at another filesystem is a real setup."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "opt/elsewhere").mkdir(parents=True)
    os.symlink("../opt/elsewhere", tmp_path / "etc/sysctl.d")
    action = _action(tmp_path)

    action.apply(action.plan(managed=[]))

    assert (tmp_path / "opt/elsewhere/99-dasik.conf").read_text() == "x\n"


def test_the_ordinary_case_still_just_works(tmp_path):
    (tmp_path / "etc").mkdir()
    action = _action(tmp_path)

    action.apply(action.plan(managed=[]))

    assert (tmp_path / "etc/sysctl.d/99-dasik.conf").read_text() == "x\n"
