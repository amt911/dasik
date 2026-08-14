"""`dasik save` — the five-step cycle as one command.

sync → check → commit → push. The parts worth asserting at this level are the
order (a capture the tool would refuse must never be committed) and the
refusals, not Git itself — that is `test_git_save.py`, against real
repositories.
"""
import json
import subprocess
from pathlib import Path

import pytest

import dasik.__main__ as m
from tests.lib.test_main_includes import _stub_capture


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "cfg"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "main.json").write_text(json.dumps({"hostname": "old"}) + "\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
    return root


def _log(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "--pretty=%s"],
                          capture_output=True, text=True, check=True).stdout


def test_a_capture_is_committed(repo, monkeypatch, capsys):
    _stub_capture(monkeypatch, {"hostname": "torre"})

    assert m._cmd_save(repo / "main.json", "/", message=None, push=False) == 0

    assert json.loads((repo / "main.json").read_text())["hostname"] == "torre"
    assert _log(repo).count("\n") == 2          # seed + the capture


def test_the_message_defaults_to_the_hostname_and_date(repo, monkeypatch):
    _stub_capture(monkeypatch, {"hostname": "torre"})

    m._cmd_save(repo / "main.json", "/", message=None, push=False)

    subject = _log(repo).splitlines()[0]
    assert subject.startswith("torre: sync ")


def test_an_explicit_message_wins(repo, monkeypatch):
    _stub_capture(monkeypatch, {"hostname": "torre"})

    m._cmd_save(repo / "main.json", "/", message="after installing steam",
                push=False)

    assert _log(repo).splitlines()[0] == "after installing steam"


def test_a_converged_machine_commits_nothing(repo, monkeypatch, capsys):
    _stub_capture(monkeypatch, {"hostname": "old"})

    assert m._cmd_save(repo / "main.json", "/", message=None, push=False) == 0

    assert _log(repo).count("\n") == 1
    assert "nothing to sync" in capsys.readouterr().out.lower()


def test_the_backup_is_removed_once_the_commit_holds_it(repo, monkeypatch):
    """`sync` leaves a .bak. In a repository the commit IS the backup, and an
    untracked .bak after every save leaves `git status` permanently dirty."""
    _stub_capture(monkeypatch, {"hostname": "torre"})

    m._cmd_save(repo / "main.json", "/", message=None, push=False)

    assert not (repo / "main.json.bak").exists()
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout == ""


def test_the_backup_survives_when_nothing_was_committed(repo, monkeypatch):
    """A capture that never made it into a commit is exactly when the backup
    is worth having."""
    _stub_capture(monkeypatch, {"hostname": "torre", "bootloader": "nonsense"})

    m._cmd_save(repo / "main.json", "/", message=None, push=False)

    assert (repo / "main.json.bak").exists()


def test_a_config_outside_a_repository_is_refused(tmp_path, monkeypatch, capsys):
    loose = tmp_path / "main.json"
    loose.write_text(json.dumps({"hostname": "old"}) + "\n")
    _stub_capture(monkeypatch, {"hostname": "torre"})

    assert m._cmd_save(loose, "/", message=None, push=False) == 1
    err = capsys.readouterr().err
    assert "not a git repository" in err.lower()
    # …and it refused BEFORE capturing: the file is untouched.
    assert json.loads(loose.read_text())["hostname"] == "old"


def test_a_capture_check_would_refuse_is_not_committed(repo, monkeypatch, capsys):
    """The invariant `sync` → `check` exists to catch: a capture the tool then
    rejects is a broken capture, and committing it spreads it."""
    _stub_capture(monkeypatch, {"hostname": "torre", "bootloader": "nonsense"})

    assert m._cmd_save(repo / "main.json", "/", message=None, push=False) == 1

    assert _log(repo).count("\n") == 1, "a rejected capture must not be committed"
    assert "check" in capsys.readouterr().err.lower()
