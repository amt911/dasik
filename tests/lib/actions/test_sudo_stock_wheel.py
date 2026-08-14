"""A machine whose sudo comes from stock /etc/sudoers must capture silent.

`SudoAction.import_state` deliberately captures `sudo: {wheel: true}` when the
target has no dasik fragment but /etc/sudoers itself grants wheel — otherwise a
captured config would reproduce a machine where sudo does not work.

The plan side never learned about that second source, so the capture of any
hand-rolled machine (uncomment `%wheel` in /etc/sudoers — what everyone does)
came back with a change waiting on it:

    $ sudo dasik sync mysystem.json
    $ dasik plan mysystem.json
    ~ [sudo] set

sync -> plan must be silent. Nothing was wrong with that machine: it grants
exactly what the config asks for, by the other of the two supported routes.
"""
import os

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.sudo_action import SudoAction
from dasik.lib.target.target import Target

_STOCK_GRANTS = "root ALL=(ALL:ALL) ALL\n%wheel ALL=(ALL:ALL) ALL\n"
_STOCK_SILENT = "root ALL=(ALL:ALL) ALL\n# %wheel ALL=(ALL:ALL) ALL\n"


def _root(tmp_path, sudoers=_STOCK_GRANTS):
    (tmp_path / "etc/sudoers.d").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/sudoers").write_text(sudoers)
    return tmp_path


def _action(tmp_path, config):
    return SudoAction(config, ActionContext(target=Target(root=str(tmp_path))))


def test_stock_sudoers_already_grants_it_so_nothing_is_planned(tmp_path):
    action = _action(_root(tmp_path), {"sudo": {"wheel": True}})

    assert action.plan(managed=[]) == []


def test_the_implicit_wheel_default_is_satisfied_by_it_too(tmp_path):
    action = _action(_root(tmp_path), {"users": [{"username": "u", "groups": ["wheel"]}]})

    assert action.plan(managed=[]) == []


def test_the_capture_of_such_a_machine_replans_to_nothing(tmp_path):
    """The round trip the rule is about: sync -> plan, silent."""
    root = _root(tmp_path)
    config = {"users": [{"username": "u", "groups": ["wheel"]}]}

    captured = _action(root, config).import_state(managed=[])

    assert captured == {"sudo": {"wheel": True, "nopasswd": False, "rules": []}}
    assert _action(root, {**config, **captured}).plan(managed=[]) == []


def test_a_commented_out_wheel_grants_nothing(tmp_path):
    action = _action(_root(tmp_path, _STOCK_SILENT), {"sudo": {"wheel": True}})

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_nopasswd_is_not_satisfied_by_the_stock_grant(tmp_path):
    """Stock wheel asks for a password; NOPASSWD is a different rule."""
    action = _action(_root(tmp_path), {"sudo": {"wheel": True, "nopasswd": True}})

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_extra_rules_are_not_satisfied_by_the_stock_grant(tmp_path):
    action = _action(_root(tmp_path), {"sudo": {"wheel": True, "rules": ["u ALL=(ALL) /usr/bin/pacman"]}})

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_a_fragment_that_drifted_is_still_repaired(tmp_path):
    """The stock grant must not mask real drift in dasik's own fragment."""
    root = _root(tmp_path)
    (root / "etc/sudoers.d/10-dasik").write_text("%wheel ALL=(ALL) NOPASSWD: ALL\n")
    action = _action(root, {"sudo": {"wheel": True}})

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]


def test_the_removal_half_still_fires(tmp_path):
    """Stock grant or not, an owned fragment nothing declares any more goes."""
    root = _root(tmp_path)
    (root / "etc/sudoers.d/10-dasik").write_text("%wheel ALL=(ALL:ALL) ALL\n")
    action = _action(root, {"users": []})

    assert [c.op.name for c in action.plan(managed=["%wheel ALL=(ALL:ALL) ALL"])] == ["REMOVE"]


def test_a_stock_nopasswd_grant_is_looser_so_the_fragment_still_goes_in(tmp_path):
    """Silence would leave a passwordless root the config never asked for."""
    root = _root(tmp_path, "root ALL=(ALL:ALL) ALL\n%wheel ALL=(ALL) NOPASSWD: ALL\n")
    action = _action(root, {"sudo": {"wheel": True}})

    assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]
