"""Day-2 btrfs subvolumes: what the action PLANS from the probed machine.

`Command.execute` is canned by argv (findmnt, btrfs, lsblk); the target is a
tmp_path carrying `etc/fstab`. Host paths printed by findmnt live under the
target root, so the action must strip that prefix to get canonical mountpoints.
"""
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.btrfs_subvolume_action import BtrfsSubvolumeAction
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.state.change import Op


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


UUID = "1111-2222"


def _fstab(*pairs):
    return "".join(
        f"UUID={UUID} {mp} btrfs rw,subvol=/{name},compress=zstd:3 0 0\n"
        for mp, name in pairs)


class _Machine:
    """Canned host: which subvolumes are mounted where, what btrfs lists."""

    def __init__(self, root, mounted, existing, labels=("ROOT",)):
        self.root, self.mounted, self.existing, self.labels = root, mounted, existing, labels

    def __call__(self, cmd, args, **kw):
        assert "target" not in kw and not kw.get("run_as_chroot"), "probe must run on the host"
        if cmd == "lsblk":
            out = "\n".join(self.labels) + "\n"
        elif cmd == "findmnt":
            out = "".join(
                f"{self.root}{'' if mp == '/' else mp} /dev/mapper/crypt[/{n}] {UUID} "
                f"rw,relatime,subvolid=1,subvol=/{n}\n"
                for n, mp in self.mounted.items())
        elif cmd == "btrfs":
            out = "".join(f"ID {256 + i} gen 9 top level 5 path {n}\n"
                          for i, n in enumerate(self.existing))
        else:
            raise AssertionError(cmd)
        return SimpleNamespace(stdout=out, returncode=0, stderr="")


def _cfg(subvols):
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "ROOT", "size": "rest", "filesystem": "btrfs",
         "btrfs_subvolumes": [{"name": n, "mountpoint": mp} for n, mp in subvols]}]}]}}


BASE = [("@", "/"), ("@home", "/home")]


def _setup(tmp_path, monkeypatch, declared, mounted=None, existing=None,
           labels=("ROOT",), fstab=None):
    mounted = dict((n, mp) for n, mp in BASE) if mounted is None else mounted
    existing = list(mounted) if existing is None else existing
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc" / "fstab").write_text(
        _fstab(*[(mp, n) for n, mp in mounted.items()]) if fstab is None else fstab)
    monkeypatch.setattr(Command, "execute",
                        staticmethod(_Machine(str(tmp_path), mounted, existing, labels)))
    return BtrfsSubvolumeAction(_cfg(declared), SimpleNamespace(target=_Target(tmp_path)))


def _plan(action, managed=()):
    return [(c.op, c.item) for c in action.plan(managed=list(managed))]


def test_fresh_disk_plans_nothing(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE + [("@var_lib_ollama", "/var/lib/ollama")],
               labels=())
    assert a.plan(managed=[]) == []


def test_new_subvolume_on_installed_disk_is_planned(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE + [("@var_lib_ollama", "/var/lib/ollama")])
    changes = a.plan(managed=[])
    assert [(c.domain, c.op, c.item) for c in changes] == [
        ("btrfs_subvolumes", Op.INSTALL, "ROOT:@var_lib_ollama")]


def test_install_over_non_empty_dir_is_destructive(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE + [("@var_lib_ollama", "/var/lib/ollama")])
    d = tmp_path / "var/lib/ollama"
    d.mkdir(parents=True)
    assert a.plan(managed=[])[0].destructive is False   # empty dir
    (d / "model.bin").write_text("x")
    assert a.plan(managed=[])[0].destructive is True


def test_move_is_modify_and_never_destructive(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, [("@", "/"), ("@home", "/srv/home")])
    (tmp_path / "srv/home").mkdir(parents=True)
    (tmp_path / "srv/home/f").write_text("x")
    (c,) = a.plan(managed=[])
    assert (c.op, c.item, c.destructive) == (Op.MODIFY, "ROOT:@home", False)


def test_converged_plans_nothing(tmp_path, monkeypatch):
    assert _setup(tmp_path, monkeypatch, BASE).plan(managed=[]) == []


def test_actual_lists_converged_keys(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE + [("@new", "/new")])
    assert a.actual() == {"ROOT:@", "ROOT:@home"}
    assert a.managed_keys() == {"btrfs_subvolumes": ["ROOT:@", "ROOT:@home"]}


def test_dropped_disks_block_removes_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, BASE)
    a = BtrfsSubvolumeAction({}, SimpleNamespace(target=_Target(tmp_path)))
    assert a.plan(managed=["ROOT:@home"]) == []


def test_owned_subvolume_dropped_from_list_is_removed(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, [("@", "/")])
    assert _plan(a, managed=["ROOT:@home"]) == [(Op.REMOVE, "ROOT:@home")]


def test_unowned_subvolume_is_left_alone(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, [("@", "/")])
    assert a.plan(managed=[]) == []


def test_missing_fstab_line_is_planned(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE, fstab=_fstab(("/", "@")))
    assert _plan(a) == [(Op.INSTALL, "ROOT:@home")]


def test_no_target_is_inert(tmp_path, monkeypatch):
    a = BtrfsSubvolumeAction(_cfg(BASE), None)
    assert a.actual() == set() and a.plan(managed=[]) == []


def test_import_state_is_empty_and_empty_config():
    assert BtrfsSubvolumeAction({}, None).import_state() == {}
    assert BtrfsSubvolumeAction.empty_config() == {}


def test_unmounted_partition_does_not_borrow_another_filesystem(tmp_path, monkeypatch):
    """DATA declares `@` too, but its filesystem is not mounted: ROOT's `@`
    must not be read as DATA's (that would plan MODIFY DATA:@ / -> /data)."""
    a = _setup(tmp_path, monkeypatch, BASE, labels=("ROOT", "DATA"))
    cfg = _cfg(BASE)
    cfg["disks"]["disks"][0]["partitions"][0]["size"] = "20GiB"
    cfg["disks"]["disks"][0]["partitions"].append(
        {"label": "DATA", "size": "rest", "filesystem": "btrfs",
         "btrfs_subvolumes": [{"name": "@", "mountpoint": "/data"}]})
    a = BtrfsSubvolumeAction(cfg, SimpleNamespace(target=_Target(tmp_path)))
    assert a.plan(managed=[]) == []
    assert "DATA" not in a._live()


def test_owned_key_of_an_undeclared_partition_is_not_removed(tmp_path, monkeypatch):
    a = _setup(tmp_path, monkeypatch, BASE)
    assert a.plan(managed=["GONE:@x"]) == []


def test_failing_probe_is_logged_not_silent(tmp_path, monkeypatch):
    from dasik.lib.logging import run_logger
    a = _setup(tmp_path, monkeypatch, BASE)
    seen = []
    monkeypatch.setattr(run_logger.get(), "warning",
                        lambda m, detail="": seen.append(m))
    monkeypatch.setattr(Command, "execute",
                        staticmethod(lambda *x, **k: (_ for _ in ()).throw(OSError("boom"))))
    assert a._host("findmnt", []) == "" and seen
