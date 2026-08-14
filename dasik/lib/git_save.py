"""Commit a capture into the repository the config lives in.

`sync` needs root — it reads ``/etc/shadow``, runs ``cryptsetup luksDump``,
queries firewalld's permanent zones. The commit that follows does not: it
belongs to whoever ran ``sudo``, whose Git identity and credentials are the ones
that work. So everything here runs as the **invoking** user, and the files the
capture wrote are handed back to them (``sudo dasik sync`` leaves a config
``root:root`` inside a user's repository today).

Two rules the rest of the module exists to enforce:

- **A gitignored file is never staged.** The writeback legitimately rewrites
  ``secrets/hashed-password``; staging it with ``git add -f`` would commit a
  password hash on the strength of a convenience flag. Such files are reported
  instead, which also surfaces a file that is ignored *by accident*.
- **Nothing outside the work tree is touched.** A `files` body extracted
  somewhere else is a bug, not something to commit quietly.

dasik does not learn Git beyond this: no branches, no rebasing, no conflict
resolution. Add, commit, push.
"""
from __future__ import annotations

import os
import pwd
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from .command_worker.command_worker import Command


class GitSaveError(Exception):
    """The repository, the user, or a path made the commit impossible."""


@dataclass
class SaveResult:
    """What `save` did, in the order it did it."""
    committed: bool = False
    pushed: bool = False
    skipped: List[Path] = field(default_factory=list)
    push_error: Optional[str] = None


def _run(repo: Path, args: Sequence[str], user: Optional[str] = None):
    """Run one git command in *repo*, as *user* when dropping privileges.

    The repository path and every argument are positional — they never reach a
    shell — which is the same rule the AUR builder and the config-saver restore
    follow.
    """
    git_args = ["-C", str(repo), *args]
    if user is None:
        return Command.execute("git", git_args)
    # `su - <user> -c '<script>' -- sh <args…>`: the script is fixed and the
    # values arrive as "$1", "$2"… so a path can never be spliced into it.
    script = 'git "$@"'
    return Command.execute("su", ["-", user, "-c", script, "--", "git", *git_args])


def invoking_user() -> Optional[str]:
    """The human behind the `sudo`, or None when dasik is not running as root.

    Running as plain root with no ``SUDO_USER`` is an error the caller reports:
    a commit authored by root, in a user's repository, with root's (absent)
    credentials, is not what anybody asked for.
    """
    if os.geteuid() != 0:
        return None
    user = os.environ.get("SUDO_USER")
    if not user:
        raise GitSaveError(
            "running as root with no SUDO_USER: dasik will not author a commit "
            "as root in your repository. Run it with sudo from your own account.")
    try:
        pwd.getpwnam(user)
    except KeyError:
        raise GitSaveError(f"SUDO_USER={user!r} is not a user on this system") from None
    return user


def repo_root(path: "str | Path") -> Optional[Path]:
    """The Git work tree containing *path*, or None."""
    start = Path(path)
    directory = start.parent if start.is_file() or start.suffix else start
    result = Command.execute(
        "git", ["-C", str(directory), "rev-parse", "--show-toplevel"])
    if getattr(result, "returncode", 1) != 0:
        return None
    out = result.stdout
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    root = out.strip()
    return Path(root) if root else None


def ignored_paths(repo: Path, paths: Iterable[Path]) -> Set[Path]:
    """Which of *paths* Git is told to ignore."""
    paths = [Path(p) for p in paths]
    if not paths:
        return set()
    # check-ignore exits 1 when nothing matches, which is not an error here.
    result = Command.execute(
        "git", ["-C", str(repo), "check-ignore", *[str(p) for p in paths]])
    out = result.stdout
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    ignored = {Path(line.strip()) for line in out.splitlines() if line.strip()}
    return {p for p in paths if p in ignored or p.resolve() in ignored}


def chown_to(user: str, paths: Iterable[Path]) -> None:
    """Give *paths* back to *user* — the capture ran as root, the repo is theirs."""
    entry = pwd.getpwnam(user)
    for path in paths:
        try:
            os.chown(path, entry.pw_uid, entry.pw_gid)
        except OSError:      # nosec B110 - a file that vanished is not fatal here
            pass


def commit_paths(repo: Path, paths: Sequence[Path], message: str,
                 push: bool = True, user: Optional[str] = None) -> SaveResult:
    """Stage *paths* (minus the ignored ones), commit, and optionally push."""
    repo = Path(repo)
    result = SaveResult()

    resolved_repo = repo.resolve()
    for path in paths:
        if resolved_repo not in Path(path).resolve().parents:
            raise GitSaveError(
                f"{path} is outside the repository {repo} — refusing to commit it")

    ignored = ignored_paths(repo, paths)
    result.skipped = sorted(ignored)
    staged = [p for p in paths if p not in ignored]
    if not staged:
        return result

    _run(repo, ["add", "--", *[str(p) for p in staged]], user)

    # Nothing staged means the capture wrote a file whose content did not
    # change — an empty commit says nothing and clutters the history.
    diff = _run(repo, ["diff", "--cached", "--quiet"], user)
    if getattr(diff, "returncode", 0) == 0:
        return result

    commit = _run(repo, ["commit", "-m", message], user)
    if getattr(commit, "returncode", 1) != 0:
        raise GitSaveError(f"git commit failed: {_text(commit)}")
    result.committed = True

    if not push:
        return result

    remotes = _text(_run(repo, ["remote"], user)).split()
    if "origin" not in remotes:
        result.push_error = "no 'origin' remote: committed locally, not pushed"
        return result

    pushed = _run(repo, ["push", "origin", "HEAD"], user)
    if getattr(pushed, "returncode", 1) != 0:
        result.push_error = _text(pushed).strip() or "git push failed"
        return result
    result.pushed = True
    return result


def _text(result) -> str:
    out = getattr(result, "stdout", "") or ""
    err = getattr(result, "stderr", "") or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", errors="replace")
    return (out + err).strip()
