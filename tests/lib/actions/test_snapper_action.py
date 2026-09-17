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
from dasik.lib.state.change import Change, Op
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


# --- removal: "snapper y firewall deben eliminar su config si desaparece" -- #
#
# The rule applies in all three disappearance forms: the config is dropped
# from `configs` while `enable` stays true, `enable` flips to false, or the
# whole `snapper` block is gone (the reconciler then hands `empty_config()`,
# see dasik#353). Every case: a managed config still on disk -> Op.REMOVE
# (destructive — `snapper delete-config` also drops its snapshots); already
# gone -> nothing; never owned -> left alone (drift, not dasik's).

def test_disabled_with_an_owned_config_plans_its_removal():
    a = SnapperAction({"enable": False}, context=SimpleNamespace(target=object()))
    a._exists = lambda name: name == "root"
    changes = a.plan(managed=["root"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "root")]
    assert changes[0].destructive is True


def test_block_absent_with_an_owned_config_plans_its_removal():
    """The reconciler hands `empty_config()` ({}) when a previous generation
    owns the domain and the block itself is gone from the config."""
    a = SnapperAction(SnapperAction.empty_config(), context=SimpleNamespace(target=object()))
    a._exists = lambda name: name == "root"
    assert [(c.op, c.item) for c in a.plan(managed=["root"])] == [(Op.REMOVE, "root")]


def test_a_config_dropped_from_the_list_while_still_enabled_is_removed():
    """The third form: `enable` stays true but the config no longer names it."""
    a = _snap(existing={"root", "home"}, configs=[{"name": "root", "subvolume": "/"}])
    changes = a.plan(managed=["root", "home"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "home")]


def test_a_config_already_gone_is_not_planned_again():
    a = SnapperAction({"enable": False}, context=SimpleNamespace(target=object()))
    a._exists = lambda name: False
    assert a.plan(managed=["root"]) == []


def test_an_unowned_config_is_left_alone():
    """A config dasik never created is somebody else's, not dasik's to remove."""
    a = SnapperAction({"enable": False}, context=SimpleNamespace(target=object()))
    a._exists = lambda name: name == "root"
    assert a.plan(managed=[]) == []


def test_removal_order_is_deterministic():
    a = SnapperAction({"enable": False}, context=SimpleNamespace(target=object()))
    a._exists = lambda name: True
    assert [c.item for c in a.plan(managed=["b", "a"])] == ["a", "b"]


def test_managed_keys_is_empty_when_disabled():
    """A disabled block must not claim ownership it no longer converges — the
    bug this domain had: `managed_keys()` returned the (ignored) `configs`
    list even with `enable: false`."""
    a = SnapperAction({"enable": False, "configs": [{"name": "root", "subvolume": "/"}]},
                      context=SimpleNamespace(target=object()))
    assert a.managed_keys() == {"snapper": []}


# --- apply(REMOVE): delete-config, MEASURED (docs/FACTS.md FACT-SFRM-*) ---- #
#
# A guest probe showed plain `snapper --no-dbus -c <name> delete-config` only
# behaves cleanly when `.snapshots` is snapper's OWN nested subvolume: it then
# deletes every snapshot AND the `.snapshots` subvolume itself, clears the name
# from /etc/conf.d/snapper's SNAPPER_CONFIGS, rc=0. On dasik's OWN recommended
# layout -- a SEPARATELY mounted `@.snapshots` -- the identical command deletes
# the individual snapshots fine, then FAILS deleting/unmounting the container
# (a live mount boundary), rc=1, and leaves `.snapshots` unmounted with its
# fstab entry gone while the config file is still there: worse than before.
# So `_delete_config` never calls that command; it does the removal itself,
# the same way for both layouts, verified below.

def _snapper_configs_dir(tmp_path):
    d = tmp_path / "etc/snapper/configs"
    d.mkdir(parents=True)
    return d


def _confd_snapper(tmp_path, names="root"):
    d = tmp_path / "etc/conf.d"
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapper").write_text(
        "## Path: System/Snapper\n"
        "## Type:        string\n"
        "## Default:     \"\"\n"
        "# List of snapper configurations.\n"
        f'SNAPPER_CONFIGS="{names}"\n'
    )


def _numbered_snapshots(tmp_path, subvol, *numbers):
    base = tmp_path / subvol.lstrip("/") / ".snapshots" if subvol != "/" \
        else tmp_path / ".snapshots"
    for n in numbers:
        (base / str(n)).mkdir(parents=True)
        (base / str(n) / "info.xml").write_text("<snapshot/>")
        (base / str(n) / "snapshot").mkdir()


def _fake_btrfs_rm(mountpoint_rc=1):
    """Records calls; `mountpoint` reports `mountpoint_rc` (0 = a separate
    mount / preexist=True, 1 = nested, not a mount)."""
    calls = []

    def fake(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args)))
        if cmd == "mountpoint":
            return SimpleNamespace(returncode=mountpoint_rc, stdout=b"")
        return SimpleNamespace(returncode=0, stdout=b"")

    return fake, calls


def test_apply_remove_nested_deletes_snapshots_and_the_container(tmp_path):
    """`.snapshots` is snapper's own nested subvolume (not a separate mount,
    `mountpoint -q` reports 1): delete every snapshot AND the container."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/root").write_text('SUBVOLUME="/"\n')
    _confd_snapper(tmp_path, "root")
    _numbered_snapshots(tmp_path, "/", 1, 2)

    a = SnapperAction({}, _ctx(tmp_path))
    fake, calls = _fake_btrfs_rm(mountpoint_rc=1)
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply([Change("snapper", Op.REMOVE, "root")])

    assert ("btrfs", ("subvolume", "delete", "/.snapshots/1/snapshot")) in calls
    assert ("btrfs", ("subvolume", "delete", "/.snapshots/2/snapshot")) in calls
    assert ("rm", ("-rf", "/.snapshots/1")) in calls
    assert ("rm", ("-rf", "/.snapshots/2")) in calls
    # nested -> the .snapshots container itself goes too (matches a
    # successful plain delete-config's own measured end state).
    assert ("btrfs", ("subvolume", "delete", "/.snapshots")) in calls
    assert ("rm", ("-f", "/etc/snapper/configs/root")) in calls

    conf = (tmp_path / "etc/conf.d/snapper").read_text()
    assert 'SNAPPER_CONFIGS=""' in conf


def test_apply_remove_separate_mount_never_deletes_the_container(tmp_path):
    """`.snapshots` is dasik's OWN separately-mounted @.snapshots subvolume
    (`mountpoint -q` reports 0): the container is NEVER handed to
    `btrfs subvolume delete` -- it stays mounted and usable."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/root").write_text('SUBVOLUME="/"\n')
    _confd_snapper(tmp_path, "root")
    _numbered_snapshots(tmp_path, "/", 1, 2)

    a = SnapperAction({}, _ctx(tmp_path))
    fake, calls = _fake_btrfs_rm(mountpoint_rc=0)
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply([Change("snapper", Op.REMOVE, "root")])

    assert ("btrfs", ("subvolume", "delete", "/.snapshots/1/snapshot")) in calls
    assert ("btrfs", ("subvolume", "delete", "/.snapshots/2/snapshot")) in calls
    assert ("btrfs", ("subvolume", "delete", "/.snapshots")) not in calls
    assert ("rm", ("-f", "/etc/snapper/configs/root")) in calls


def test_apply_remove_reads_the_subvolume_from_the_existing_config(tmp_path):
    """The removed name is not in `self.configs` any more (nothing declares
    it) -- its subvolume must come from the config file still on disk, not a
    hardcoded default."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/home").write_text('SUBVOLUME="/home"\n')
    _confd_snapper(tmp_path, "home")
    _numbered_snapshots(tmp_path, "/home", 1)

    a = SnapperAction({}, _ctx(tmp_path))
    fake, calls = _fake_btrfs_rm(mountpoint_rc=1)
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply([Change("snapper", Op.REMOVE, "home")])

    assert ("btrfs", ("subvolume", "delete", "/home/.snapshots/1/snapshot")) in calls
    assert ("btrfs", ("subvolume", "delete", "/home/.snapshots")) in calls


def test_apply_remove_stops_on_a_failed_snapshot_delete(tmp_path):
    """A failed btrfs delete raises and the config's OWN metadata is left
    alone -- retry-safe: the next plan still sees (and can retry) the REMOVE,
    rather than a config gone with orphaned snapshot subvolumes."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/root").write_text('SUBVOLUME="/"\n')
    _confd_snapper(tmp_path, "root")
    _numbered_snapshots(tmp_path, "/", 1)

    a = SnapperAction({}, _ctx(tmp_path))
    calls = []

    def fake(cmd, args, *aa, **kw):
        calls.append((cmd, tuple(args)))
        if cmd == "mountpoint":
            return SimpleNamespace(returncode=1, stdout=b"")
        if cmd == "btrfs":
            return SimpleNamespace(returncode=1, stdout=b"")
        return SimpleNamespace(returncode=0, stdout=b"")

    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        with pytest.raises(CommandExecutionError):
            a.apply([Change("snapper", Op.REMOVE, "root")])

    assert ("rm", ("-f", "/etc/snapper/configs/root")) not in calls
    assert (tmp_path / "etc/snapper/configs/root").exists()


def test_apply_remove_with_no_snapshots_still_drops_the_config(tmp_path):
    """A config with zero snapshots has nothing to delete under `.snapshots`
    but the config's own registration must still go."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/root").write_text('SUBVOLUME="/"\n')
    _confd_snapper(tmp_path, "root")
    (tmp_path / ".snapshots").mkdir()

    a = SnapperAction({}, _ctx(tmp_path))
    fake, calls = _fake_btrfs_rm(mountpoint_rc=1)
    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake):
        a.apply([Change("snapper", Op.REMOVE, "root")])

    assert ("rm", ("-f", "/etc/snapper/configs/root")) in calls
    assert ("btrfs", ("subvolume", "delete", "/.snapshots")) in calls


def test_drop_from_snapper_configs_list_keeps_other_names(tmp_path):
    _confd_snapper(tmp_path, "root home")
    a = SnapperAction({}, _ctx(tmp_path))
    a._drop_from_snapper_configs_list("root", a._target())
    conf = (tmp_path / "etc/conf.d/snapper").read_text()
    assert 'SNAPPER_CONFIGS="home"' in conf


def test_drop_from_snapper_configs_list_missing_file_is_a_noop(tmp_path):
    a = SnapperAction({}, _ctx(tmp_path))
    a._drop_from_snapper_configs_list("root", a._target())  # no raise


def test_apply_remove_ensures_snapper_installed_first(tmp_path):
    """The `if changes:` install guard covers REMOVE too -- SnapperAction runs
    before PackagesAction, so this is defensive, not load-bearing here (unlike
    the ufw case), but must not regress."""
    _snapper_configs_dir(tmp_path)
    (tmp_path / "etc/snapper/configs/root").write_text('SUBVOLUME="/"\n')
    _confd_snapper(tmp_path, "root")

    a = SnapperAction({}, _ctx(tmp_path))
    calls = []

    def fake_exec(cmd, args, **kw):
        calls.append((cmd, tuple(args)))
        rc = 1 if (cmd, tuple(args)) == ("pacman", ("-Qq", "snapper")) else 0
        return SimpleNamespace(returncode=rc, stdout=b"")

    with patch("dasik.lib.actions.snapper_action.Command.execute", side_effect=fake_exec):
        a.apply([Change("snapper", Op.REMOVE, "root")])

    cmds = [c for c, _ in calls]
    assert cmds.index("pacman") < cmds.index("mountpoint")
