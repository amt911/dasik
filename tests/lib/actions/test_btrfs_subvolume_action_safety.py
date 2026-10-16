"""Day-2 btrfs subvolumes: the guards of the destructive moves (final review).

Same recording fake as the apply tests (`Rig`): the top level lives at
<root>/run/dasik-btrfs-top with one directory per subvolume.
"""
import importlib.util
import os
from pathlib import Path

import pytest

from dasik.lib.actions.btrfs_subvolume_action import BtrfsSubvolumeAction
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.exceptions.exceptions import CommandExecutionError, SubvolumeChangeRefused
from dasik.lib.state.change import Change, Op


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_apply = _sibling("test_btrfs_subvolume_action_apply")
Rig, OLLAMA, UUID = _apply.Rig, _apply.OLLAMA, _apply.UUID
_ollama_dir, _install, _remove, _removal = (_apply._ollama_dir, _apply._install,
                                            _apply._remove, _apply._removal)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), (OLLAMA, "/var/lib/ollama")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    return r


# --- 3: data written during the copy is never deleted ---------------------- #

def test_file_written_to_the_source_after_the_copy_is_not_deleted(rig):
    d = _ollama_dir(rig)
    rig.cp_hook = lambda: (d / "late.bin").write_text("late")    # a racing writer
    _install(rig)
    assert (d / "late.bin").read_text() == "late"
    assert (rig.top / OLLAMA / "model.bin").read_text() == "weights"
    assert not (d.parent / (d.name + ".dasik-moving")).exists()


def test_source_is_renamed_aside_before_the_copy_and_copied_from_there(rig):
    d = _ollama_dir(rig)
    seen = {}
    rig.cp_hook = lambda: seen.update(
        staged=(d.parent / (d.name + ".dasik-moving")).is_dir(), fresh=d.is_dir())
    _install(rig)
    cp = [a for c, a in rig.calls if c == "cp"][0]
    assert cp[-2].endswith(".dasik-moving/.")
    assert seen == {"staged": True, "fresh": True}


# --- 4: owner/mode of the directory follow, even when it is empty ---------- #

def test_remove_copies_even_an_empty_subvolume_back(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    (r.top / OLLAMA / "model.bin").unlink()
    _remove(r)
    assert r.find("cp") is not None


# --- 5: a failed probe before a delete is a refusal ------------------------ #

def test_failed_nested_probe_before_add_is_a_refusal(rig):
    d = _ollama_dir(rig)
    rig.list_rc = 1
    with pytest.raises(SubvolumeChangeRefused, match="Nothing was changed"):
        _install(rig)
    assert (d / "model.bin").exists()
    assert rig.find("btrfs", "subvolume", "create") is None and rig.find("cp") is None


def test_failed_nested_probe_before_remove_is_a_refusal(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    (r.top / "@" / "var/lib/ollama").mkdir(parents=True)
    r.list_rc = 1
    with pytest.raises(SubvolumeChangeRefused, match="Nothing was"):
        _remove(r)
    assert r.find("umount", str(tmp_path / "var/lib/ollama")) is None
    assert r.find("btrfs", "subvolume", "delete") is None


# --- 6: whitespace / commas / control chars would corrupt fstab ------------ #

@pytest.mark.parametrize("name,mp", [
    ("@a b", "/srv/x"), ("@a,b", "/srv/x"), ("@a\tb", "/srv/x"), ("@a\nb", "/srv/x"),
    ("@a", "/srv/a b"), ("@a", "/srv/a,b"), ("@a", "/srv/a\tb"), ("@a", "/srv/a\x01b"),
    ("@a", "/srv/a\x7fb"),
])
def test_whitespace_commas_and_control_chars_are_refused(tmp_path, monkeypatch, name, mp):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), (name, mp)])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    with pytest.raises(SubvolumeChangeRefused, match="Nothing was changed"):
        r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, f"ROOT:{name}")])
    assert r.find("btrfs", "subvolume", "create") is None and r.find("mount", "subvolid=5") is None


# --- 7: never mount over an existing mount --------------------------------- #

def test_adding_above_an_existing_mount_is_refused(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), ("@var", "/var")])
    r.other_mounts = [str(tmp_path / "var/log")]
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    with pytest.raises(SubvolumeChangeRefused, match="mounted below"):
        r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@var")])
    assert r.find("btrfs", "subvolume", "create") is None and r.find("cp") is None


def test_a_sibling_prefix_mount_does_not_block(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), ("@var", "/var")])
    r.other_mounts = [str(tmp_path / "variable/x")]
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@var")])
    assert r.find("btrfs", "subvolume", "create") is not None


# --- 8: daemon-reload after an fstab edit on a live systemd host ----------- #

def _fstab_only(tmp_path, monkeypatch, *, live, systemd=True):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"})
    (tmp_path / "etc" / "fstab").write_text(f"UUID={UUID} / btrfs rw,subvol=/@ 0 0\n")
    r.action.context.target.is_chroot = not live
    real = os.path.isdir
    monkeypatch.setattr(os.path, "isdir",
                        lambda p: systemd if p == "/run/systemd/system" else real(p))
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@home")])
    return r


def test_daemon_reload_runs_once_after_an_fstab_edit_on_a_live_host(tmp_path, monkeypatch):
    r = _fstab_only(tmp_path, monkeypatch, live=True)
    assert r.argvs().count(("systemctl", ["daemon-reload"])) == 1
    assert r.sysctl_kw["target"] is r.action.context.target


def test_no_daemon_reload_on_an_install_target(tmp_path, monkeypatch):
    r = _fstab_only(tmp_path, monkeypatch, live=False)
    assert "systemctl" not in r.names()


def test_no_daemon_reload_without_a_running_systemd(tmp_path, monkeypatch):
    r = _fstab_only(tmp_path, monkeypatch, live=True, systemd=False)
    assert "systemctl" not in r.names()


def test_no_daemon_reload_when_fstab_did_not_change(tmp_path, monkeypatch, rig):
    rig.action.context.target.is_chroot = False
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    with pytest.raises(SubvolumeChangeRefused):
        rig.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@ghost")])
    assert "systemctl" not in rig.names()


# --- 10/11: a failed copy leaves no half-filled leftovers ------------------ #

def test_failed_create_and_copy_deletes_the_subvolume_this_item_created(rig):
    d = _ollama_dir(rig)
    rig.reflink_rc = rig.plain_rc = 1
    rig.partial = True
    with pytest.raises(CommandExecutionError):
        _install(rig)
    assert rig.find("btrfs", "subvolume", "delete", OLLAMA) is not None
    assert OLLAMA not in os.listdir(rig.top)
    assert (d / "model.bin").read_text() == "weights"
    assert not (d.parent / (d.name + ".dasik-moving")).exists()
    # the next run is not stuck on "both hold data"
    rig.reflink_rc = rig.plain_rc = 0
    _install(rig)
    assert (rig.top / OLLAMA / "model.bin").read_text() == "weights"


def test_failed_copy_into_an_adopted_subvolume_empties_it_but_keeps_it(rig):
    rig.existing.append(OLLAMA)
    (rig.top / OLLAMA).mkdir()
    d = _ollama_dir(rig)
    rig.reflink_rc = rig.plain_rc = 1
    rig.partial = True
    with pytest.raises(CommandExecutionError):
        _install(rig)
    assert (rig.top / OLLAMA).is_dir() and os.listdir(rig.top / OLLAMA) == []
    assert rig.find("btrfs", "subvolume", "delete") is None
    assert (d / "model.bin").read_text() == "weights"


def test_adopting_with_an_empty_source_keeps_the_subvolume_root_untouched(rig):
    rig.existing.append(OLLAMA)
    (rig.top / OLLAMA).mkdir()
    (rig.top / OLLAMA / "keep").write_text("k")
    (rig.top / "@" / "var/lib/ollama").mkdir(parents=True)
    _install(rig)
    assert rig.find("cp") is None      # an empty dir must not restamp the adopted root


def test_failed_copy_back_on_remove_empties_the_parent_side_dir(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    dst = r.top / "@" / "var/lib/ollama"
    dst.mkdir(parents=True)
    r.reflink_rc = r.plain_rc = 1
    r.partial = True
    r.du = "10\tx\n"
    with pytest.raises(SubvolumeChangeRefused, match="remounted"):
        _remove(r)
    assert dst.is_dir() and os.listdir(dst) == []
    assert (r.top / OLLAMA / "model.bin").exists()


# --- 12: adopting at a new mountpoint leaves no second fstab line ---------- #

def test_adopt_at_a_new_mountpoint_drops_the_old_lines_of_that_subvolume(rig):
    rig.existing.append(OLLAMA)
    (rig.top / OLLAMA).mkdir()
    with open(rig.root / "etc" / "fstab", "a") as f:
        f.write(f"UUID={UUID} /old/place btrfs rw,subvol=/{OLLAMA} 0 0\n"
                f"UUID={UUID} /older btrfs rw,subvol={OLLAMA},noatime 0 0\n"
                f"UUID=OTHER /elsewhere btrfs rw,subvol=/{OLLAMA} 0 0\n"
                f"UUID={UUID} /srv/x btrfs rw,subvol=/{OLLAMA}_x 0 0\n")
    _install(rig)
    fstab = rig.fstab()
    assert "/old/place" not in fstab and "/older " not in fstab
    assert f"UUID={UUID} /var/lib/ollama btrfs" in fstab
    assert "UUID=OTHER /elsewhere" in fstab and "/srv/x " in fstab


# --- 13: fstab is durable and keeps its mode ------------------------------- #

def test_fstab_rewrite_fsyncs_and_keeps_the_mode(rig, monkeypatch):
    path = rig.root / "etc" / "fstab"
    os.chmod(path, 0o600)
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    _ollama_dir(rig)
    _install(rig)
    assert synced
    assert (path.stat().st_mode & 0o777) == 0o600


# --- re-review: committed copies, late writers, exact-path subvolume ------- #

def test_failing_delete_of_the_old_copy_never_touches_the_new_subvolume(rig, monkeypatch):
    import shutil
    d = _ollama_dir(rig)

    def half_delete(path, *a, **k):
        raise OSError("immutable file")
    monkeypatch.setattr(shutil, "rmtree", half_delete)
    _install(rig)          # warns, does not raise, does not wipe dst
    assert (rig.top / OLLAMA / "model.bin").read_text() == "weights"
    assert (rig.top / OLLAMA / "sub" / "x").exists()
    assert (d.parent / (d.name + ".dasik-moving") / "model.bin").exists()
    assert ("btrfs", ["subvolume", "delete", str(rig.top / OLLAMA)]) not in rig.argvs()
    assert rig.find("mount", f"subvol={OLLAMA}") is not None


def test_interrupted_delete_of_the_old_copy_keeps_the_new_subvolume(rig, monkeypatch):
    import shutil
    _ollama_dir(rig)
    monkeypatch.setattr(shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        _install(rig)
    assert (rig.top / OLLAMA / "model.bin").read_text() == "weights"
    assert ("btrfs", ["subvolume", "delete", str(rig.top / OLLAMA)]) not in rig.argvs()


def test_remove_failing_subvolume_delete_keeps_the_copied_back_data(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    dst = r.top / "@" / "var/lib/ollama"
    dst.mkdir(parents=True)
    real = r.__class__.__call__

    def call(self, cmd, args, **kw):
        if cmd == "btrfs" and args[:2] == ["subvolume", "delete"]:
            raise CommandExecutionError("busy")
        return real(self, cmd, args, **kw)
    monkeypatch.setattr(r.__class__, "__call__", call)
    with pytest.raises(CommandExecutionError):
        _remove(r)
    assert (dst / "model.bin").read_text() == "weights"


def test_files_that_appear_under_the_mountpoint_block_the_mount(rig, tmp_path):
    _ollama_dir(rig)
    host = tmp_path / "var/lib/ollama"

    def late():
        host.mkdir(parents=True, exist_ok=True)
        (host / "late.bin").write_text("x")
    rig.cp_hook = late
    with pytest.raises(SubvolumeChangeRefused, match="holds the copy"):
        _install(rig)
    assert rig.find("mount", f"subvol={OLLAMA}") is None
    assert "var/lib/ollama" not in rig.fstab()
    assert (rig.top / OLLAMA / "model.bin").exists() and (host / "late.bin").exists()


def test_a_subvolume_exactly_at_the_path_is_refused_even_if_show_fails(rig):
    d = _ollama_dir(rig)
    rig.show_rc = 1
    rig.deep = ["@/var/lib/ollama"]
    with pytest.raises(SubvolumeChangeRefused, match="nested"):
        _install(rig)
    assert (d / "model.bin").exists() and rig.find("btrfs", "subvolume", "create") is None
