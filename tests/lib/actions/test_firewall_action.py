"""FirewallAction — declarative firewalld zone rules, idempotent.

expand_firewall only installs firewalld + enables the service; the declared
allowed_services / rich_rules / remove_services were previously ignored. This
action applies them via firewall-offline-cmd. Tests pin the plan decision
(idempotency) and the apply command construction with a mocked offline-cmd.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.state.change import Op


def _fw(**cfg):
    cfg.setdefault("enable", True)
    return FirewallAction(cfg, context=SimpleNamespace(target=object()))


def _with_zone(services="", rich=""):
    """Mock firewall-offline-cmd: --list-services / --list-rich-rules output."""
    def fake(cmd, args, *a, **kw):
        joined = " ".join(args)
        if "--list-services" in joined:
            return SimpleNamespace(stdout=services.encode())
        if "--list-rich-rules" in joined:
            return SimpleNamespace(stdout=rich.encode())
        return SimpleNamespace(stdout=b"")
    return patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake)


def test_disabled_firewall_plans_nothing():
    a = _fw(enable=False, allowed_services=["ssh"])
    assert a.plan(managed=[]) == []


def test_converged_firewall_is_a_noop():
    a = _fw(allowed_services=["syncthing"], rich_rules=["rule x accept"], remove_services=["ssh"])
    # zone already has syncthing + the rich rule, and ssh already absent
    with _with_zone(services="syncthing dhcpv6-client", rich="rule x accept"):
        assert a.plan(managed=[]) == []
        assert a.is_needed() is False


def test_missing_service_and_rule_are_planned():
    a = _fw(allowed_services=["syncthing"], rich_rules=["rule x accept"])
    with _with_zone(services="dhcpv6-client", rich=""):
        changes = a.plan(managed=[])
    items = {(c.op, c.item) for c in changes}
    assert (Op.ENABLE, "service:syncthing") in items
    assert (Op.ENABLE, "rich:rule x accept") in items


def test_remove_service_only_when_present():
    a = _fw(remove_services=["ssh"])
    with _with_zone(services="ssh dhcpv6-client"):
        changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.DISABLE, "service:ssh")]
    # if ssh already absent → no-op
    a2 = _fw(remove_services=["ssh"])
    with _with_zone(services="dhcpv6-client"):
        assert a2.plan(managed=[]) == []


def test_apply_runs_offline_cmd_with_right_flags():
    a = _fw(allowed_services=["syncthing"], remove_services=["ssh"], rich_rules=["rule r"])
    calls = []

    def fake(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args)))
        return SimpleNamespace(stdout=b"")

    changes = [
        type("C", (), {"item": "service:syncthing", "op": Op.ENABLE})(),
        type("C", (), {"item": "service:ssh", "op": Op.DISABLE})(),
        type("C", (), {"item": "rich:rule r", "op": Op.ENABLE})(),
    ]
    with patch("dasik.lib.actions.firewall_action.Command.execute", side_effect=fake):
        a.apply(changes)
    flat = [args for _cmd, args in calls]
    assert ("--zone=public", "--add-service=syncthing") in flat
    assert ("--zone=public", "--remove-service=ssh") in flat
    assert ("--zone=public", "--add-rich-rule=rule r") in flat
    assert all(cmd == "firewall-offline-cmd" for cmd, _ in calls)
