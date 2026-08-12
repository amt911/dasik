"""Reading the PAM policy back off a machine.

The rule this file pins: `sync` reports the MACHINE, never the config. A
declared block the target does not have is cleared, not echoed back — otherwise
the captured file describes a policy nobody ever applied, and re-planning it
looks like a no-op it is not.
"""
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.pam_action import PamAction


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


@pytest.fixture
def target(tmp_path):
    (tmp_path / "etc/security").mkdir(parents=True)
    (tmp_path / "etc/pam.d").mkdir(parents=True)
    (tmp_path / "etc/security/faillock.conf").write_text(
        "# Configuration for locking the user.\n# deny = 3\n")
    (tmp_path / "etc/pam.d/passwd").write_text(
        "#%PAM-1.0\npassword\tinclude\t\tsystem-auth\n")
    return _Target(tmp_path)


def _action(cfg, target):
    return PamAction(cfg, SimpleNamespace(target=target))


def _hardened(target, **over):
    action = _action({"pam": {"faillock": over or {}}}, target)
    action.apply(action.plan(managed=[]))


def test_a_stock_machine_captures_nothing(target):
    assert _action({}, target).import_state() == {}


def test_a_hardened_faillock_is_captured(target):
    _hardened(target, deny=4, unlock_time=1200)

    captured = _action({}, target).import_state()["pam"]["faillock"]
    assert captured["deny"] == 4
    assert captured["unlock_time"] == 1200
    assert captured["persistent"] is True


def test_a_non_persistent_lockout_captures_as_such(target):
    _hardened(target, persistent=False)

    assert _action({}, target).import_state()["pam"]["faillock"]["persistent"] is False


def test_a_hand_written_faillock_is_captured_too(target):
    """The machine is the source of truth, whoever wrote the file."""
    with open(target.path("/etc/security/faillock.conf"), "w") as f:
        f.write("deny = 3\nunlock_time = 60\n")

    assert _action({}, target).import_state()["pam"]["faillock"]["deny"] == 3


def test_limits_are_captured(target):
    action = _action({"pam": {"limits": {"nproc_soft": 50, "nproc_hard": 75}}}, target)
    action.apply(action.plan(managed=[]))

    captured = _action({}, target).import_state()["pam"]["limits"]
    assert captured == {"nproc_soft": 50, "nproc_hard": 75}


def test_pwquality_is_captured_only_when_the_stack_actually_loads_it(target):
    """A drop-in nobody reads is not a policy: without pam_pwquality.so in
    /etc/pam.d/passwd the file is inert, and capturing it would describe an
    enforcement that does not happen."""
    os.makedirs(target.path("/etc/security/pwquality.conf.d"), exist_ok=True)
    with open(target.path("/etc/security/pwquality.conf.d/10-dasik.conf"), "w") as f:
        f.write("minlen = 12\n")

    assert "pwquality" not in _action({}, target).import_state().get("pam", {})


def test_pwquality_is_captured_when_the_module_is_in_the_stack(target):
    action = _action({"pam": {"pwquality": {"minlen": 14, "enforce_for_root": True}}},
                     target)
    action.apply(action.plan(managed=[]))

    captured = _action({}, target).import_state()["pam"]["pwquality"]
    assert captured["minlen"] == 14
    assert captured["enforce_for_root"] is True
    assert captured["enable"] is True


def test_a_declared_item_the_machine_lacks_is_cleared(target):
    """ConfigWriter.merge can only overwrite a key, never delete one, so staying
    silent would leave the stale declaration standing in the captured config."""
    captured = _action({"pam": {"faillock": {"deny": 3}}}, target).import_state()

    assert captured == {"pam": {}}


def test_an_undeclared_domain_on_a_stock_machine_invents_nothing(target):
    assert _action({}, target).import_state() == {}
