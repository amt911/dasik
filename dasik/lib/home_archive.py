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

With config-saver 3.5.0's restic store (``--restic-init``) the backups are
snapshots inside ``~/.config/config-saver/restic`` and the tree above goes
stale; the newest of each configuration is then exported first
(``store_exports``) and published the same way.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .command_worker.command_worker import Command

ENCRYPTED_SUFFIXES = (".age", ".gpg")
DEFAULT_ROOT = ".config/config-saver/configs"
RESTIC_STORE = ".config/config-saver/restic"
# config-saver names an export after its configuration and snapshot time.
_EXPORT_NAME = re.compile(r"(.+)-\d{8}-\d{6}\.tar\.gz(?:\.age|\.gpg)?$")


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


def has_restic_store(home: "str | Path") -> bool:
    """Whether config-saver keeps this home's backups in its restic store.

    config-saver 3.5.0+ creates the store at ``--restic-init``; from then on the
    backups are snapshots inside it and nothing under DEFAULT_ROOT is the
    newest one any more.
    """
    return (Path(home) / RESTIC_STORE).is_dir()


_EXPORT_PREFIX = "dasik-home-export-"


def _text(done, stream: str) -> str:
    """stdout/stderr of a Command.execute result, which are bytes."""
    value = getattr(done, stream, b"") or b""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip()


def make_export_dir(home: "str | Path", user: Optional[str]) -> Path:
    """A private (0700) directory under ``~/.cache`` for the store's exports.

    On disk on purpose: ``/tmp`` is a tmpfs and the newest archives of a real
    home are about a gigabyte. Under sudo it is made **by the user** — root never
    creates or chowns inside their home, where a symlinked ``~/.cache`` would
    send it anywhere — and what comes back must sit in that ``~/.cache``.
    """
    cache = Path(home) / ".cache"
    if user is None:
        try:
            cache.mkdir(exist_ok=True)
            return Path(tempfile.mkdtemp(prefix=_EXPORT_PREFIX, dir=cache))
        except OSError as e:
            raise HomeArchiveError(f"could not create an export directory in {cache}: {e}") from e
    done = Command.execute("su", ["-", user, "-c",
                                  'mkdir -p "$HOME/.cache" && '
                                  f'mktemp -d "$HOME/.cache/{_EXPORT_PREFIX}XXXXXX"'])
    if getattr(done, "returncode", 1) != 0:
        detail = _text(done, "stderr")
        raise HomeArchiveError(f"could not create an export directory in {cache}: "
                               f"{detail or 'mktemp failed'}")
    lines = _text(done, "stdout").splitlines()
    made = Path(lines[-1].strip() if lines else "")
    if made.parent != cache or not made.name.startswith(_EXPORT_PREFIX):
        raise HomeArchiveError(f"unexpected export directory from mktemp: {made}")
    return made


def remove_export_dir(path: "str | Path", user: Optional[str]) -> None:
    """Remove what ``make_export_dir`` made, as the same user. Best effort."""
    if user is None:
        shutil.rmtree(path, ignore_errors=True)
        return
    Command.execute("su", ["-", user, "-c", 'rm -rf -- "$1"', "--", "rm", str(path)])


def store_exports(user: Optional[str],
                  out_dir: "str | Path") -> Tuple[Dict[str, Path], List[str]]:
    """Export the newest backup of each configuration from the restic store.

    config-saver encrypts each export per its document's ``encrypt:`` block, so
    what lands in *out_dir* is the same ``<name>-<stamp>.tar.gz.age`` a run used
    to leave on disk — and ``publish_archives`` refuses it the same way when it
    is not encrypted. It runs as *user*: the store, its password and the age key
    are theirs. A failure that still exported something is returned as a
    warning, like a configuration that produced nothing on disk is just absent;
    one that exported nothing is an error.
    """
    out_dir = Path(out_dir)
    args = ["--export-all-configs", "--output", str(out_dir)]
    if user is None:
        done = Command.execute("config-saver", args)
    else:
        done = Command.execute("su", ["-", user, "-c", 'config-saver "$@"', "--",
                                     "config-saver", *args])
    exported: Dict[str, Path] = {}
    for path in sorted(p for p in out_dir.iterdir() if p.is_file() and _is_archive(p)):
        match = _EXPORT_NAME.match(path.name)
        exported[match.group(1) if match else path.name.split(".tar.gz")[0]] = path

    returncode = getattr(done, "returncode", 1)
    detail = " ".join(_text(done, s) for s in ("stderr", "stdout")).strip()
    if returncode != 0 and not exported:
        raise HomeArchiveError(
            f"config-saver could not export from its restic store (exit "
            f"{returncode}): {detail or 'no output'}")
    if not exported:
        raise HomeArchiveError(
            "config-saver's restic store exported nothing — run `config-saver "
            "--compress` first (and check `config-saver --list`)")
    warnings = []
    if returncode != 0:
        warnings.append(f"config-saver --export-all-configs failed for some "
                        f"configuration (exit {returncode}): {detail or 'no output'}")
    return exported, warnings


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
