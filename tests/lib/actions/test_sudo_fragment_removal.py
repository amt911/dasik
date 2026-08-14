"""Dropping the sudo declaration must take the sudo away.

`/etc/sudoers.d/10-dasik` is what makes `%wheel` work on Arch — the stock
/etc/sudoers ships that line commented out. dasik writes the fragment when a
`sudo` block is declared, and also when no block is declared but a user is in
`wheel` (that implicit default is deliberate).

What did not happen is the other direction. Remove the block AND take the last
user out of `wheel`, and the fragment stays on the machine:

    $ dasik plan
    No changes - system matches config.
    $ cat /etc/sudoers.d/10-dasik
    %wheel ALL=(ALL:ALL) ALL

Nothing in the config grants sudo any more, and sudo is still granted. Same
shape as the zram leftover, with a sharper edge.
"""
import os

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.sudo_action import SudoAction
from dasik.lib.target.target import Target

_FRAGMENT = "%wheel ALL=(ALL:ALL) ALL"
_PATH = "etc/sudoers.d/10-dasik"


def _machine(tmp_path, body=f"# Managed by dasik\n{_FRAGMENT}\n"):
    (tmp_path / "etc/sudoers.d").mkdir(parents=True, exist_ok=True)
    if body is not None:
        (tmp_path / _PATH).write_text(body)
    return tmp_path


def _action(tmp_path, config):
    return SudoAction(config, ActionContext(target=Target(root=str(tmp_path))))


def test_nothing_declares_sudo_any_more_so_the_fragment_goes(tmp_path):
    action = _action(_machine(tmp_path), {"users": [{"username": "u", "groups": []}]})

    changes = action.plan(managed=[_FRAGMENT])

    assert [c.op.name for c in changes] == ["REMOVE"]
    assert "no longer declared" in changes[0].reason


def test_and_apply_takes_it_away(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {"users": [{"username": "u", "groups": []}]})

    action.apply(action.plan(managed=[_FRAGMENT]))

    assert not (root / _PATH).exists()
    assert action.plan(managed=[]) == []


def test_a_user_still_in_wheel_keeps_it(tmp_path):
    """The implicit default: no block, but somebody is in wheel."""
    action = _action(_machine(tmp_path), {"users": [{"username": "u", "groups": ["wheel"]}]})

    assert action.plan(managed=[_FRAGMENT]) == []


def test_a_declared_block_keeps_it(tmp_path):
    action = _action(_machine(tmp_path), {"sudo": {"wheel": True}, "users": []})

    assert action.plan(managed=[_FRAGMENT]) == []


def test_an_explicit_wheel_false_also_removes_it(tmp_path):
    """`{"wheel": false}` is a declaration too: it says do not grant this."""
    action = _action(_machine(tmp_path), {"sudo": {"wheel": False}, "users": []})

    assert [c.op.name for c in action.plan(managed=[_FRAGMENT])] == ["REMOVE"]


def test_a_fragment_dasik_never_wrote_is_left_alone(tmp_path):
    action = _action(_machine(tmp_path), {"users": []})

    assert action.plan(managed=[]) == []


def test_nothing_to_remove_when_the_file_is_gone(tmp_path):
    action = _action(_machine(tmp_path, body=None), {"users": []})

    assert action.plan(managed=[_FRAGMENT]) == []
