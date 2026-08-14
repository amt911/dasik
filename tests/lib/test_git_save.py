"""`dasik save`: capture the machine, then commit it — as the right user.

The cycle a config in Git needs was five manual steps. This is the piece that
makes it one, and the two things it must never get wrong:

- **the invoking user owns the result.** `sync` needs root (it reads
  /etc/shadow, runs cryptsetup); the commit belongs to whoever ran `sudo`,
  whose credentials and `user.email` are the ones that work. `sudo dasik sync`
  currently leaves the config `root:root` inside a user's repository.
- **a gitignored file is never staged.** The writeback legitimately rewrites
  `secrets/hashed-password`; `git add -f` would commit a password hash on the
  strength of a convenience flag.

Real Git repositories in tmp_path rather than mocks: what is being asserted is
what Git does, and stubbing it would assert what I think Git does.
"""
import subprocess
from pathlib import Path

import pytest

from dasik.lib.git_save import (
    GitSaveError,
    commit_paths,
    ignored_paths,
    repo_root,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A real repository with one commit, an ignore rule, and a remote."""
    root = tmp_path / "cfg"
    (root / "secrets").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / ".gitignore").write_text("secrets/*\n")
    (root / "main.json").write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed"], check=True)
    return root


# --- finding the repository -------------------------------------------------- #

def test_repo_root_finds_the_work_tree(repo):
    assert repo_root(repo / "main.json") == repo


def test_repo_root_is_none_outside_a_repository(tmp_path):
    (tmp_path / "loose.json").write_text("{}\n")
    assert repo_root(tmp_path / "loose.json") is None


# --- what may be staged ------------------------------------------------------ #

def test_an_ignored_file_is_reported_not_staged(repo):
    secret = repo / "secrets" / "hashed-password"
    secret.write_text("$y$new\n")

    assert ignored_paths(repo, [repo / "main.json", secret]) == {secret}


def test_nothing_is_ignored_when_no_rule_matches(repo):
    assert ignored_paths(repo, [repo / "main.json"]) == set()


# --- committing --------------------------------------------------------------- #

def test_a_changed_file_is_committed(repo):
    (repo / "main.json").write_text('{"hostname": "torre"}\n')

    result = commit_paths(repo, [repo / "main.json"], "torre: sync", push=False)

    assert result.committed
    assert "torre: sync" in _git(repo, "log", "-1", "--pretty=%s")
    assert _git(repo, "status", "--porcelain") == ""


def test_an_ignored_file_stays_out_of_the_commit(repo):
    secret = repo / "secrets" / "hashed-password"
    secret.write_text("$y$new\n")
    (repo / "main.json").write_text('{"hostname": "torre"}\n')

    result = commit_paths(repo, [repo / "main.json", secret], "torre: sync",
                          push=False)

    assert result.skipped == [secret]
    files = _git(repo, "show", "--name-only", "--pretty=", "HEAD").split()
    assert files == ["main.json"]


def test_no_commit_when_nothing_changed(repo):
    result = commit_paths(repo, [repo / "main.json"], "torre: sync", push=False)

    assert not result.committed
    assert _git(repo, "log", "--oneline").count("\n") == 1


def test_push_is_skipped_without_a_remote(repo):
    (repo / "main.json").write_text('{"hostname": "torre"}\n')

    result = commit_paths(repo, [repo / "main.json"], "torre: sync", push=True)

    assert result.committed
    assert not result.pushed
    assert "origin" in (result.push_error or "")


def test_push_reaches_a_real_remote(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
                   check=True)
    (repo / "main.json").write_text('{"hostname": "torre"}\n')

    result = commit_paths(repo, [repo / "main.json"], "torre: sync", push=True)

    assert result.pushed
    # The branch by name: a bare repo's HEAD follows init.defaultBranch, which
    # need not be the branch that was just pushed.
    assert "torre: sync" in subprocess.run(
        ["git", "-C", str(remote), "log", "-1", "--pretty=%s", "main"],
        capture_output=True, text=True, check=True).stdout


def test_a_path_outside_the_repository_is_refused(repo, tmp_path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}\n")

    with pytest.raises(GitSaveError, match="outside"):
        commit_paths(repo, [outside], "torre: sync", push=False)
