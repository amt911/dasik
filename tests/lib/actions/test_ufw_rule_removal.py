"""A ufw rule you stop declaring must go.

Found in a VM: a config with `["allow 22/tcp", "allow 22000/tcp"]` applied, then
changed to `["allow 22/tcp", "allow 51820/udp"]`. The plan proposed only

    + [firewall] install allow 51820/udp  (ufw rule)

`allow 22000/tcp` stayed open on the machine, the re-plan said *No changes*, and
`sync` captured the port back into the config as if it had been asked for. A
firewall that only ever opens ports is not a firewall you can reason about.

The rest of dasik has said this since set_math: **owned, no longer declared ⇒
removed**. The ufw backend only had the install half.
"""
from unittest.mock import MagicMock, patch

import pytest

from types import SimpleNamespace

from dasik.lib.actions.firewall_action import FirewallAction


def _action(rules, live):
    cfg = {"enable": True, "backend": "ufw", "rules": list(rules)}
    action = FirewallAction(cfg, SimpleNamespace(target=None))
    action._live_ufw_rules = lambda: list(live)
    action._ufw_installed = lambda: True
    return action


def _plan(rules, live, managed):
    return [(c.op.name, c.item) for c in _action(rules, live).plan(managed=list(managed))]


def test_a_rule_no_longer_declared_is_removed():
    planned = _plan(rules=["allow 22/tcp"],
                    live=["allow 22/tcp", "allow 22000/tcp"],
                    managed=["allow 22/tcp", "allow 22000/tcp"])

    assert planned == [("REMOVE", "allow 22000/tcp")]


def test_a_rule_dasik_never_added_is_left_alone():
    """Somebody else's port is drift, not dasik's to close."""
    planned = _plan(rules=["allow 22/tcp"],
                    live=["allow 22/tcp", "allow 9999/tcp"],
                    managed=["allow 22/tcp"])

    assert planned == []


def test_install_and_remove_in_the_same_plan():
    planned = _plan(rules=["allow 22/tcp", "allow 51820/udp"],
                    live=["allow 22/tcp", "allow 22000/tcp"],
                    managed=["allow 22/tcp", "allow 22000/tcp"])

    assert sorted(planned) == [("INSTALL", "allow 51820/udp"),
                               ("REMOVE", "allow 22000/tcp")]


def test_a_converged_firewall_plans_nothing():
    assert _plan(rules=["allow 22/tcp"], live=["allow 22/tcp"],
                 managed=["allow 22/tcp"]) == []


def test_an_owned_rule_already_gone_is_not_planned_again():
    assert _plan(rules=[], live=[], managed=["allow 22000/tcp"]) == []


def test_apply_deletes_through_the_cli():
    action = _action(rules=["allow 22/tcp"], live=["allow 22/tcp", "allow 22000/tcp"])
    changes = action.plan(managed=["allow 22/tcp", "allow 22000/tcp"])

    with patch("dasik.lib.actions.firewall_action.Command.execute") as run:
        action.apply(changes)

    calls = [c.args[1] for c in run.call_args_list if c.args[0] == "ufw"]
    assert ["--force", "delete", "allow", "22000/tcp"] in calls


def test_the_rules_are_never_handed_to_a_shell():
    """`allow 22/tcp` is two arguments; the value comes from the config."""
    action = _action(rules=[], live=["allow 22000/tcp"])
    changes = action.plan(managed=["allow 22000/tcp"])

    with patch("dasik.lib.actions.firewall_action.Command.execute") as run:
        action.apply(changes)

    for call in run.call_args_list:
        assert isinstance(call.args[1], list)


def test_a_pure_removal_never_re_enables_ufw():
    """Tearing down the whole block has no business flipping the firewall
    back on -- `--force enable` only belongs to an apply with an INSTALL."""
    action = _action(rules=[], live=["allow 22000/tcp"])
    changes = action.plan(managed=["allow 22000/tcp"])

    with patch("dasik.lib.actions.firewall_action.Command.execute") as run:
        action.apply(changes)

    calls = [c.args[1] for c in run.call_args_list if c.args[0] == "ufw"]
    assert ["--force", "enable"] not in calls


def test_apply_installs_ufw_before_removing_a_rule():
    """FirewallAction now runs BEFORE PackagesAction (mirrors
    SnapperAction._ensure_snapper_installed): a REMOVE must still work even
    when `ufw` is not (yet, or any more) installed."""
    action = _action(rules=[], live=["allow 22000/tcp"])
    changes = action.plan(managed=["allow 22000/tcp"])
    calls = []

    def fake(cmd, args, **kw):
        calls.append((cmd, tuple(args)))
        rc = 1 if (cmd, tuple(args)) == ("pacman", ("-Qq", "ufw")) else 0
        return SimpleNamespace(returncode=rc, stdout=b"")

    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        action.apply(changes)

    cmds = [c for c, _ in calls]
    assert cmds.index("pacman") < cmds.index("ufw")
    install = next(args for cmd, args in calls if cmd == "pacman" and "-S" in args)
    assert "ufw" in install


def test_apply_skips_the_ufw_install_when_already_present():
    action = _action(rules=[], live=["allow 22000/tcp"])
    changes = action.plan(managed=["allow 22000/tcp"])
    calls = []

    def fake(cmd, args, **kw):
        calls.append((cmd, tuple(args)))
        return SimpleNamespace(returncode=0, stdout=b"")

    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        action.apply(changes)

    assert not any(cmd == "pacman" and "-S" in args for cmd, args in calls)
