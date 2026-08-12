"""auditd owns the mode of its own log directory.

VM-proven: with the tmpfiles override in place and no `log_group`, a booted
guest still showed `drwx------ root root /var/log/audit`. auditd sets that at
start, so the group membership the toggle grants buys nothing until this line
exists — a feature that looked applied and did nothing.
"""
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.auditd_conf_action import AuditdConfAction
from dasik.lib.state.change import Op

_STOCK = ("#\n# This file controls the configuration of the audit daemon\n#\n"
          "local_events = yes\nwrite_logs = yes\nlog_file = /var/log/audit/audit.log\n"
          "log_format = ENRICHED\nfreq = 50\n")


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


@pytest.fixture
def target(tmp_path):
    (tmp_path / "etc/audit").mkdir(parents=True)
    (tmp_path / "etc/audit/auditd.conf").write_text(_STOCK)
    return _Target(tmp_path)


def _action(cfg, target):
    return AuditdConfAction(cfg, SimpleNamespace(target=target))


def _conf(target):
    return open(target.path("/etc/audit/auditd.conf")).read()


AUDIT_ON = {"apparmor": {"audit": True}}


def test_the_declared_audit_flag_plans_the_log_group(target):
    assert [(c.op, c.item) for c in _action(AUDIT_ON, target).plan(managed=[])] == [
        (Op.MODIFY, "log_group")]


def test_apply_sets_it_and_keeps_every_other_line(target):
    action = _action(AUDIT_ON, target)
    action.apply(action.plan(managed=[]))

    written = _conf(target)
    assert "log_group = adm" in written
    assert "log_format = ENRICHED" in written
    assert "log_file = /var/log/audit/audit.log" in written


def test_a_second_plan_is_silent(target):
    action = _action(AUDIT_ON, target)
    action.apply(action.plan(managed=[]))

    assert _action(AUDIT_ON, target).plan(managed=["log_group"]) == []


def test_applying_twice_leaves_one_line(target):
    for _ in range(2):
        action = _action(AUDIT_ON, target)
        action.apply(action.plan(managed=[]))

    assert _conf(target).count("log_group") == 1


def test_an_existing_wrong_value_is_replaced_not_appended(target):
    with open(target.path("/etc/audit/auditd.conf"), "a") as f:
        f.write("log_group = root\n")
    action = _action(AUDIT_ON, target)
    action.apply(action.plan(managed=[]))

    assert _conf(target).count("log_group") == 1
    assert "log_group = adm" in _conf(target)


def test_apparmor_without_audit_plans_nothing(target):
    assert _action({"apparmor": {}}, target).plan(managed=[]) == []


def test_no_apparmor_block_plans_nothing(target):
    assert _action({}, target).plan(managed=[]) == []


def test_dropping_the_audit_flag_removes_the_line(target):
    action = _action(AUDIT_ON, target)
    action.apply(action.plan(managed=[]))

    dropped = _action({}, target)
    changes = dropped.plan(managed=["log_group"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "log_group")]
    dropped.apply(changes)
    assert "log_group" not in _conf(target)
    assert "log_format = ENRICHED" in _conf(target)


def test_a_log_group_dasik_never_set_is_left_alone(target):
    with open(target.path("/etc/audit/auditd.conf"), "a") as f:
        f.write("log_group = wheel\n")

    assert _action({}, target).plan(managed=["log_group"]) == []


def test_a_target_without_the_file_yet_still_plans_the_change(tmp_path):
    """The whole plan is computed before anything is applied, and on a fresh
    install `audit` — which ships auditd.conf — is not installed yet. Gating the
    plan on the file made the change land one apply late; PackagesAction runs
    before this action, so by apply time the file is there."""
    empty = _Target(tmp_path)

    assert [(c.op, c.item) for c in _action(AUDIT_ON, empty).plan(managed=[])] == [
        (Op.MODIFY, "log_group")]


def test_apply_never_creates_the_file(tmp_path):
    """It belongs to the `audit` package: a file pacman does not own yet makes
    installing that package fail with "exists in filesystem"."""
    empty = _Target(tmp_path)
    action = _action(AUDIT_ON, empty)
    action.apply(action.plan(managed=[]))

    assert not os.path.exists(empty.path("/etc/audit/auditd.conf"))
