from unittest.mock import MagicMock, patch

from dasik.lib.actions.systemd_action import SystemdAction


def _enabled_map(enabled):
    """subprocess.run side-effect: 'enabled' for units in *enabled*, else 'disabled'."""
    def side(cmd, **kw):
        unit = cmd[-1]
        state = b"enabled\n" if unit in enabled else b"disabled\n"
        return MagicMock(stdout=state, returncode=0)
    return side


def test_not_needed_when_all_units_enabled():
    a = SystemdAction({"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"sshd.service", "cups.socket"})):
        assert a.is_needed() is False
        assert a.verify() is True


def test_needed_when_a_unit_pending():
    a = SystemdAction({"enable_units": ["sshd.service", "fail2ban.service"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"sshd.service"})):
        assert a.is_needed() is True
        assert a.verify() is False


def test_pending_lists_only_disabled_units():
    a = SystemdAction({"enable_units": ["a.service", "b.service"], "enable_sockets": ["c.socket"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"a.service"})):
        assert a._pending() == ["b.service", "c.socket"]


def test_empty_config_is_noop():
    a = SystemdAction({})
    assert a.is_needed() is False
    assert a.name == "Systemd Units"
    assert a.is_optional is True


# ---------------------------------------------------------------------- #
#  v3 contract (Plan 6)                                                   #
# ---------------------------------------------------------------------- #
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Change, Op


def _ctx(root="/"):
    return ActionContext(target=Target(root=root))


def test_constructor_exposes_d_on_and_d_off():
    a = SystemdAction(
        {"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"],
         "disable_units": ["bluetooth.service"]}
    )
    assert a._d_on() == ["sshd.service", "cups.socket"]
    assert a._d_off() == ["bluetooth.service"]


def test_actual_parses_enabled_unit_files():
    out = b"sshd.service enabled\ncups.socket enabled\nfstrim.timer enabled\n"
    fake = MagicMock(return_value=MagicMock(stdout=out, returncode=0))
    with patch("dasik.lib.actions.systemd_action.Command.execute", fake):
        a = SystemdAction({}, _ctx("/"))
        assert a.actual() == {"sshd.service", "cups.socket", "fstrim.timer"}
    call = fake.call_args
    assert call.args[0] == "systemctl"
    assert call.args[1] == ["list-unit-files", "--state=enabled", "--no-legend"]
    assert call.kwargs["target"].root == "/"


def test_actual_empty_when_no_target():
    a = SystemdAction({}, None)
    assert a.actual() == set()


def test_is_v3_true():
    assert SystemdAction.is_v3() is True


def _action(cfg, actual):
    a = SystemdAction(cfg, _ctx("/"))
    a.actual = lambda: set(actual)   # stub system reality
    return a


def test_plan_enables_missing_declared_units():
    a = _action({"enable_units": ["sshd.service"]}, actual=[])
    changes = a.plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.ENABLE, "sshd.service")]


def test_plan_disables_owned_no_longer_declared():
    a = _action({"enable_units": []}, actual=["old.service"])
    changes = a.plan(managed=["old.service"])
    assert [(c.op, c.item) for c in changes] == [(Op.DISABLE, "old.service")]


def test_plan_disables_forced_non_owned():
    a = _action({"disable_units": ["bluetooth.service"]}, actual=["bluetooth.service"])
    changes = a.plan(managed=[])
    assert [(c.op, c.item, c.reason) for c in changes] == [
        (Op.DISABLE, "bluetooth.service", "explicitly disabled")
    ]


def test_plan_empty_when_converged():
    a = _action({"enable_units": ["sshd.service"]}, actual=["sshd.service"])
    assert a.plan(managed=["sshd.service"]) == []


def test_managed_keys_is_d_on():
    a = SystemdAction(
        {"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"]}
    )
    assert a.managed_keys() == {"systemd": ["sshd.service", "cups.socket"]}
