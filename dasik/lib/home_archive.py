"""Publish the `$HOME` archives config-saver produced.

config-saver writes one timestamped directory per configuration per run:

    ~/.config/config-saver/configs/<name>/<stamp>/<name>-<stamp>.tar.gz.age

so "upload my backups" is never one file. On a real machine it was seven, and
275 MB — which is also why they are **release assets** and not commits: Git
would keep every version of every one of them forever.

Two rules, both learned the hard way:

- **the newest of each configuration**, not everything (that would re-upload
  months of history) and not one arbitrary file;
- **nothing unencrypted, ever**. A release asset is a URL, `$HOME` holds
  browser profiles and SSH config, and "I had not set up encryption yet" is
  precisely the mistake worth refusing rather than reporting.

`gh` runs as the invoking user: the credentials are theirs, not root's.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

from .command_worker.command_worker import Command

ENCRYPTED_SUFFIXES = (".age", ".gpg")
DEFAULT_ROOT = ".config/config-saver/configs"


def _is_archive(path: Path) -> bool:
    """A backup, not one of the sidecars config-saver writes next to it."""
    name = path.name
    return name.endswith(".tar.gz") or any(
        name.endswith(f".tar.gz{suffix}") for suffix in ENCRYPTED_SUFFIXES)


class HomeArchiveError(Exception):
    """Nothing to publish, or something that must not be published."""


def _run(args: Sequence[str], user: Optional[str] = None):
    """One `gh` call, as *user* when dropping privileges.

    Same rule as the Git side: every value is a positional argument, and `su -`
    is a login shell so nothing may rely on the current directory.
    """
    if user is None:
        return Command.execute("gh", list(args))
    return Command.execute("su", ["-", user, "-c", 'gh "$@"', "--", "gh", *args])


def latest_archives(root: "str | Path") -> Dict[str, Path]:
    """The newest archive of each configuration under *root*.

    A configuration that produced none is simply absent — config-saver skips a
    document that needs root, and says so at the time.
    """
    root = Path(root)
    newest: Dict[str, Path] = {}
    for config_dir in sorted(p for p in root.glob("*") if p.is_dir()):
        # The run's timestamp is the DIRECTORY, and each run also drops a
        # description.txt beside the archive — sorting by file name picks that
        # one ("d" sorts after "claude-…") and publishes a note instead of a
        # backup.
        runs = sorted((p for p in config_dir.glob("*") if p.is_dir()),
                      key=lambda p: p.name)
        for run in reversed(runs):
            archives = sorted(p for p in run.glob("*")
                              if p.is_file() and _is_archive(p))
            if archives:
                newest[config_dir.name] = archives[-1]
                break
    if not newest:
        raise HomeArchiveError(
            f"no config-saver archives under {root} — run `config-saver "
            "--compress` first (and check `config-saver --show-configs` finds "
            "your documents)")
    return newest


def publish_archives(repo: str, tag: str, archives: Dict[str, Path],
                     user: Optional[str] = None) -> Dict[str, Path]:
    """Upload *archives* to release *tag* of *repo*, replacing what is there.

    One release per machine, always holding its newest archives — rather than a
    pile of releases nobody prunes.
    """
    plaintext = sorted(str(p) for p in archives.values()
                       if p.suffix not in ENCRYPTED_SUFFIXES)
    if plaintext:
        raise HomeArchiveError(
            "refusing to publish: these archives are not encrypted — "
            f"{', '.join(plaintext)}. A release asset is a URL and these hold "
            "$HOME. Declare `encrypt: {method: age, recipients: [...]}` in the "
            "config-saver document and run --compress again.")

    paths = [str(p) for p in archives.values()]
    exists = _run(["release", "view", tag, "-R", repo], user)
    if getattr(exists, "returncode", 1) != 0:
        _run(["release", "create", tag, *paths, "-R", repo, "--title", tag,
              "--notes", "Encrypted $HOME archives. The private key is not here."],
             user)
    else:
        _run(["release", "upload", tag, *paths, "--clobber", "-R", repo], user)
    return archives
