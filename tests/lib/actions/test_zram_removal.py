"""Dropping the `zram` block must remove the file the block wrote.

Every other quiet domain takes its work back when the block goes:

    - [oomd] remove drop-in 10-dasik.conf                (block no longer declared)
    - [systemd_system_conf] remove drop-in 10-dasik.conf (block no longer declared)
    - [files] delete /etc/plymouth/plymouthd.conf        (no longer declared)

`zram` did not. `/etc/systemd/zram-generator.conf` stayed on the machine and no
plan ever mentioned it — inert while zram-generator is uninstalled, and awake
again the day the package comes back for any reason.
"""
import os

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.zram_action import ZramAction
from dasik.lib.target.target import Target

_CONF = "/etc/systemd/zram-generator.conf"
_BODY = "[zram0]\nzram-size = min(ram / 2, 4096)\n"


def _machine(tmp_path, body=_BODY):
    (tmp_path / "etc/systemd").mkdir(parents=True, exist_ok=True)
    if body is not None:
        (tmp_path / "etc/systemd/zram-generator.conf").write_text(body)
    return tmp_path


def _action(tmp_path, config):
    return ZramAction(config, ActionContext(target=Target(root=str(tmp_path))))


def test_dropping_the_block_plans_the_removal(tmp_path):
    action = _action(_machine(tmp_path), {})

    changes = action.plan(managed=[_BODY])

    assert [c.op.name for c in changes] == ["REMOVE"]
    assert "no longer declared" in changes[0].reason


def test_and_apply_takes_the_file_away(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {})

    action.apply(action.plan(managed=[_BODY]))

    assert not (root / "etc/systemd/zram-generator.conf").exists()
    assert action.plan(managed=[]) == []


def test_a_file_dasik_never_wrote_is_left_alone(tmp_path):
    """Somebody else's zram config is drift, not dasik's to delete."""
    action = _action(_machine(tmp_path), {})

    assert action.plan(managed=[]) == []


def test_a_declared_block_still_converges(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {"zram": {"zram0": {"zram-size": "min(ram / 2, 4096)"}}})

    assert action.plan(managed=[_BODY]) == []


def test_a_changed_block_is_still_a_modify(tmp_path):
    root = _machine(tmp_path)
    action = _action(root, {"zram": {"zram0": {"zram-size": "ram / 4"}}})

    assert [c.op.name for c in action.plan(managed=[_BODY])] == ["MODIFY"]


def test_nothing_to_remove_when_the_file_is_gone(tmp_path):
    action = _action(_machine(tmp_path, body=None), {})

    assert action.plan(managed=[_BODY]) == []
