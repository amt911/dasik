"""A directory that mirrors ``/etc``, expanded into ``files`` entries.

``$include_text`` moves one file body out of the JSON. This moves all of them:

    config/
    ├── main.json          "etc_tree": "etc"
    └── etc/
        ├── pam.d/sudo                    -> /etc/pam.d/sudo
        └── profile.d/dasik.sh            -> /etc/profile.d/dasik.sh

The tree reads like the ``/etc`` it produces, which is the point — nobody has to
learn a schema to review it. It also covers what the snippet sections cannot:
``/etc/pam.d`` has no section of its own.

Expansion happens in the **loader**, because only the loader knows where the
config file is and therefore where the tree is. After it, every action, the
preflight and ``plan`` see ordinary ``files`` entries.

Git preserves one permission bit, so an executable file becomes ``0755`` and
anything else needs ``etc_tree_modes``. That is deliberate for the case that
matters: NetworkManager and ``wg-quick`` **ignore** a world-readable keyfile in
silence, so the mode that protects a secret is declared where a reader sees it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Set

ETC_TREE = "etc_tree"
ETC_TREE_MODES = "etc_tree_modes"
_ETC = "/etc"


class ConfigTreeError(Exception):
    """The tree could not be read (missing, a symlink, not text…)."""


def _tree_root(raw: Any, base_dir: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigTreeError(f"{ETC_TREE} must be a non-empty string, got {raw!r}")
    if os.path.isabs(raw):
        raise ConfigTreeError(
            f"{ETC_TREE} {raw!r} must be relative to the config that names it")
    if ".." in Path(raw).parts:
        raise ConfigTreeError(
            f"{ETC_TREE} {raw!r} must not contain '..' — a config may only pull "
            "in files at or below its own directory")
    root = base_dir / raw
    if not root.is_dir():
        raise ConfigTreeError(f"{ETC_TREE} directory not found: {raw} ({root})")
    return root


def _read(path: Path, relative: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ConfigTreeError(
            f"{ETC_TREE} file {relative} is not UTF-8 text; a managed file's "
            "content is a string, so a binary belongs in a package") from None
    except OSError as exc:
        raise ConfigTreeError(f"cannot read {ETC_TREE} file {relative}: {exc}") from None


def _walk(root: Path) -> List[str]:
    """Every regular file under *root*, as a sorted relative path."""
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames) + sorted(filenames):
            full = Path(dirpath) / name
            relative = str(full.relative_to(root))
            # A symlink would publish whatever it points at — including a file
            # outside the config directory, which every other directive refuses.
            if full.is_symlink():
                raise ConfigTreeError(
                    f"{ETC_TREE} entry {relative} is a symlink; the tree holds "
                    "real files only")
            if name in filenames:
                out.append(relative)
        dirnames.sort()
    return sorted(out)


def expand_etc_tree(config: Dict[str, Any], base_dir: "str | Path") -> Dict[str, Any]:
    """Return *config* with the tree's files added to ``files``.

    An explicit ``files`` entry wins over a tree file for the same path: the
    config says it in the file you are reading, so that is what it means.
    """
    if ETC_TREE not in config:
        return config

    root = _tree_root(config[ETC_TREE], Path(base_dir))
    modes: Dict[str, Any] = config.get(ETC_TREE_MODES) or {}

    declared = {entry.get("path") for entry in config.get("files") or []
                if isinstance(entry, dict)}
    relatives = _walk(root)

    unknown = sorted(set(modes) - set(relatives))
    if unknown:
        raise ConfigTreeError(
            f"{ETC_TREE_MODES} names {', '.join(unknown)}, which the tree does "
            "not hold — a mode nobody applies is a typo, and this one may be "
            "what keeps a secret unreadable")

    grown: List[Dict[str, Any]] = []
    for relative in relatives:
        path = f"{_ETC}/{Path(relative).as_posix()}"
        if path in declared:
            continue
        full = root / relative
        entry: Dict[str, Any] = {"path": path, "content": _read(full, relative)}
        mode = modes.get(relative)
        if mode is None and os.stat(full).st_mode & 0o111:
            mode = "0755"
        if mode is not None:
            entry["mode"] = mode
        grown.append(entry)

    if not grown:
        return config
    out = dict(config)
    out["files"] = list(config.get("files") or []) + grown
    return out


class Extraction(NamedTuple):
    """What `sync` should do with a capture, given a declared tree.

    ``config`` no longer carries the extracted bodies (the tree re-grows them on
    the next load); ``writes`` and ``deletions`` are handed to the writeback so
    the whole capture is still all-or-nothing, and ``modes`` are applied after.
    """
    config: Dict[str, Any]
    writes: Dict[Path, str]
    deletions: Set[Path]
    modes: Dict[Path, int]


def extract_to_etc_tree(config: Dict[str, Any],
                        base_dir: "str | Path") -> Extraction:
    """Move captured ``/etc`` file bodies out of *config* and into the tree.

    Without this, a capture undoes the split from the other side: every PAM
    snippet comes back as an escaped one-line string in the JSON.

    A mode Git can carry (executable) becomes a `chmod`; anything else has to be
    declared in ``etc_tree_modes``, which is rebuilt from the capture — a stale
    entry naming a file the machine no longer has would refuse to load.
    """
    if not config.get(ETC_TREE):
        return Extraction(config, {}, set(), {})

    root = Path(base_dir) / config[ETC_TREE]
    writes: Dict[Path, str] = {}
    modes: Dict[Path, int] = {}
    declared_modes: Dict[str, str] = {}
    kept: List[Any] = []

    for entry in config.get("files") or []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path.startswith(_ETC + "/"):
            kept.append(entry)
            continue
        relative = path[len(_ETC) + 1:]
        full = root / relative
        writes[full] = entry.get("content", "")
        mode = entry.get("mode")
        if mode is not None:
            modes[full] = int(mode, 8)
            # 0755 is the one mode Git carries by itself; anything else has to
            # be said out loud or it silently degrades to the umask.
            if int(mode, 8) != 0o755:
                declared_modes[relative] = mode

    # The tree is a declaration, not a pile: a file the capture did not report
    # is no longer on the machine, so it does not belong in the tree either.
    deletions: Set[Path] = set()
    if root.is_dir():
        deletions = {root / relative for relative in _walk(root)} - set(writes)

    out = dict(config)
    out["files"] = kept
    if declared_modes:
        out[ETC_TREE_MODES] = declared_modes
    else:
        out.pop(ETC_TREE_MODES, None)
    return Extraction(out, writes, deletions, modes)
