"""In-process integration tests for every CLI verb.

Drives dasik.__main__.main([...]) against a tmp_path fake root with the system
commands mocked, exercising the REAL action registry (unlike tests/test_cli_*,
which mock the registry out). Deterministic; never touches the host.

Configs are kept package-focused: optional domains whose config slice is absent
are skipped by the reconciler, so apply only touches `packages` (mocked) and the
always-on `__root__` actions (which write into the fake `/etc`).
"""
import json
from unittest.mock import MagicMock, patch

from dasik.__main__ import main


def _fake_exec(table=None):
    """Command.execute / subprocess.run replacement.

    `table` maps (cmd, args[0]) -> stdout bytes; default empty stdout, rc 0.
    """
    table = table or {}

    def run(cmd, args=None, *a, **k):
        if isinstance(cmd, (list, tuple)):          # subprocess.run(["arch-chroot", ...])
            key = (cmd[0], cmd[1] if len(cmd) > 1 else "")
        else:                                       # Command.execute("pacman", ["-Qqe"])
            key = (cmd, (args or [""])[0] if args else "")
        out = table.get(key, b"")
        return MagicMock(stdout=out, stderr=b"", returncode=0)

    return run


def _invoke(argv, table=None):
    with patch("dasik.lib.command_worker.command_worker.Command.execute",
               side_effect=_fake_exec(table)), \
         patch("subprocess.run", side_effect=_fake_exec(table)):
        return main(argv)


def _write(tmp_path, cfg):
    (tmp_path / "etc").mkdir(exist_ok=True)   # fake root /etc for file-writing actions
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def test_plan_runs_readonly_and_exits_zero(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["git"]})
    code = _invoke(["plan", str(p), "--target", str(tmp_path)],
                   table={("pacman", "-Qqe"): b""})   # nothing installed
    assert code == 0
    assert "git" in capsys.readouterr().out                 # plan shows the install
    assert not (tmp_path / "var/lib/dasik/state.json").exists()  # plan writes nothing


def test_apply_writes_state_and_generation(tmp_path):
    p = _write(tmp_path, {"packages": ["git"]})
    code = _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
                   table={("pacman", "-Qqe"): b""})
    assert code == 0
    assert (tmp_path / "var/lib/dasik/state.json").exists()
    assert (tmp_path / "var/lib/dasik/generations/1").is_dir()


def test_apply_is_idempotent_second_run_no_generation_2(tmp_path):
    p = _write(tmp_path, {"packages": ["git"]})
    # mark the fake target as already-bootstrapped so the always-on __root__
    # domains are converged and don't perpetually plan:
    #   - bootloader domain (default grub) -> /boot/grub/grub.cfg
    #   - base-install domain (v3 pacstrap) -> /usr/bin/pacman
    (tmp_path / "boot" / "grub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text("")
    (tmp_path / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr" / "bin" / "pacman").write_text("")
    table = {("pacman", "-Qqe"): b"git\n",      # git already installed (explicit)
             ("pacman", "-Qq"): b"git\n"}       # ...and present (any-reason check)
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"], table=table)
    code = _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"], table=table)
    assert code == 0
    assert not (tmp_path / "var/lib/dasik/generations/2").exists()


def test_sync_captures_reality_into_config(tmp_path):
    p = _write(tmp_path, {"packages": ["git"]})
    # reality has more than declared: htop present, undeclared
    code = _invoke(["sync", str(p), "--target", str(tmp_path)],
                   table={("pacman", "-Qqe"): b"git\nhtop\n"})
    assert code == 0
    new = json.loads(p.read_text())
    assert "git" in new["packages"] and "htop" in new["packages"]
    assert (tmp_path / "config.json.bak").exists()


def test_generations_lists_after_apply(tmp_path, capsys):
    p = _write(tmp_path, {"packages": ["git"]})
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b""})
    capsys.readouterr()
    code = _invoke(["generations", "--target", str(tmp_path)])
    assert code == 0
    assert "Generation 1" in capsys.readouterr().out


def test_rollback_restores_previous_generation(tmp_path):
    p = _write(tmp_path, {"packages": ["git"]})
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b""})            # gen 1 (git)
    _write(tmp_path, {"packages": ["git", "htop"]})
    _invoke(["apply", str(p), "--target", str(tmp_path), "--yes"],
            table={("pacman", "-Qqe"): b"git\n"})       # gen 2 (git, htop)
    code = _invoke(["rollback", "1", "--target", str(tmp_path), "--yes"],
                   table={("pacman", "-Qqe"): b"git\nhtop\n"})
    assert code == 0
