"""Journey: `config_saver.restic_stores` through every verb, in-process.

`main([...])` against a fake root, system commands mocked; the mocked
`config-saver --restic-init` creates the store the way the real one does, so
the next `plan` reads real files. The real thing runs in the guest
(`scripts/vmtest/guest-config-saver-restic-upgrade.sh` for a machine on
config-saver 3.4.0, `-fresh.sh` for a fresh install).

Round trips: plan → apply → plan silent; sync → check → plan silent; the
block removed plans no removal (the store holds the backups); generations
lists the applies. And the edge the user asked for: no restic on the target is
a warning, the apply succeeds, and plan keeps naming the store.
"""
import json
from unittest.mock import MagicMock, patch

from dasik.__main__ import main
from tests.support.pacman import pacman_double

_PUB = b"age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq"
_PKGS = ["config-saver", "restic", "age", "git"]


def _root(tmp_path, restic=True):
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc/passwd").write_text(
        "root:x:0:0::/root:/bin/bash\nandres:x:1000:1000::/home/andres:/bin/bash\n")
    (tmp_path / "boot/grub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "boot/grub/grub.cfg").write_text("")
    (tmp_path / "usr/bin").mkdir(parents=True, exist_ok=True)
    for name in ("pacman", "config-saver", "age") + (("restic",) if restic else ()):
        (tmp_path / "usr/bin" / name).write_text("")
    key = tmp_path / "home/andres/.config/age/key.txt"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("AGE-SECRET-KEY-1\n")
    return tmp_path


def _exec(root, calls):
    strict = pacman_double(installed=_PKGS, repo=_PKGS)
    saves = root / "home/andres/.config/config-saver"

    def run(cmd, args=None, *a, **k):
        if isinstance(cmd, (list, tuple)):
            cmd, args = cmd[0], list(cmd[1:])
        args = list(args or [])
        calls.append([cmd, *args])
        if cmd == "pacman":
            return strict(cmd, args)
        if cmd == "config-saver" and args == ["--version"]:
            return MagicMock(returncode=0, stdout=b"config-saver 3.5.0\n", stderr=b"")
        if cmd == "su" and "age-keygen" in (args[3] if len(args) > 3 else ""):
            return MagicMock(returncode=0, stdout=b"\x1edasik\x1e" + _PUB + b"\n", stderr=b"")
        if cmd == "su" and "--restic-init" in args:
            (saves / "restic").mkdir(parents=True)
            (saves / "restic-password.age").write_text("x")
            (saves / "restic-store.json").write_text(json.dumps(
                {"identity": "/home/andres/.config/age/key.txt"}))
            return MagicMock(returncode=0, stdout=b"", stderr=b"")
        if cmd == "genfstab":
            return MagicMock(returncode=0, stdout=b"UUID=t / ext4 rw 0 1\n", stderr=b"")
        return MagicMock(returncode=0, stdout=b"", stderr=b"")
    return run


def _invoke(root, argv, calls):
    fake = _exec(root, calls)
    with patch("dasik.lib.command_worker.command_worker.Command.execute", side_effect=fake), \
         patch("subprocess.run", side_effect=fake):
        return main(argv)


def _config(root, block):
    p = root / "config.json"
    p.write_text(json.dumps({"packages": ["git", "restic", "age"], "config_saver": block}))
    return p


def _inits(calls):
    return [c for c in calls if c[0] == "su" and "--restic-init" in c]


def test_restic_store_through_every_verb(tmp_path, capsys):
    root = _root(tmp_path)
    cfg = _config(root, {"restic_stores": [{"user": "andres"}]})
    t = ["--target", str(root)]
    calls = []

    assert _invoke(root, ["plan", str(cfg), *t], calls) == 0
    assert "config_saver_restic" in capsys.readouterr().out

    assert _invoke(root, ["apply", str(cfg), *t, "--yes"], calls) == 0
    assert len(_inits(calls)) == 1
    assert (root / "home/andres/.config/config-saver/restic").is_dir()
    capsys.readouterr()

    assert _invoke(root, ["plan", str(cfg), *t], calls) == 0
    assert "config_saver_restic" not in capsys.readouterr().out      # converged

    # sync from a config that does NOT declare it: reality brings it back.
    bare = _config(root, {})
    assert _invoke(root, ["sync", str(bare), *t], calls) == 0
    captured = json.loads(bare.read_text())
    assert captured["config_saver"]["restic_stores"] == [
        {"user": "andres", "identity": "~/.config/age/key.txt"}]
    assert _invoke(root, ["check", str(bare)], calls) == 0
    capsys.readouterr()
    assert _invoke(root, ["plan", str(bare), *t], calls) == 0
    assert "config_saver_restic" not in capsys.readouterr().out

    # The block dropped: no removal is ever planned, and nothing is deleted.
    dropped = _config(root, {})
    capsys.readouterr()
    assert _invoke(root, ["plan", str(dropped), *t], calls) == 0
    out = capsys.readouterr().out
    assert "config_saver_restic" not in out
    assert _invoke(root, ["apply", str(dropped), *t, "--yes"], calls) == 0
    assert (root / "home/andres/.config/config-saver/restic").is_dir()

    capsys.readouterr()
    assert _invoke(root, ["generations", *t], calls) == 0
    # The second apply (block dropped) changed nothing, so it wrote no
    # generation: the store's apply is generation 1 and still current.
    assert "Generation 1 (current)" in capsys.readouterr().out

    # Rolling back to the generation that declared it: the store is there, so
    # nothing is planned, and it is never initialised twice.
    assert _invoke(root, ["rollback", "1", *t, "--yes"], calls) == 0
    capsys.readouterr()
    assert _invoke(root, ["plan", str(cfg), *t], calls) == 0
    assert "config_saver_restic" not in capsys.readouterr().out
    assert len(_inits(calls)) == 1                                   # never again


def test_no_restic_on_the_target_warns_and_keeps_planning(tmp_path, capsys):
    root = _root(tmp_path, restic=False)
    cfg = _config(root, {"restic_stores": [{"user": "andres"}]})
    t = ["--target", str(root)]
    calls = []

    assert _invoke(root, ["apply", str(cfg), *t, "--yes"], calls) == 0
    out = capsys.readouterr()
    assert "restic is not installed" in out.out + out.err
    assert _inits(calls) == []
    assert not (root / "home/andres/.config/config-saver/restic").exists()

    assert _invoke(root, ["plan", str(cfg), *t], calls) == 0
    assert "config_saver_restic" in capsys.readouterr().out          # still pending
