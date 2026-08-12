"""The ufw backend of the `firewall` domain.

Unlike firewalld — whose whole zone dasik writes as a file — ufw keeps its state
in generated files (`/etc/ufw/user.rules`) that only the tool should write. So
this backend READS the machine through `ufw status` and WRITES through the CLI.

`ufw allow` is itself idempotent ("Skipping adding existing rule"), but a plan
that proposed the same rule forever would be a lie, which is what the status
parsing is for.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.state.change import Op


_STATUS_ACTIVE = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
Syncthing                  ALLOW IN    Anywhere
"""

_STATUS_INACTIVE = "Status: inactive\n"


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _action(cfg, status=False, fail=False):
    """`status=True` = a machine already carrying the rules in _STATUS_ACTIVE."""
    action = FirewallAction(cfg, SimpleNamespace(target=None))

    def fake_execute(cmd, args=None, **kwargs):
        if fail:
            raise FileNotFoundError(cmd)
        if cmd == "ufw" and args and args[0] == "status":
            return _Result(_STATUS_ACTIVE if status else _STATUS_INACTIVE)
        return _Result("")

    return action, fake_execute


UFW = {"enable": True, "backend": "ufw", "rules": ["allow 22/tcp"],
       "allowed_services": ["Syncthing"]}


def _plan(cfg, status=False):
    action, fake = _action(cfg, status=status)
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        return [(c.op, c.item) for c in action.plan(managed=[])]


def test_declared_ufw_rules_are_planned_on_a_machine_without_them():
    assert sorted(_plan(UFW)) == [(Op.INSTALL, "allow 22/tcp"),
                                  (Op.INSTALL, "allow Syncthing")]


def test_rules_already_present_plan_nothing():
    assert _plan(UFW, status=True) == []


def test_a_missing_rule_is_still_planned_when_the_other_is_there():
    cfg = dict(UFW, rules=["allow 22/tcp", "limit 443/tcp"])
    assert _plan(cfg, status=True) == [(Op.INSTALL, "limit 443/tcp")]


def test_the_firewalld_backend_is_untouched(tmp_path):
    """The default path must behave exactly as it did before the backend field."""
    action = FirewallAction({"enable": True, "allowed_services": ["samba"]},
                            SimpleNamespace(target=None))
    assert [c.op for c in action.plan(managed=[])] == [Op.MODIFY]


def test_apply_drives_the_cli_not_the_rules_file():
    action, fake = _action(UFW)
    calls = []

    def record(cmd, args=None, **kwargs):
        calls.append((cmd, tuple(args or ())))
        return fake(cmd, args, **kwargs)

    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=record):
        action.apply(action.plan(managed=[]))

    applied = {args for cmd, args in calls if cmd == "ufw"}
    assert ("allow", "22/tcp") in applied
    assert ("allow", "Syncthing") in applied
    # The firewall itself has to be switched on, non-interactively.
    assert ("--force", "enable") in applied


def test_a_rule_is_never_passed_as_one_shell_word():
    """`ufw allow 22/tcp` is two arguments. Passing 'allow 22/tcp' as a single
    argv entry makes ufw reject it — and the config controls this string."""
    action, fake = _action(UFW)
    calls = []

    def record(cmd, args=None, **kwargs):
        calls.append((cmd, list(args or ())))
        return fake(cmd, args, **kwargs)

    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=record):
        action.apply(action.plan(managed=[]))

    for cmd, args in calls:
        for arg in args:
            assert " " not in arg, f"{cmd} {args}"


def test_an_unreadable_status_plans_the_rules_rather_than_claiming_convergence():
    """At install time there is no running firewall to ask. Planning the rules
    is the safe answer: `ufw allow` is idempotent, so re-applying costs nothing,
    while claiming convergence would skip them forever."""
    action, fake = _action(UFW, fail=True)
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        assert len(action.plan(managed=[])) == 2


def test_the_manifest_records_the_ufw_rules():
    action, _ = _action(UFW)
    assert action.managed_keys() == {"firewall": ["allow 22/tcp", "allow Syncthing"]}


def test_a_disabled_block_plans_nothing():
    assert _plan({"enable": False, "backend": "ufw", "rules": ["allow 22/tcp"]}) == []


def test_import_state_captures_the_live_ufw_rules():
    action, fake = _action({})
    with patch("dasik.lib.actions.firewall_action.Command.execute",
               side_effect=lambda cmd, args=None, **kw: (
                   _Result(_STATUS_ACTIVE) if cmd == "ufw" else _Result("", 1))):
        with patch("os.path.exists", return_value=True):
            captured = action.import_state()

    assert captured["firewall"]["backend"] == "ufw"
    assert "allow 22/tcp" in captured["firewall"]["rules"]
    assert "allow Syncthing" in captured["firewall"]["rules"]
