import os
import subprocess

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.sudo_action import SudoAction, _canonical, _render
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


@pytest.fixture
def visudo_ok(monkeypatch):
    """visudo always validates. Records the argv it was called with."""
    calls = []

    def fake(cmd, args, **kwargs):
        calls.append((cmd, list(args)))
        return subprocess.CompletedProcess(args=[cmd, *args], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(Command, "execute", staticmethod(fake))
    return calls


@pytest.fixture
def visudo_fails(monkeypatch):
    def fake(cmd, args, **kwargs):
        return subprocess.CompletedProcess(args=[cmd, *args], returncode=1,
                                           stdout=b"", stderr=b"parse error")

    monkeypatch.setattr(Command, "execute", staticmethod(fake))


# --- rendering -----------------------------------------------------------

def test_render_wheel_with_password():
    assert "%wheel ALL=(ALL:ALL) ALL" in _render({"wheel": True})


def test_render_wheel_nopasswd():
    out = _render({"wheel": True, "nopasswd": True})
    assert "%wheel ALL=(ALL) NOPASSWD: ALL" in out
    assert "ALL=(ALL:ALL) ALL" not in out


def test_render_keeps_rule_order_after_wheel():
    out = _canonical(_render({"wheel": True, "rules": ["a ALL=(ALL) ALL", "b ALL=(ALL) ALL"]}))
    assert out.splitlines() == ["%wheel ALL=(ALL:ALL) ALL", "a ALL=(ALL) ALL", "b ALL=(ALL) ALL"]


def test_render_empty_when_nothing_declared():
    assert _render({}) == ""
    assert _render({"wheel": False}) == ""


def test_canonical_drops_comments_and_blank_lines():
    assert _canonical("# managed\n\n%wheel ALL=(ALL:ALL) ALL\n") == "%wheel ALL=(ALL:ALL) ALL\n"


# --- planning ------------------------------------------------------------

def test_plans_a_write_when_the_fragment_is_absent(tmp_path):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    assert [c.item for c in action.plan(managed=[])]


def test_no_plan_when_the_fragment_already_matches(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))
    assert action.plan(managed=[]) == []          # idempotency


def test_plans_a_rewrite_when_the_fragment_differs(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))
    changed = SudoAction({"sudo": {"wheel": True, "nopasswd": True}}, _ctx(tmp_path))
    assert [c.item for c in changed.plan(managed=[])]


def test_a_user_in_wheel_enables_sudo_without_a_sudo_block(tmp_path):
    action = SudoAction({"users": [{"username": "andres", "groups": ["wheel"]}]}, _ctx(tmp_path))
    assert "%wheel ALL=(ALL:ALL) ALL" in (action._desired_value() or "")


def test_explicit_wheel_false_disables_the_implicit_default(tmp_path):
    action = SudoAction({"sudo": {"wheel": False},
                         "users": [{"username": "andres", "groups": ["wheel"]}]}, _ctx(tmp_path))
    assert action._desired_value() is None
    assert action.plan(managed=[]) == []


def test_no_user_in_wheel_plans_nothing(tmp_path):
    action = SudoAction({"users": [{"username": "bob", "groups": ["video"]}]}, _ctx(tmp_path))
    assert action.plan(managed=[]) == []


# --- applying ------------------------------------------------------------

def test_apply_writes_the_fragment_0440_and_validates_it(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    action.apply(action.plan(managed=[]))

    written = tmp_path / "etc/sudoers.d/10-dasik"
    assert "%wheel ALL=(ALL:ALL) ALL" in written.read_text()
    assert oct(os.stat(written).st_mode & 0o777) == "0o440"
    assert visudo_ok == [("visudo", ["-cf", "/etc/sudoers.d/10-dasik.tmp"])]
    assert not (tmp_path / "etc/sudoers.d/10-dasik.tmp").exists()


def test_a_fragment_visudo_rejects_never_reaches_the_directory(tmp_path, visudo_fails):
    action = SudoAction({"sudo": {"wheel": True}}, _ctx(tmp_path))
    with pytest.raises(Exception):
        action.apply(action.plan(managed=[]))
    assert not (tmp_path / "etc/sudoers.d/10-dasik").exists()
    assert not (tmp_path / "etc/sudoers.d/10-dasik.tmp").exists()


# --- capture -------------------------------------------------------------

def test_import_state_round_trips_the_fragment(tmp_path, visudo_ok):
    action = SudoAction({"sudo": {"wheel": True, "nopasswd": True,
                                  "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]}},
                        _ctx(tmp_path))
    action.apply(action.plan(managed=[]))

    captured = SudoAction({}, _ctx(tmp_path)).import_state()
    assert captured == {"sudo": {"wheel": True, "nopasswd": True,
                                 "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]}}


def test_import_state_sees_wheel_enabled_in_stock_sudoers(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/sudoers").write_text("# %wheel ALL=(ALL:ALL) ALL\n%wheel ALL=(ALL:ALL) ALL\n")
    captured = SudoAction({}, _ctx(tmp_path)).import_state()
    assert captured["sudo"]["wheel"] is True


def test_import_state_is_empty_on_a_system_without_sudo_access(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/sudoers").write_text("# %wheel ALL=(ALL:ALL) ALL\nroot ALL=(ALL:ALL) ALL\n")
    assert SudoAction({}, _ctx(tmp_path)).import_state() == {}


# --- registry ------------------------------------------------------------

def test_registered_after_users_action():
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    from dasik.lib.actions.users_action import UsersAction

    registry = get_default_registry()
    registry.clear()
    setup_actions()
    classes = [meta["class"] for meta in registry.get_all_actions()]
    assert classes.index(SudoAction) > classes.index(UsersAction)
