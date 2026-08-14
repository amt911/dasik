"""Switching the initramfs generator must rebuild the image.

Found in a VM: on a machine installed with mkinitcpio, changing the config to
`"initramfs": "dracut"` installs dracut and neutralises mkinitcpio's pacman
hooks — and leaves `/boot/initramfs-linux.img` exactly as it was, same size and
same timestamp, built by the tool the config no longer declares. `plan` then
says *No changes*.

It boots, because the old image is still valid. It is still a machine whose
initramfs nobody has built the way the config says, reported as converged.
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.initramfs_action import InitramfsAction
from dasik.lib.target.target import Target


def _action(tmp_path, declared, detected):
    action = InitramfsAction({"initramfs": declared},
                             ActionContext(target=Target(root=str(tmp_path))))
    action._detect_generator = lambda: detected
    return action


def test_a_machine_on_the_other_generator_is_planned(tmp_path):
    action = _action(tmp_path, declared="dracut", detected="mkinitcpio")

    with patch.object(type(action._backend), "actual_value", return_value="whatever"), \
         patch.object(type(action._backend), "desired_value", return_value="whatever"):
        changes = action.plan(managed=[])

    assert [c.op.name for c in changes] == ["MODIFY"]
    assert "generator" in changes[0].reason


def test_a_machine_already_on_the_declared_generator_is_silent(tmp_path):
    action = _action(tmp_path, declared="dracut", detected="dracut")

    with patch.object(type(action._backend), "actual_value", return_value="same"), \
         patch.object(type(action._backend), "desired_value", return_value="same"):
        assert action.plan(managed=[]) == []


def test_the_switch_runs_the_new_backend(tmp_path):
    action = _action(tmp_path, declared="dracut", detected="mkinitcpio")

    with patch.object(type(action._backend), "actual_value", return_value="same"), \
         patch.object(type(action._backend), "desired_value", return_value="same"), \
         patch.object(type(action._backend), "apply") as backend_apply:
        action.apply(action.plan(managed=[]))

    backend_apply.assert_called_once()


def test_an_unknowable_generator_forces_nothing(tmp_path):
    """No target to probe (a plan against a config, not a machine): the domain
    keeps its old behaviour rather than churning the image."""
    action = _action(tmp_path, declared="dracut", detected=None)

    with patch.object(type(action._backend), "actual_value", return_value="same"), \
         patch.object(type(action._backend), "desired_value", return_value="same"):
        assert action.plan(managed=[]) == []


@pytest.mark.parametrize("declared,detected", [("mkinitcpio", "dracut"),
                                               ("dracut", "mkinitcpio")])
def test_both_directions_are_caught(tmp_path, declared, detected):
    action = _action(tmp_path, declared=declared, detected=detected)

    with patch.object(type(action._backend), "actual_value", return_value="same"), \
         patch.object(type(action._backend), "desired_value", return_value="same"):
        assert [c.op.name for c in action.plan(managed=[])] == ["MODIFY"]
