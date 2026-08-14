"""Dropping a block must not delete a file a package ships.

Found by the round-I strip (thirty optional blocks dropped in one apply):

    - [files] delete /etc/environment  (no longer declared)

`/etc/environment` is owned by `pam`. What the config did was OVERRIDE a file the
package ships; dropping the block should undo the override, not remove the path.
After the strip `pacman -Qkk pam` reports it missing, and it reappears with
package content on the next upgrade — a change nobody planned.

DropFilesAction already knows about package ownership (`_pacman_owner`) and uses
it to refuse to CAPTURE such files. The delete path never consulted it: dasik
would not own one on the way back, but removed one on the way out.
"""
import os
from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target

_OWNED = "/etc/environment"
_MINE = "/etc/profile.d/dasik.sh"


@pytest.fixture(autouse=True)
def _no_shadow_warnings(monkeypatch):
    """_warn_shadowed probes pacman too; the tests below pin the probe itself.

    The skip also logs through the process-wide run_logger — stub it, or this
    test writes to a capture stream another test already closed (which is how
    it fails only under the mutation gate's ordering).
    """
    monkeypatch.setattr(DropFilesAction, "_vendor_copy", lambda self, path: None)
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.run_logger.get",
                        lambda: MagicMock())


def _machine(tmp_path):
    (tmp_path / "etc/profile.d").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/environment").write_text("EDITOR=vim\n")
    (tmp_path / "etc/profile.d/dasik.sh").write_text("export FOO=1\n")
    return tmp_path


def _action(tmp_path, config, owners):
    action = DropFilesAction(config, ActionContext(target=Target(root=str(tmp_path))))
    action._pacman_owner = lambda path: owners.get(path)   # type: ignore[assignment]
    return action


def _plan(action, managed):
    return action.plan(managed=managed)


def test_the_delete_of_a_package_file_says_what_it_will_really_do(tmp_path):
    action = _action(_machine(tmp_path), {}, {_OWNED: "pam"})

    change = [c for c in _plan(action, [_OWNED]) if c.item == _OWNED][0]

    assert change.op is Op.DELETE
    assert "pam" in change.reason
    assert "left in place" in change.reason


def test_and_apply_leaves_the_package_file_alone(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {}, {_OWNED: "pam"})

    action.apply(_plan(action, [_OWNED]))

    assert (root / "etc/environment").exists()


def test_a_file_no_package_owns_is_still_deleted(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {}, {_MINE: None})

    action.apply(_plan(action, [_MINE]))

    assert not (root / "etc/profile.d/dasik.sh").exists()


def test_the_two_are_decided_one_by_one_in_the_same_apply(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {}, {_OWNED: "pam"})

    action.apply(_plan(action, [_OWNED, _MINE]))

    assert (root / "etc/environment").exists()
    assert not (root / "etc/profile.d/dasik.sh").exists()


def test_a_failed_probe_keeps_the_old_behaviour(tmp_path):
    """No pacman (or a probe that errors) must not turn deletes into no-ops."""
    root = _machine(tmp_path)
    action = _action(root, {}, {})

    action.apply(_plan(action, [_MINE]))

    assert not (root / "etc/profile.d/dasik.sh").exists()


def test_a_declared_package_file_is_still_written(tmp_path):
    """Overriding a package file stays legal — only the removal changes."""
    root = _machine(tmp_path)
    action = _action(root, {"etc_environment": ["EDITOR=nano"]}, {_OWNED: "pam"})

    action.apply(_plan(action, [_OWNED]))

    assert "nano" in (root / "etc/environment").read_text()
