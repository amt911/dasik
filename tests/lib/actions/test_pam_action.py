"""PAM hardening: three items, three files, and one that touches the PAM stack.

The shape is SystemdConfAction's — write a drop-in, read the EFFECTIVE
configuration — with the whole file owned only where no drop-in mechanism
exists (`/etc/security/faillock.conf`). Every file involved is a pacman backup
file, so ownership costs at most a `.pacnew`.
"""
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.pam_action import PamAction
from dasik.lib.state.change import Op


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


@pytest.fixture
def target(tmp_path):
    (tmp_path / "etc/security").mkdir(parents=True)
    (tmp_path / "etc/pam.d").mkdir(parents=True)
    # A stock Arch machine: faillock.conf is all comments, passwd just includes
    # system-auth.
    (tmp_path / "etc/security/faillock.conf").write_text(
        "# Configuration for locking the user after multiple failed\n"
        "# authentication attempts.\n# deny = 3\n")
    (tmp_path / "etc/pam.d/passwd").write_text(
        "#%PAM-1.0\nauth\t\tinclude\t\tsystem-auth\n"
        "account\t\tinclude\t\tsystem-auth\n"
        "password\tinclude\t\tsystem-auth\n")
    return _Target(tmp_path)


def _action(cfg, target):
    return PamAction(cfg, SimpleNamespace(target=target))


def _read(target, canonical):
    path = target.path(canonical)
    return open(path).read() if os.path.exists(path) else ""


def _ops(changes):
    return [(c.op, c.item) for c in changes]


FAILLOCK = {"pam": {"faillock": {}}}
LIMITS = {"pam": {"limits": {}}}
PWQUALITY = {"pam": {"pwquality": {}}}


# --- faillock -------------------------------------------------------------- #

def test_a_declared_faillock_is_planned_on_a_stock_machine(target):
    assert _ops(_action(FAILLOCK, target).plan(managed=[])) == [(Op.INSTALL, "faillock")]


def test_apply_writes_the_declared_values(target):
    action = _action(FAILLOCK, target)
    action.apply(action.plan(managed=[]))
    written = _read(target, "/etc/security/faillock.conf")
    assert "deny = 5" in written
    assert "unlock_time = 600" in written
    assert "fail_interval = 900" in written


def test_persistent_lockouts_land_in_var_lib(target):
    """The default /run/faillock is cleared by a reboot — which an attacker with
    the power button can arrange."""
    action = _action(FAILLOCK, target)
    action.apply(action.plan(managed=[]))
    assert "dir = /var/lib/faillock" in _read(target, "/etc/security/faillock.conf")


def test_non_persistent_lockouts_do_not_set_a_directory(target):
    action = _action({"pam": {"faillock": {"persistent": False}}}, target)
    action.apply(action.plan(managed=[]))
    assert "dir =" not in _read(target, "/etc/security/faillock.conf")


def test_a_second_plan_after_apply_is_silent(target):
    action = _action(FAILLOCK, target)
    action.apply(action.plan(managed=[]))
    assert _action(FAILLOCK, target).plan(managed=["faillock"]) == []


def test_key_order_on_disk_does_not_produce_a_phantom_change(target):
    (target.path("/etc/security/faillock.conf")) and None
    with open(target.path("/etc/security/faillock.conf"), "w") as f:
        f.write("# Managed by dasik\nunlock_time = 600\ndeny = 5\n"
                "fail_interval = 900\ndir = /var/lib/faillock\n")
    assert _action(FAILLOCK, target).plan(managed=["faillock"]) == []


def test_dropping_the_block_restores_the_compiled_in_defaults(target):
    action = _action(FAILLOCK, target)
    action.apply(action.plan(managed=[]))
    dropped = _action({}, target)
    changes = dropped.plan(managed=["faillock"])
    assert _ops(changes) == [(Op.REMOVE, "faillock")]
    dropped.apply(changes)
    written = _read(target, "/etc/security/faillock.conf")
    assert "deny" not in written.replace("# ", "")   # nothing left but the header


def test_an_unowned_faillock_is_left_alone(target):
    """Somebody else's hardening is not dasik's to undo."""
    with open(target.path("/etc/security/faillock.conf"), "w") as f:
        f.write("deny = 3\n")
    assert _action({}, target).plan(managed=[]) == []


# --- limits ---------------------------------------------------------------- #

def test_limits_write_a_drop_in(target):
    action = _action(LIMITS, target)
    action.apply(action.plan(managed=[]))
    written = _read(target, "/etc/security/limits.d/10-dasik.conf")
    assert "* soft nproc 100" in written
    assert "* hard nproc 200" in written


def test_dropping_limits_deletes_the_drop_in(target):
    action = _action(LIMITS, target)
    action.apply(action.plan(managed=[]))
    dropped = _action({}, target)
    dropped.apply(dropped.plan(managed=["limits"]))
    assert not os.path.exists(target.path("/etc/security/limits.d/10-dasik.conf"))


# --- pwquality ------------------------------------------------------------- #

def test_pwquality_writes_both_the_drop_in_and_the_pam_stack(target):
    action = _action(PWQUALITY, target)
    action.apply(action.plan(managed=[]))
    conf = _read(target, "/etc/security/pwquality.conf.d/10-dasik.conf")
    assert "minlen = 10" in conf
    assert "dcredit = -1" in conf
    stack = _read(target, "/etc/pam.d/passwd")
    assert "pam_pwquality.so" in stack


def test_pam_unix_takes_the_password_pwquality_already_validated(target):
    """Without `use_authtok`, pam_unix prompts again and the policy it just
    enforced is bypassed entirely."""
    action = _action(PWQUALITY, target)
    action.apply(action.plan(managed=[]))
    stack = _read(target, "/etc/pam.d/passwd")
    pwq = next(i for i, l in enumerate(stack.splitlines()) if "pam_pwquality.so" in l)
    unix = next(i for i, l in enumerate(stack.splitlines()) if "pam_unix.so" in l)
    assert pwq < unix
    assert "use_authtok" in stack.splitlines()[unix]


def test_enforce_for_root_only_appears_when_declared(target):
    action = _action(PWQUALITY, target)
    action.apply(action.plan(managed=[]))
    assert "enforce_for_root" not in _read(target, "/etc/pam.d/passwd")

    strict = _action({"pam": {"pwquality": {"enforce_for_root": True}}}, target)
    strict.apply(strict.plan(managed=["pwquality"]))
    assert "enforce_for_root" in _read(target, "/etc/pam.d/passwd")


def test_a_value_set_in_the_package_file_is_not_read_as_absent(target):
    """The effective read merges /etc/security/pwquality.conf with the drop-in,
    later winning — reading only our own file made a package-file value
    invisible, which is the bug PR #177 fixed for systemd."""
    (target.path("/etc/security/pwquality.conf")) and None
    with open(target.path("/etc/security/pwquality.conf"), "w") as f:
        f.write("minlen = 10\n")
    action = _action(PWQUALITY, target)
    assert action._effective_pwquality()["minlen"] == "10"


def test_a_disabled_pwquality_is_not_planned(target):
    assert _action({"pam": {"pwquality": {"enable": False}}}, target).plan(managed=[]) == []


def test_dropping_pwquality_restores_the_stock_passwd_stack(target):
    action = _action(PWQUALITY, target)
    action.apply(action.plan(managed=[]))
    dropped = _action({}, target)
    changes = dropped.plan(managed=["pwquality"])
    assert _ops(changes) == [(Op.REMOVE, "pwquality")]
    dropped.apply(changes)
    stack = _read(target, "/etc/pam.d/passwd")
    assert "pam_pwquality.so" not in stack
    assert "password\tinclude\t\tsystem-auth" in stack
    assert not os.path.exists(target.path("/etc/security/pwquality.conf.d/10-dasik.conf"))


# --- the domain as a whole -------------------------------------------------- #

def test_the_three_items_are_independent(target):
    cfg = {"pam": {"faillock": {}, "limits": {}, "pwquality": {}}}
    assert sorted(i for _op, i in _ops(_action(cfg, target).plan(managed=[]))) == [
        "faillock", "limits", "pwquality"]


def test_an_absent_block_plans_nothing(target):
    assert _action({}, target).plan(managed=[]) == []


def test_the_manifest_records_the_declared_items(target):
    assert _action(FAILLOCK, target).managed_keys() == {"pam": ["faillock"]}
