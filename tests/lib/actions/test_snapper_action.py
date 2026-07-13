"""SnapperAction — declarative btrfs snapshot configs, idempotent.

Plans a `snapper create-config` only for a config that does not already exist
under /etc/snapper/configs, so a converged system re-plans to nothing. The
package + timers come from the expand toggle; this action does the create-config.
"""
from types import SimpleNamespace
from unittest.mock import patch

from dasik.lib.actions.snapper_action import SnapperAction
from dasik.lib.expand.toggles import expand_snapper
from dasik.lib.state.change import Op


def _snap(existing=(), **cfg):
    cfg.setdefault("enable", True)
    a = SnapperAction(cfg, context=SimpleNamespace(target=object()))
    a._exists = lambda name: name in existing
    return a


def test_disabled_plans_nothing():
    assert SnapperAction({"enable": False}, context=SimpleNamespace(target=object())).plan([]) == []


def test_default_config_is_root_on_slash():
    a = _snap()
    assert a.configs == [{"name": "root", "subvolume": "/"}]


def test_missing_config_is_planned():
    a = _snap(existing=())      # root config absent
    changes = a.plan([])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "root")]
    assert a.is_needed() is True


def test_existing_config_is_a_noop():
    a = _snap(existing={"root"})
    assert a.plan([]) == []
    assert a.is_needed() is False


def test_multiple_configs_only_missing_planned():
    a = _snap(existing={"root"},
              configs=[{"name": "root", "subvolume": "/"},
                       {"name": "home", "subvolume": "/home"}])
    changes = a.plan([])
    assert [(c.op, c.item) for c in changes] == [(Op.CREATE, "home")]


def test_apply_runs_snapper_create_config():
    a = _snap(existing=(),
              configs=[{"name": "home", "subvolume": "/home"}])
    calls = []

    def fake(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args)))
        return SimpleNamespace(stdout=b"")

    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply(a.plan([]))
    assert ("snapper", ("--no-dbus", "-c", "home", "create-config", "/home")) in calls


def test_toggle_contributes_packages_and_timers():
    out = expand_snapper({"snapper": {"enable": True}})
    assert "snapper" in out["packages"] and "snap-pac" in out["packages"]
    assert "snapper-timeline.timer" in out["units"]
    assert "snapper-cleanup.timer" in out["units"]
    assert expand_snapper({}) == {}
