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


# PackagesAction now resolves each declared name's origin against the target's
# pacman sync DBs (pacman -Slq). These flow tests keep `packages` mocked, so the
# fake repo DB just needs to list the names they install so resolution classifies
# them as repo (not unknown → which would abort apply before touching state).
_REPO_DB = b"git\nhtop\nbluez\nbluez-utils\n"


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
        # A real genfstab always emits fstab content; base install now aborts on
        # an empty one (a mountless /etc/fstab would be non-bootable), so the fake
        # must mimic that rather than the impossible empty default.
        if not out and key[0] == "genfstab":
            out = b"UUID=test-root / ext4 rw,relatime 0 1\n"
        # Default the package-resolver's repo-DB read so declared packages resolve
        # as repo (tests may override via `table`).
        if not out and key == ("pacman", "-Slq"):
            out = _REPO_DB
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


def test_apply_expands_bluetooth_toggle_into_packages(tmp_path):
    # bluetooth toggle must expand so the packages domain installs bluez
    p = _write(tmp_path, {"packages": ["git"], "bluetooth": {"enable": True}})
    # Accumulate across every `pacman -S`: the always-on bootloader domain also
    # runs one (`-S grub efibootmgr`), so capturing only the last call would
    # clobber the packages-domain install we're asserting on.
    captured = {"installed": []}

    def run(cmd, args=None, *a, **k):
        if cmd == "pacman" and args and args[0] == "-Qqe":
            return MagicMock(stdout=b"", stderr=b"", returncode=0)
        if cmd == "pacman" and args and args[0] == "-Slq":   # resolver repo DB
            return MagicMock(stdout=_REPO_DB, stderr=b"", returncode=0)
        if cmd == "genfstab":                       # base install aborts on empty fstab
            return MagicMock(stdout=b"UUID=t / ext4 rw 0 1\n", stderr=b"", returncode=0)
        if cmd == "pacman" and args and "-S" in args:
            captured["installed"].extend(args)
        return MagicMock(stdout=b"", stderr=b"", returncode=0)

    with patch("dasik.lib.command_worker.command_worker.Command.execute", side_effect=run), \
         patch("subprocess.run", side_effect=_fake_exec({("pacman", "-Qqe"): b""})):
        code = main(["apply", str(p), "--target", str(tmp_path), "--yes"])
    assert code == 0
    assert "bluez" in captured["installed"]


# --- partial generations surfaced by the CLI (F-01) ------------------------ #

def test_generations_marks_partial_entries(tmp_path, capsys):
    from dasik.lib.state.generation_store import GenerationStore
    from dasik.lib.target.target import Target
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({}, {"generation": 1})
    store.new({}, {"generation": 2, "partial": True})

    rc = main(["generations", "--target", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Generation 1" in out and "partial" not in out.split("Generation 2")[0]
    assert "Generation 2 (current, partial — apply failed part-way)" in out


def test_rollback_refuses_a_partial_target(tmp_path, capsys):
    from dasik.lib.state.generation_store import GenerationStore
    from dasik.lib.target.target import Target
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({}, {"generation": 1, "partial": True})

    rc = main(["rollback", "1", "--target", str(tmp_path), "--yes"])
    assert rc == 1
    assert "partial" in capsys.readouterr().err


def test_rollback_default_skips_partial_generations(tmp_path, capsys):
    """`rollback` with no number must land on the last COMPLETE generation."""
    from dasik.lib.state.generation_store import GenerationStore
    from dasik.lib.target.target import Target
    store = GenerationStore(Target(root=str(tmp_path)))
    store.new({"packages": []}, {"generation": 1})              # complete
    store.new({"packages": []}, {"generation": 2, "partial": True})
    store.new({"packages": []}, {"generation": 3})              # current

    from dasik import __main__ as cli_mod
    assert cli_mod._previous_generation(store) == 1


def test_apply_failure_reports_the_recorded_partial_generation(tmp_path, capsys):
    from unittest.mock import MagicMock, patch as _patch
    from dasik.lib.exceptions.exceptions import CommandExecutionError

    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"packages": ["git"]}))

    with _patch("dasik.__main__.Reconciler") as Recon, \
         _patch("dasik.__main__.setup_actions", lambda: None), \
         _patch("dasik.__main__.get_default_registry") as Reg, \
         _patch("dasik.__main__.StateStore") as Store, \
         _patch("dasik.__main__.GenerationStore"):
        Reg.return_value.get_all_actions.return_value = []
        plan = MagicMock()
        plan.is_empty.return_value = False
        Recon.return_value.build_plan.return_value = (plan, [])
        Recon.return_value.apply.side_effect = CommandExecutionError("pacman failed")
        Store.return_value.load.return_value.to_dict.return_value = {"managed": {}}
        rc = main(["apply", str(cfg), "--yes", "--target", str(tmp_path)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "pacman failed" in err
    assert "partial" in err.lower()
