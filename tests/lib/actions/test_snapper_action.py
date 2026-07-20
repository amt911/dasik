"""SnapperAction — declarative btrfs snapshot configs, idempotent.

Plans a `snapper create-config` only for a config that does not already exist
under /etc/snapper/configs, so a converged system re-plans to nothing. The
package + timers come from the expand toggle; this action does the create-config.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dasik.lib.actions.snapper_action import SnapperAction
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.expand.toggles import expand_snapper
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _fake_exec(mountpoint_rc=1, create_rc=0):
    """Fake Command.execute recording calls; mountpoint/create returncodes tunable."""
    calls = []

    def fake(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args)))
        if cmd == "mountpoint":
            return SimpleNamespace(returncode=mountpoint_rc, stdout=b"")
        if cmd == "snapper":
            return SimpleNamespace(returncode=create_rc, stdout=b"")
        return SimpleNamespace(returncode=0, stdout=b"")

    return fake, calls


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


def test_apply_preexisting_snapshots_does_wiki_dance():
    # When our @snapshots is already mounted at /.snapshots, snapper create-config
    # fails; follow the Arch wiki: umount → rmdir → create-config → delete
    # snapper's nested .snapshots → mkdir → remount our subvolume (via fstab).
    a = _snap(existing=())      # root config missing
    fake, calls = _fake_exec(mountpoint_rc=0)   # /.snapshots IS a mountpoint
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply(a.plan([]))
    seq = [c for c in calls]
    assert ("mountpoint", ("-q", "/.snapshots")) in seq
    assert ("umount", ("/.snapshots",)) in seq
    assert ("rmdir", ("/.snapshots",)) in seq
    assert ("snapper", ("--no-dbus", "-c", "root", "create-config", "/")) in seq
    assert ("btrfs", ("subvolume", "delete", "/.snapshots")) in seq
    assert ("mkdir", ("-p", "/.snapshots")) in seq
    assert ("mount", ("/.snapshots",)) in seq
    # order: umount before create-config before btrfs-delete before remount
    i_umount = seq.index(("umount", ("/.snapshots",)))
    i_create = seq.index(("snapper", ("--no-dbus", "-c", "root", "create-config", "/")))
    i_delete = seq.index(("btrfs", ("subvolume", "delete", "/.snapshots")))
    i_mount = seq.index(("mount", ("/.snapshots",)))
    assert i_umount < i_create < i_delete < i_mount


def test_apply_no_preexisting_snapshots_just_creates():
    a = _snap(existing=())
    fake, calls = _fake_exec(mountpoint_rc=1)   # /.snapshots NOT a mountpoint
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply(a.plan([]))
    cmds = [c[0] for c in calls]
    assert "snapper" in cmds
    assert "umount" not in cmds and "btrfs" not in cmds   # no dance needed


def test_apply_raises_when_create_config_fails():
    # Don't silently swallow a failure (the bug the VM caught): surface it.
    a = _snap(existing=())
    fake, _ = _fake_exec(mountpoint_rc=1, create_rc=1)
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        with pytest.raises(CommandExecutionError):
            a.apply(a.plan([]))


def test_toggle_contributes_packages_and_timers():
    out = expand_snapper({"snapper": {"enable": True}})
    assert "snapper" in out["packages"] and "snap-pac" in out["packages"]
    assert "snapper-timeline.timer" in out["units"]
    assert "snapper-cleanup.timer" in out["units"]
    assert expand_snapper({}) == {}


# --- sync round-trip (F-14) ------------------------------------------------ #

def test_import_state_captures_configs_from_the_target(tmp_path):
    """import_state() returned {} — a real snapper setup was invisible to sync,
    so a captured config lost its snapshots entirely."""
    cfg_dir = tmp_path / "etc" / "snapper" / "configs"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "root").write_text('SUBVOLUME="/"\nTIMELINE_CREATE="yes"\n')
    (cfg_dir / "home").write_text("SUBVOLUME=/home\n")
    a = SnapperAction({}, _ctx(tmp_path))
    frag = a.import_state()
    assert frag["snapper"]["enable"] is True
    assert sorted(frag["snapper"]["configs"], key=lambda c: c["name"]) == [
        {"name": "home", "subvolume": "/home"},
        {"name": "root", "subvolume": "/"},
    ]


def test_import_state_empty_without_snapper_configs(tmp_path):
    assert SnapperAction({}, _ctx(tmp_path)).import_state() == {}


# --- bootstrap order (F-13) ------------------------------------------------ #

def test_registered_before_packages():
    """snap-pac hooks snapshot pacman transactions, so the config must exist
    before the big package transaction — not after it."""
    from dasik.lib.actions.action_registry import get_default_registry
    from dasik.lib.actions.actions_handler_v2 import setup_actions
    setup_actions()
    names = [m["class"].__name__ for m in get_default_registry().get_all_actions()]
    assert names.index("SnapperAction") < names.index("PackagesAction")


def test_apply_installs_snapper_before_creating_the_config(tmp_path):
    """Running before Packages means the binary may not be there yet; the action
    installs its own prerequisite (idempotent: pacman --needed)."""
    from unittest.mock import patch
    a = SnapperAction({"enable": True,
                       "configs": [{"name": "root", "subvolume": "/"}]},
                      _ctx(tmp_path))
    calls = []

    def fake_exec(cmd, args, **kw):
        calls.append((cmd, args))
        rc = 1 if (cmd, tuple(args)) == ("pacman", ("-Qq", "snapper")) else 0
        return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"")

    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake_exec):
        a.apply(a.plan(managed=[]))
    cmds = [c for c, _ in calls]
    assert cmds.index("pacman") < cmds.index("snapper")
    install = next(args for cmd, args in calls if cmd == "pacman" and "-S" in args)
    assert "snapper" in install and "snap-pac" in install


def test_apply_skips_the_install_when_snapper_is_present(tmp_path):
    from unittest.mock import patch
    a = SnapperAction({"enable": True,
                       "configs": [{"name": "root", "subvolume": "/"}]},
                      _ctx(tmp_path))
    calls = []

    def fake_exec(cmd, args, **kw):
        calls.append((cmd, args))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake_exec):
        a.apply(a.plan(managed=[]))
    assert not any(cmd == "pacman" and "-S" in args for cmd, args in calls)
