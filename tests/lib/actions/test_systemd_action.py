from unittest.mock import MagicMock, patch

import pytest

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


def test_apply_enables_and_disables_routed():
    a = SystemdAction({}, _ctx("/"))
    changes = [
        Change("systemd", Op.ENABLE, "sshd.service"),
        Change("systemd", Op.DISABLE, "bluetooth.service"),
    ]
    with patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply(changes)
    calls = [(c.args[0], c.args[1]) for c in run.call_args_list]
    assert calls[0] == ("systemctl", ["enable", "sshd.service"])
    assert calls[1] == ("systemctl", ["disable", "bluetooth.service"])
    assert run.call_args_list[0].kwargs["target"].root == "/"


def test_apply_noop_on_empty():
    a = SystemdAction({}, _ctx("/"))
    with patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply([])
    run.assert_not_called()


def test_apply_noop_without_target():
    a = SystemdAction({}, None)
    with patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply([Change("systemd", Op.ENABLE, "sshd.service")])
    run.assert_not_called()


def test_import_state_captures_drift_routed_by_suffix():
    a = _action(
        {"enable_units": ["sshd.service"], "enable_sockets": []},
        actual=["sshd.service", "docker.service", "cups.socket"],
    )
    frag = a.import_state(managed=[])
    sd = frag["systemd"]
    assert sd["enable_units"] == ["sshd.service", "docker.service"]
    assert sd["enable_sockets"] == ["cups.socket"]
    assert sd["disable_units"] == []


def test_import_state_keeps_declared_intent_even_if_not_enabled():
    """A declared unit not currently enabled is kept as intent (sync never drops
    a declaration just because it is not enabled right now)."""
    a = _action({"enable_units": ["sshd.service", "old.service"]},
                actual=["sshd.service"])
    frag = a.import_state(managed=["sshd.service", "old.service"])
    assert frag["systemd"]["enable_units"] == ["sshd.service", "old.service"]


def test_import_state_keeps_declared_intent_not_present():
    a = _action({"enable_units": ["sshd.service", "future.service"]},
                actual=["sshd.service"])
    frag = a.import_state(managed=[])
    assert frag["systemd"]["enable_units"] == ["sshd.service", "future.service"]


def test_import_state_preserves_disable_units_and_excludes_them_from_drift():
    a = _action({"disable_units": ["bluetooth.service"]},
                actual=["bluetooth.service", "docker.service"])
    frag = a.import_state(managed=[])
    sd = frag["systemd"]
    assert sd["disable_units"] == ["bluetooth.service"]
    assert sd["enable_units"] == ["docker.service"]


def test_legacy_is_needed_true_when_unit_to_disable_is_enabled():
    a = SystemdAction({"disable_units": ["bluetooth.service"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"bluetooth.service"})):
        assert a.is_needed() is True


def test_legacy_not_needed_when_disable_target_already_off():
    a = SystemdAction({"enable_units": ["sshd.service"],
                       "disable_units": ["bluetooth.service"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"sshd.service"})):
        assert a.is_needed() is False
        assert a.verify() is True


def test_legacy_to_disable_lists_only_enabled_targets():
    a = SystemdAction({"disable_units": ["a.service", "b.service"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"a.service"})):
        assert a._to_disable() == ["a.service"]


def test_import_state_captures_owned_present_undeclared_unit():
    a = _action({"enable_units": ["sshd.service"]},
                actual=["sshd.service", "docker.service"])
    frag = a.import_state(managed=["docker.service"])   # docker owned, not declared
    assert "docker.service" in frag["systemd"]["enable_units"]
    assert "sshd.service" in frag["systemd"]["enable_units"]


def test_actual_includes_enabled_template_instance():
    # `systemctl list-unit-files --state=enabled` OMITS enabled template instances
    # (e.g. wg-quick@wg0.service — the enablement is a .wants symlink, not a listed
    # unit file). `is-enabled` resolves them, so actual() must probe declared
    # instances or the reconciler re-enables them on every apply (non-idempotent).
    def fake(cmd, args=None, *a, **k):
        if args and args[0] == "list-unit-files":
            return MagicMock(stdout=b"sshd.service enabled\n", returncode=0)
        if args and args[0] == "is-enabled":
            unit = args[1]
            ok = unit == "wg-quick@wg0.service"
            return MagicMock(stdout=(b"enabled\n" if ok else b"disabled\n"), returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.systemd_action.Command.execute", side_effect=fake):
        a = SystemdAction({"enable_units": ["sshd.service", "wg-quick@wg0.service"]}, _ctx("/"))
        assert a.actual() == {"sshd.service", "wg-quick@wg0.service"}


def test_actual_invents_nothing_for_a_unit_systemd_calls_disabled():
    # The is-enabled fallback covers every declared unit now, not just '@'
    # instances (VM-observed: inside an arch-chroot the listing omitted an
    # ufw.service that is-enabled reported as enabled, and the reconciler
    # re-enabled it on every apply). It still adds nothing systemd does not
    # call exactly "enabled" — "static", "indirect" and "disabled" all stay out.
    def fake(cmd, args=None, *a, **k):
        if args and args[0] == "list-unit-files":
            return MagicMock(stdout=b"sshd.service enabled\n", returncode=0)
        if args and args[0] == "is-enabled":
            return MagicMock(stdout=b"static\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.systemd_action.Command.execute", side_effect=fake):
        a = SystemdAction({"enable_units": ["sshd.service", "cups.service"]}, _ctx("/"))
        assert a.actual() == {"sshd.service"}


# --- mutating commands must fail loud (F-06) ------------------------------- #

def test_enable_disable_use_check_true():
    """A missing unit makes `systemctl enable` exit non-zero; without check=True
    the failure is invisible and the unit is recorded as managed."""
    a = SystemdAction({}, _ctx("/"))
    changes = [
        Change("systemd", Op.ENABLE, "sshd.service"),
        Change("systemd", Op.DISABLE, "bluetooth.service"),
    ]
    with patch("dasik.lib.actions.systemd_action.Command.execute") as run:
        a.apply(changes)
    assert all(c.kwargs.get("check") is True for c in run.call_args_list)


def test_enable_failure_propagates():
    from dasik.lib.exceptions.exceptions import CommandExecutionError
    a = SystemdAction({}, _ctx("/"))
    with patch("dasik.lib.actions.systemd_action.Command.execute",
               side_effect=CommandExecutionError("no such unit")):
        with pytest.raises(CommandExecutionError):
            a.apply([Change("systemd", Op.ENABLE, "sddm.service")])


# --- enablements `list-unit-files` cannot see ------------------------------ #
#
# VM-observed: inside an arch-chroot, `systemctl list-unit-files --state=enabled`
# did not list ufw.service while `systemctl is-enabled ufw.service` answered
# "enabled". The reconciler therefore re-enabled it on every apply and a fresh
# install never reached a silent plan. Same shape as the template-instance case.

def test_a_unit_the_listing_misses_is_probed_with_is_enabled(monkeypatch):
    from types import SimpleNamespace
    from dasik.lib.actions.systemd_action import SystemdAction

    def fake_execute(cmd, args=None, **kwargs):
        if args and args[0] == "list-unit-files":
            return SimpleNamespace(stdout="sshd.service enabled\n", returncode=0)
        if args and args[0] == "is-enabled":
            return SimpleNamespace(stdout="enabled\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr("dasik.lib.actions.systemd_action.Command.execute", fake_execute)
    action = SystemdAction({"enable_units": ["ufw.service"]},
                           SimpleNamespace(target=SimpleNamespace(root="/mnt")))

    assert "ufw.service" in action.actual()


def test_a_unit_that_is_really_disabled_is_not_invented(monkeypatch):
    from types import SimpleNamespace
    from dasik.lib.actions.systemd_action import SystemdAction

    def fake_execute(cmd, args=None, **kwargs):
        if args and args[0] == "list-unit-files":
            return SimpleNamespace(stdout="sshd.service enabled\n", returncode=0)
        return SimpleNamespace(stdout="disabled\n", returncode=0)

    monkeypatch.setattr("dasik.lib.actions.systemd_action.Command.execute", fake_execute)
    action = SystemdAction({"enable_units": ["ufw.service"]},
                           SimpleNamespace(target=SimpleNamespace(root="/mnt")))

    assert "ufw.service" not in action.actual()
