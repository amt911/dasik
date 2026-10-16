"""Day-2 btrfs subvolumes: the APPLY mechanics and the refusals.

`Command.execute` is a recording fake that performs the filesystem effects on
tmp dirs: the "top level" lives at <root>/run/dasik-btrfs-top, with one dir per
subvolume (`@`, `@home`, ...) and the host mountpoints under <root>.
"""
import os
import shutil
from types import SimpleNamespace

import pytest

from dasik.lib.actions.btrfs_subvolume_action import BtrfsSubvolumeAction
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.exceptions.exceptions import CommandExecutionError, SubvolumeChangeRefused
from dasik.lib.state.change import Change, Op

UUID = "1111-2222"
OLLAMA = "@var_lib_ollama"


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


class Rig:
    def __init__(self, tmp_path, mounted, existing=None, declared=None):
        self.root = tmp_path
        self.top = tmp_path / "run" / "dasik-btrfs-top"
        self.mounted = dict(mounted)
        self.existing = list(self.mounted) if existing is None else list(existing)
        self.calls = []
        self.reflink_rc = 0
        self.plain_rc = 0
        self.show_rc = 1
        self.nested = ""
        self.du = "100\tx\n"
        self.lsattr = "--------------e------- x\n"
        self.fail_mount_new = False
        self.deep = []        # extra `btrfs subvolume list` lines (nested subvolumes)
        (tmp_path / "etc").mkdir(exist_ok=True)
        (tmp_path / "etc" / "fstab").write_text("".join(
            f"UUID={UUID} {mp} btrfs rw,subvol=/{n},compress=zstd:3 0 0\n"
            for n, mp in self.mounted.items()))
        (tmp_path / "etc" / "fstab").write_text(
            (tmp_path / "etc" / "fstab").read_text() + "UUID=ZZ /boot vfat defaults 0 2\n")
        for n in self.existing:
            (self.top / n).mkdir(parents=True, exist_ok=True)
        decl = declared if declared is not None else [("@", "/"), ("@home", "/home")]
        cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
            {"label": "ROOT", "size": "rest", "filesystem": "btrfs",
             "btrfs_subvolumes": [{"name": n, "mountpoint": mp,
                                   "mount_options": ["compress-force=zstd:3"]}
                                  for n, mp in decl]}]}]}}
        self.action = BtrfsSubvolumeAction(cfg, SimpleNamespace(target=_Target(tmp_path)))

    def argvs(self):
        return [(c, list(a)) for c, a in self.calls]

    def names(self):
        return [c for c, _a in self.calls]

    def find(self, cmd, *needles):
        for i, (c, a) in enumerate(self.calls):
            if c == cmd and all(any(n in x for x in a) for n in needles):
                return i
        return None

    def __call__(self, cmd, args, **kw):
        assert "target" not in kw and not kw.get("run_as_chroot"), "host only"
        self.calls.append((cmd, list(args)))
        ok = SimpleNamespace(stdout="", returncode=0, stderr="")
        if cmd == "lsblk":
            return SimpleNamespace(stdout="ROOT\n", returncode=0, stderr="")
        if cmd == "findmnt":
            out = "".join(
                f"{self.root}{'' if mp == '/' else mp} /dev/mapper/crypt[/{n}] {UUID} "
                f"rw,subvolid=1,subvol=/{n}\n" for n, mp in self.mounted.items())
            return SimpleNamespace(stdout=out, returncode=0, stderr="")
        if cmd == "btrfs" and args[:2] == ["subvolume", "list"]:
            if "-o" in args:
                return SimpleNamespace(stdout=self.nested, returncode=0, stderr="")
            out = "".join(f"ID {256 + i} gen 9 top level 5 path {n}\n"
                          for i, n in enumerate(self.existing))
            out += "".join(f"ID 900 gen 9 top level 260 path {d}\n" for d in self.deep)
            return SimpleNamespace(stdout=out, returncode=0, stderr="")
        if cmd == "btrfs" and args[:2] == ["subvolume", "show"]:
            return SimpleNamespace(stdout="", returncode=self.show_rc, stderr="")
        if cmd == "btrfs" and args[:2] == ["subvolume", "create"]:
            os.makedirs(args[2])
            self.existing.append(os.path.basename(args[2]))
            return ok
        if cmd == "btrfs" and args[:2] == ["subvolume", "delete"]:
            shutil.rmtree(args[2])
            self.existing.remove(os.path.basename(args[2]))
            return ok
        if cmd == "cp":
            rc = self.reflink_rc if "--reflink=always" in args else self.plain_rc
            if rc == 0:
                src, dst = args[-2][:-2], args[-1]
                shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
            if rc != 0 and kw.get("check"):
                raise CommandExecutionError("cp failed")
            return SimpleNamespace(stdout="", returncode=rc, stderr="no")
        if cmd == "du":
            return SimpleNamespace(stdout=self.du, returncode=0, stderr="")
        if cmd == "lsattr":
            return SimpleNamespace(stdout=self.lsattr, returncode=0, stderr="")
        if cmd == "mount" and "subvolid=5" not in args[1]:
            if self.fail_mount_new:
                raise CommandExecutionError("mount failed")
            sub = [o for o in args[1].split(",") if o.startswith("subvol=")][0][7:]
            self.mounted[sub] = args[-1][len(str(self.root)):] or "/"
        if cmd == "umount" and args[0] != str(self.top):
            mp = args[0][len(str(self.root)):] or "/"
            self.mounted = {n: m for n, m in self.mounted.items() if m != mp}
        return ok     # mount / umount / chattr

    def fstab(self):
        return (self.root / "etc" / "fstab").read_text()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), (OLLAMA, "/var/lib/ollama")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    return r


def _ollama_dir(rig):
    d = rig.top / "@" / "var/lib/ollama"
    d.mkdir(parents=True)
    (d / "model.bin").write_text("weights")
    (d / "sub").mkdir()
    (d / "sub" / "x").write_text("x")
    return d


def _install(rig, name=OLLAMA):
    rig.action.apply([Change("btrfs_subvolumes", Op.INSTALL, f"ROOT:{name}")])


def test_install_create_runs_create_copy_mount_fstab_in_order(rig):
    _ollama_dir(rig)
    _install(rig)
    create = rig.find("btrfs", "subvolume", "create")
    cp = rig.find("cp", "--reflink=always")
    mnt = rig.find("mount", f"compress-force=zstd:3,subvol={OLLAMA}")
    assert None not in (create, cp, mnt) and create < cp < mnt
    assert (rig.top / OLLAMA / "model.bin").read_text() == "weights"
    assert (rig.top / OLLAMA / "sub" / "x").exists()
    assert (f"UUID={UUID} /var/lib/ollama btrfs "
            f"compress-force=zstd:3,subvol={OLLAMA} 0 0\n") in rig.fstab()
    assert "UUID=ZZ /boot vfat" in rig.fstab()
    assert rig.calls[rig.find("mount", f"subvol={OLLAMA}")][1][-1] == \
        str(rig.root / "var/lib/ollama")


def test_install_deletes_old_contents_after_copy(rig):
    d = _ollama_dir(rig)
    _install(rig)
    assert d.is_dir() and os.listdir(d) == []


def test_top_level_is_mounted_by_subvolid_and_cleaned_up(rig):
    _ollama_dir(rig)
    _install(rig)
    assert rig.find("mount", "subvolid=5", "/dev/mapper/crypt") is not None
    assert rig.find("umount", str(rig.top)) is not None


def test_top_level_mount_unmounted_even_on_failure(rig):
    _ollama_dir(rig)
    rig.reflink_rc = rig.plain_rc = 1
    rig.du = "999999999999999999\tx\n"
    with pytest.raises(SubvolumeChangeRefused):
        _install(rig)
    assert rig.find("umount", str(rig.top)) is not None


def test_empty_source_dir_creates_without_copy(rig):
    _install(rig)
    assert rig.find("btrfs", "subvolume", "create") is not None
    assert rig.find("cp") is None
    assert (rig.root / "var/lib/ollama").is_dir()
    assert rig.find("mount", f"subvol={OLLAMA}") is not None


def test_nocow_source_dir_keeps_nocow_before_copy(rig):
    _ollama_dir(rig)
    rig.lsattr = "---------------C------ x\n"
    _install(rig)
    chattr = rig.find("chattr", "+C", str(rig.top / OLLAMA))
    assert chattr is not None and rig.find("btrfs", "subvolume", "create") < chattr \
        < rig.find("cp")


def test_copy_failure_deletes_nothing_and_raises(rig):
    d = _ollama_dir(rig)
    rig.reflink_rc = 1
    rig.plain_rc = 1
    with pytest.raises(CommandExecutionError):
        _install(rig)
    assert (d / "model.bin").read_text() == "weights"
    assert OLLAMA in "".join(p for p in os.listdir(rig.top))   # subvolume left, not deleted
    assert "var/lib/ollama" not in rig.fstab()
    assert rig.find("mount", f"subvol={OLLAMA}") is None


def test_reflink_failure_falls_back_to_plain_copy_when_space_allows(rig):
    d = _ollama_dir(rig)
    rig.reflink_rc = 1
    _install(rig)
    plain = [a for c, a in rig.calls if c == "cp" and "--reflink=always" not in a]
    assert len(plain) == 1 and plain[0][0] == "-a"
    assert (rig.top / OLLAMA / "model.bin").exists() and os.listdir(d) == []


def test_reflink_failure_refuses_when_space_short(rig):
    d = _ollama_dir(rig)
    rig.reflink_rc = 1
    rig.du = f"{2 ** 60}\tx\n"
    with pytest.raises(SubvolumeChangeRefused, match="free space"):
        _install(rig)
    assert (d / "model.bin").exists()
    assert not [1 for c, a in rig.calls if c == "cp" and "--reflink=always" not in a]


def test_busy_path_refused_before_any_mutation(rig, tmp_path):
    _ollama_dir(rig)
    host = tmp_path / "var/lib/ollama"
    host.mkdir(parents=True)
    pdir = tmp_path / "proc" / "4242"
    pdir.mkdir()
    (pdir / "comm").write_text("ollama\n")
    os.symlink(host, pdir / "cwd")
    with pytest.raises(SubvolumeChangeRefused, match=r"pid 4242 \(ollama\)"):
        _install(rig)
    assert not {"mount", "umount", "cp"} & set(rig.names()[1:])   # only the findmnt probe
    assert rig.find("btrfs", "subvolume", "create") is None


def test_busy_pids_sees_cwd_and_open_fd_but_not_siblings(tmp_path):
    a = BtrfsSubvolumeAction({}, None)
    host = tmp_path / "data"
    (host / "deep").mkdir(parents=True)
    other = tmp_path / "data2"
    other.mkdir()
    proc = tmp_path / "proc"
    for pid, link in ((1, host / "deep"), (2, other)):
        (proc / str(pid)).mkdir(parents=True)
        os.symlink(link, proc / str(pid) / "cwd")
    (proc / "3" / "fd").mkdir(parents=True)
    os.symlink(other, proc / "3" / "cwd")
    os.symlink(host / "deep" / "f", proc / "3" / "fd" / "5")
    (proc / "self").mkdir()
    assert a._busy_pids(str(host), proc=str(proc)) == [1, 3]


def test_nested_subvolume_at_path_refused(rig):
    _ollama_dir(rig)
    rig.show_rc = 0
    with pytest.raises(SubvolumeChangeRefused, match="nested"):
        _install(rig)
    assert rig.find("btrfs", "subvolume", "create") is None


def test_add_path_already_a_mountpoint_refused(rig, monkeypatch):
    _ollama_dir(rig)
    monkeypatch.setattr(os.path, "ismount", lambda p: p.endswith("var/lib/ollama"))
    with pytest.raises(SubvolumeChangeRefused, match="already a mountpoint"):
        _install(rig)
    assert rig.find("btrfs", "subvolume", "create") is None


def test_adopt_with_both_sides_non_empty_refused(rig):
    rig2 = rig
    rig2.existing.append(OLLAMA)
    (rig2.top / OLLAMA).mkdir()
    (rig2.top / OLLAMA / "old").write_text("o")
    d = _ollama_dir(rig2)
    with pytest.raises(SubvolumeChangeRefused, match="both hold data"):
        _install(rig2)
    assert (d / "model.bin").exists() and rig2.find("cp") is None


def test_adopt_moves_content_without_creating(rig):
    rig.existing.append(OLLAMA)
    (rig.top / OLLAMA).mkdir()
    d = _ollama_dir(rig)
    _install(rig)
    assert rig.find("btrfs", "subvolume", "create") is None
    assert (rig.top / OLLAMA / "model.bin").exists() and os.listdir(d) == []
    assert rig.find("mount", f"subvol={OLLAMA}") is not None


def test_fstab_only_just_appends_and_mounts_nothing(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"})
    (tmp_path / "etc" / "fstab").write_text(
        f"UUID={UUID} / btrfs rw,subvol=/@ 0 0\n")
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@home")])
    assert f"UUID={UUID} /home btrfs compress-force=zstd:3,subvol=@home 0 0" in r.fstab()
    assert "mount" not in r.names() and "cp" not in r.names()


def test_remove_copies_back_unmounts_drops_fstab_deletes(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home", OLLAMA: "/var/lib/ollama"},
            declared=[("@", "/"), ("@home", "/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    (r.top / OLLAMA / "model.bin").write_text("weights")
    (r.top / "@" / "var/lib/ollama").mkdir(parents=True)
    (tmp_path / "etc" / "fstab").write_text(
        r.fstab() + "tmpfs /var/lib/ollama/tmp tmpfs defaults 0 0\n"
        "/dev/sdz1 /var/lib/ollama ext4 defaults 0 2\n")
    r.action.apply([Change("btrfs_subvolumes", Op.REMOVE, f"ROOT:{OLLAMA}")])
    um = r.find("umount", str(tmp_path / "var/lib/ollama"))
    cp = r.find("cp")
    dele = r.find("btrfs", "subvolume", "delete")
    assert um < cp < dele
    assert (r.top / "@" / "var/lib/ollama" / "model.bin").read_text() == "weights"
    assert f"subvol=/{OLLAMA}" not in r.fstab()
    assert "/dev/sdz1 /var/lib/ollama ext4" in r.fstab()     # only btrfs lines go
    assert "UUID=ZZ /boot vfat" in r.fstab() and " /home btrfs" in r.fstab()
    assert not (r.top / OLLAMA).exists()


def _removal(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home", OLLAMA: "/var/lib/ollama"},
            declared=[("@", "/"), ("@home", "/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    (r.top / OLLAMA / "model.bin").write_text("weights")
    return r


def _remove(r):
    r.action.apply([Change("btrfs_subvolumes", Op.REMOVE, f"ROOT:{OLLAMA}")])


def test_remove_refused_when_parent_dir_not_empty(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    p = r.top / "@" / "var/lib/ollama"
    p.mkdir(parents=True)
    (p / "stale").write_text("s")
    with pytest.raises(SubvolumeChangeRefused, match="not empty"):
        _remove(r)
    assert (r.top / OLLAMA / "model.bin").exists()
    assert r.find("umount", str(tmp_path / "var/lib/ollama")) is None
    assert r.find("btrfs", "subvolume", "delete") is None


def test_remove_refused_with_nested_subvolumes(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    r.nested = "ID 300 gen 9 top level 260 path @var_lib_ollama/Q\n"
    with pytest.raises(SubvolumeChangeRefused, match="nested subvolumes"):
        _remove(r)
    assert r.find("umount", str(tmp_path / "var/lib/ollama")) is None
    assert r.find("btrfs", "subvolume", "delete") is None


def test_remove_busy_refused_before_unmount(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    host = tmp_path / "var/lib/ollama"
    host.mkdir(parents=True)
    pdir = tmp_path / "proc" / "77"
    pdir.mkdir()
    (pdir / "comm").write_text("python\n")
    os.symlink(host, pdir / "cwd")
    with pytest.raises(SubvolumeChangeRefused, match=r"pid 77 \(python\)"):
        _remove(r)
    assert r.find("umount", str(host)) is None and r.find("btrfs", "subvolume", "delete") is None


def test_remove_copy_failure_keeps_the_subvolume(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    (r.top / "@" / "var/lib/ollama").mkdir(parents=True)
    r.reflink_rc = r.plain_rc = 1
    r.du = "10\tx\n"
    with pytest.raises(SubvolumeChangeRefused, match="remounted"):
        _remove(r)
    assert (r.top / OLLAMA / "model.bin").exists()
    assert r.find("btrfs", "subvolume", "delete") is None
    assert "/var/lib/ollama" in r.fstab()
    um = r.find("umount", str(tmp_path / "var/lib/ollama"))
    re = r.find("mount", f"subvol=/{OLLAMA}", str(tmp_path / "var/lib/ollama"))
    assert re is not None and um < re
    assert r.calls[re][1][:2] == ["-o", f"rw,subvol=/{OLLAMA},compress=zstd:3"]


def test_remove_copy_failure_and_remount_failure_says_so(tmp_path, monkeypatch):
    r = _removal(tmp_path, monkeypatch)
    (r.top / "@" / "var/lib/ollama").mkdir(parents=True)
    r.reflink_rc = r.plain_rc = 1
    r.du = "10\tx\n"
    r.fail_mount_new = True
    with pytest.raises(SubvolumeChangeRefused, match="mount it by hand"):
        _remove(r)
    assert r.find("btrfs", "subvolume", "delete") is None


def test_root_never_touched(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/")])   # @home declared at /
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    for op in (Op.INSTALL, Op.MODIFY):
        with pytest.raises(SubvolumeChangeRefused, match="root"):
            r.action.apply([Change("btrfs_subvolumes", op, "ROOT:@home")])
    with pytest.raises(SubvolumeChangeRefused, match="root"):
        r.action.apply([Change("btrfs_subvolumes", Op.REMOVE, "ROOT:@")])
    assert not {"umount", "cp"} & set(r.names())
    assert r.find("btrfs", "subvolume", "delete") is None


def test_modify_moves_mountpoint_and_rewrites_fstab(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/srv/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@home")])
    um = r.find("umount", str(tmp_path / "home"))
    mt = r.find("mount", "compress-force=zstd:3,subvol=@home", str(tmp_path / "srv/home"))
    assert None not in (um, mt) and um < mt
    assert (tmp_path / "srv/home").is_dir()
    fstab = r.fstab()
    assert " /home btrfs" not in fstab
    assert f"UUID={UUID} /srv/home btrfs compress-force=zstd:3,subvol=@home 0 0" in fstab
    assert "UUID=ZZ /boot vfat" in fstab and "cp" not in r.names()


def test_modify_busy_refused_before_unmount(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/srv/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    pdir = tmp_path / "proc" / "9"
    pdir.mkdir(parents=True)
    (pdir / "comm").write_text("firefox\n")
    (tmp_path / "home").mkdir()
    os.symlink(tmp_path / "home", pdir / "cwd")
    with pytest.raises(SubvolumeChangeRefused, match="pid 9"):
        r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@home")])
    assert r.find("umount", str(tmp_path / "home")) is None
    assert " /home btrfs" in r.fstab()


def test_unmounted_filesystem_refuses(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"})
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    r.mounted.clear()
    with pytest.raises(SubvolumeChangeRefused, match="not mounted"):
        r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@home")])


def test_undeclared_item_refuses(rig):
    with pytest.raises(SubvolumeChangeRefused, match="not declared"):
        rig.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@ghost")])


def _two_items(r):
    r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@var"),
                    Change("btrfs_subvolumes", Op.INSTALL, "ROOT:@var_log")])


def test_nested_additions_reprobe_so_logs_are_not_hidden(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), ("@var", "/var"),
                      ("@var_log", "/var/log")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    (r.top / "@" / "var/log").mkdir(parents=True)
    (r.top / "@" / "var/log/syslog").write_text("logs")
    (r.top / "@" / "var/cache").mkdir()
    _two_items(r)
    assert (r.top / "@var_log" / "syslog").read_text() == "logs"
    assert (r.top / "@var" / "cache").is_dir()
    assert not (r.top / "@var" / "log" / "syslog").exists()
    assert not (r.top / "@" / "var" / "log" / "syslog").exists()


def test_move_onto_non_empty_target_refused_nothing_unmounted(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/srv/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(BtrfsSubvolumeAction, "_PROC", str(tmp_path / "proc"))
    (tmp_path / "proc").mkdir()
    (r.top / "@" / "srv/home").mkdir(parents=True)
    (r.top / "@" / "srv/home/hidden").write_text("h")
    with pytest.raises(SubvolumeChangeRefused, match="hide that data"):
        r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@home")])
    assert r.find("umount", str(tmp_path / "home")) is None
    assert r.find("mount", "subvol=@home") is None
    assert " /home btrfs" in r.fstab()


def test_move_onto_a_subvolume_refused_nothing_unmounted(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/srv/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    (r.top / "@" / "srv/home").mkdir(parents=True)
    r.show_rc = 0
    with pytest.raises(SubvolumeChangeRefused, match="nested"):
        r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@home")])
    assert r.find("umount", str(tmp_path / "home")) is None


def test_move_onto_existing_mountpoint_refused(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/srv/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    monkeypatch.setattr(os.path, "ismount", lambda p: p.endswith("srv/home"))
    with pytest.raises(SubvolumeChangeRefused, match="already a mountpoint"):
        r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@home")])
    assert r.find("umount", str(tmp_path / "home")) is None


def test_move_of_the_root_subvolume_refused(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/srv/root"), ("@home", "/home")])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    with pytest.raises(SubvolumeChangeRefused, match="root subvolume"):
        r.action.apply([Change("btrfs_subvolumes", Op.MODIFY, "ROOT:@")])
    assert r.find("umount") is None


@pytest.mark.parametrize("name,mp", [
    ("/@foo", "/srv/foo"), ("@a/b", "/srv/foo"), ("..", "/srv/foo"),
    ("@foo", "srv/foo"), ("@foo", "/home/../srv"), ("@foo", "/srv//foo"),
    ("@foo", "/srv/foo/"),
])
def test_declarations_that_could_escape_are_refused(tmp_path, monkeypatch, name, mp):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home"), (name, mp)])
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    with pytest.raises(SubvolumeChangeRefused, match="Nothing was changed"):
        r.action.apply([Change("btrfs_subvolumes", Op.INSTALL, f"ROOT:{name}")])
    assert not {"umount", "cp"} & set(r.names())
    assert r.find("btrfs", "subvolume", "create") is None
    assert r.find("mount", "subvolid=5") is None


def test_remove_with_a_dotdot_mountpoint_never_copies_outside(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"@": "/", "@home": "/home"},
            declared=[("@", "/"), ("@home", "/home")])
    r.existing.append("@x")
    (r.top / "@x").mkdir()
    (r.top / "@x" / "f").write_text("f")
    (tmp_path / "etc" / "fstab").write_text(
        r.fstab() + f"UUID={UUID} /home/../srv btrfs subvol=/@x 0 0\n")
    monkeypatch.setattr(Command, "execute", staticmethod(r))
    with pytest.raises(SubvolumeChangeRefused, match="normalized"):
        r.action.apply([Change("btrfs_subvolumes", Op.REMOVE, "ROOT:@x")])
    assert not (r.top / "srv").exists() and (r.top / "@x" / "f").exists()
    assert r.find("btrfs", "subvolume", "delete") is None


def test_path_resolving_outside_the_top_level_is_refused(rig, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    (rig.top / "@" / "var/lib").mkdir(parents=True)
    os.symlink(outside, rig.top / "@" / "var/lib/ollama")
    with pytest.raises(SubvolumeChangeRefused, match="outside the btrfs top level"):
        _install(rig)
    assert (outside / "keep").exists()
    assert rig.find("btrfs", "subvolume", "create") is None


def test_subvolume_below_the_add_path_refused_not_flattened(rig):
    d = _ollama_dir(rig)
    rig.deep = ["@/var/lib/ollama/Q"]
    with pytest.raises(SubvolumeChangeRefused, match="contains nested subvolumes"):
        _install(rig)
    assert (d / "model.bin").exists()
    assert rig.find("btrfs", "subvolume", "create") is None and rig.find("cp") is None


def test_sibling_prefix_subvolume_does_not_block(rig):
    _ollama_dir(rig)
    rig.deep = ["@/var/lib/ollama2/Q"]
    _install(rig)
    assert rig.find("btrfs", "subvolume", "create") is not None


def test_mount_failure_after_create_raises_and_writes_no_fstab(rig):
    _ollama_dir(rig)
    rig.fail_mount_new = True
    with pytest.raises(CommandExecutionError):
        _install(rig)
    assert "var/lib/ollama" not in rig.fstab()
    assert rig.find("umount", str(rig.top)) is not None


def test_fstab_is_replaced_atomically_never_left_half_written(rig, monkeypatch):
    _ollama_dir(rig)
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append((a, b)), real(a, b))[1])
    _install(rig)
    assert seen and seen[0][0].endswith("fstab.dasik-tmp") and seen[0][1].endswith("etc/fstab")
    assert not (rig.root / "etc" / "fstab.dasik-tmp").exists()
