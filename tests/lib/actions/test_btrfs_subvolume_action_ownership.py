"""Day-2 btrfs subvolumes: OWNERSHIP survives a sync, and the root is skipped.

`Reconciler._owned_after_sync` keeps `actual() & (managed | declared)`. If
`actual()` only ever reported what the config DECLARES, a sync with a config
that no longer lists a subvolume (or seeded from `{}`) would drop it from
`managed` and a later apply could never plan its REMOVE.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from dasik.lib.actions.btrfs_subvol_state import Live, Wanted, diff
from dasik.lib.actions.btrfs_subvolume_action import BtrfsSubvolumeAction
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.state.change import Op


def _sibling(name):
    """Load a sibling test module's helpers (pytest runs in importlib mode)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_plan_mod = _sibling("test_btrfs_subvolume_action_plan")
BASE, _Target, _plan, _setup = (_plan_mod.BASE, _plan_mod._Target,
                                _plan_mod._plan, _plan_mod._setup)
DATA = [("@data", "/srv/data")]


def _owned_after_sync(action, managed):
    """What `Reconciler._owned_after_sync` computes for this action."""
    declared = set(action.managed_keys().get("btrfs_subvolumes", []))
    return sorted(action.actual() & (set(managed) | declared))


def test_actual_reports_a_mounted_subvolume_the_config_omits(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE, mounted=dict(BASE + DATA))
    assert a.actual() == {"ROOT:@home", "ROOT:@data"}


def test_actual_works_from_an_empty_config(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, BASE, mounted=dict(BASE + DATA))
    a = BtrfsSubvolumeAction({}, SimpleNamespace(target=_Target(tmp_path)))
    assert a.actual() == {"ROOT:@home", "ROOT:@data"}
    assert a.managed_keys() == {"btrfs_subvolumes": []}


def test_actual_needs_both_the_mount_and_the_fstab_line(tmp_path, monkeypatch):
    fstab = "UUID=1111-2222 / btrfs rw,subvol=/@ 0 0\n"
    a = _setup(tmp_path, monkeypatch, BASE, mounted=dict(BASE + DATA), fstab=fstab)
    assert a.actual() == set()


def test_managed_keys_never_claims_a_hand_made_subvolume(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE, mounted=dict(BASE + DATA))
    assert a.managed_keys() == {"btrfs_subvolumes": ["ROOT:@home"]}


def test_subvolume_dropped_from_config_keeps_its_ownership_through_sync(
        tmp_path, monkeypatch):
    """apply @data -> sync -> drop @data from the config -> plan shows REMOVE."""
    mounted = dict(BASE + DATA)
    with_data = _setup(tmp_path, monkeypatch, BASE + DATA, mounted=mounted)
    managed = with_data.managed_keys()["btrfs_subvolumes"]
    assert "ROOT:@data" in managed                       # apply claimed it
    for seed in (BASE, None):                            # edited config / `{}`
        owner = (_setup(tmp_path, monkeypatch, seed, mounted=mounted) if seed
                 else BtrfsSubvolumeAction({}, SimpleNamespace(target=_Target(tmp_path))))
        assert "ROOT:@data" in _owned_after_sync(owner, managed)
    after_sync = _owned_after_sync(
        _setup(tmp_path, monkeypatch, BASE, mounted=mounted), managed)
    final = _setup(tmp_path, monkeypatch, BASE, mounted=mounted)
    assert (Op.REMOVE, "ROOT:@data") in _plan(final, managed=after_sync)


def test_hand_made_undeclared_subvolume_is_never_planned_for_removal(
        tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE, mounted=dict(BASE + DATA))
    managed = a.managed_keys()["btrfs_subvolumes"]       # what an apply records
    assert (Op.REMOVE, "ROOT:@data") not in _plan(a, managed=managed)


def test_actual_label_comes_from_lsblk(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE, labels=("POOL",))
    assert a.actual() == {"POOL:@home"}


# --- the root is never wanted-planned ------------------------------------- #

def test_root_booted_into_a_snapshot_plans_nothing_for_it(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE,
               mounted={"@snapshots/3/snapshot": "/", "@home": "/home"})
    assert _plan(a) == []


def test_root_fstab_line_without_subvol_plans_nothing_for_it(tmp_path, monkeypatch):
    fstab = ("UUID=1111-2222 / btrfs rw,subvolid=256 0 0\n"
             "UUID=1111-2222 /home btrfs rw,subvol=/@home,compress=zstd:3 0 0\n")
    a = _setup(tmp_path, monkeypatch, BASE, fstab=fstab)
    assert _plan(a) == []


def test_diff_skips_a_wanted_at_the_root():
    live = {"ROOT": Live(mounted={"@snap": "/"}, fstab={}, existing={"@snap"})}
    wanted = [Wanted("ROOT:@", "ROOT", "@", "/")]
    assert diff(wanted, live, {"ROOT"}, []) == []


def test_diff_still_plans_a_non_root_wanted():
    live = {"ROOT": Live(mounted={"@": "/"}, fstab={}, existing={"@"})}
    wanted = [Wanted("ROOT:@", "ROOT", "@", "/"),
              Wanted("ROOT:@x", "ROOT", "@x", "/x")]
    assert diff(wanted, live, {"ROOT"}, []) == [
        (Op.INSTALL, "ROOT:@x", "create + move contents")]
