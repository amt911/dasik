"""A declared file mode is desired state, so drift in it must be planned.

`EtcFile.mode` exists because NetworkManager and wg-quick REFUSE a keyfile that
is world-readable — the mode is not decoration, it is what makes the feature
work. dasik wrote it once and then stopped looking:

    $ chmod 644 /etc/wireguard/wg0.conf     # or any hand edit, or a restore
    $ dasik plan config.json
    No changes - system matches config.

A private key sits at 0644, the config says 0600, and the plan is silent. The
content comparison was the whole check.

A file with no declared mode is left alone: dasik does not own the permissions
it was never told about.
"""
import os

from unittest.mock import MagicMock

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target

_PATH = "/etc/wireguard/wg0.conf"
_BODY = "[Interface]\nPrivateKey = SECRET\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(DropFilesAction, "_vendor_copy", lambda self, path: None)
    monkeypatch.setattr("dasik.lib.actions.drop_files_action.run_logger.get",
                        lambda: MagicMock())


def _machine(tmp_path, mode=0o644, body=_BODY):
    (tmp_path / "etc/wireguard").mkdir(parents=True, exist_ok=True)
    f = tmp_path / "etc/wireguard/wg0.conf"
    f.write_text(body)
    os.chmod(f, mode)
    return tmp_path


def _action(tmp_path, declared_mode="0600"):
    entry = {"path": _PATH, "content": _BODY}
    if declared_mode is not None:
        entry["mode"] = declared_mode
    action = DropFilesAction({"files": [entry]},
                             ActionContext(target=Target(root=str(tmp_path))))
    action._pacman_owner = lambda path: None      # type: ignore[assignment]
    return action


def test_a_key_left_world_readable_is_planned(tmp_path):
    changes = _action(_machine(tmp_path)).plan(managed=[_PATH])

    assert [c.op.name for c in changes] == ["MODIFY"]
    assert "0644" in changes[0].reason and "0600" in changes[0].reason


def test_and_apply_puts_the_mode_back(tmp_path):
    root = _machine(tmp_path)
    action = _action(root)

    action.apply(action.plan(managed=[_PATH]))

    assert oct(os.stat(root / "etc/wireguard/wg0.conf").st_mode & 0o777) == "0o600"
    assert action.plan(managed=[_PATH]) == []


def test_the_right_mode_is_silent(tmp_path):
    assert _action(_machine(tmp_path, mode=0o600)).plan(managed=[_PATH]) == []


def test_a_file_with_no_declared_mode_keeps_whatever_it_has(tmp_path):
    action = _action(_machine(tmp_path), declared_mode=None)

    assert action.plan(managed=[_PATH]) == []


def test_content_drift_is_still_reported_as_content(tmp_path):
    root = _machine(tmp_path, mode=0o600, body="[Interface]\nPrivateKey = OTHER\n")

    changes = _action(root).plan(managed=[_PATH])

    assert [c.op.name for c in changes] == ["MODIFY"]
    assert "content" in changes[0].reason


def test_both_drifts_are_one_change(tmp_path):
    root = _machine(tmp_path, mode=0o644, body="[Interface]\nPrivateKey = OTHER\n")

    changes = _action(root).plan(managed=[_PATH])

    assert len(changes) == 1
    assert "content" in changes[0].reason and "0600" in changes[0].reason


def test_a_file_that_is_missing_is_still_a_create(tmp_path):
    (tmp_path / "etc/wireguard").mkdir(parents=True)
    action = _action(tmp_path)

    assert [c.op.name for c in action.plan(managed=[])] == ["CREATE"]
