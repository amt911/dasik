"""A directory that mirrors users' homes, expanded into ``home_files``.

The `/etc` half of this is :mod:`etc_tree`; this is the same idea one level
deeper, because a home file is addressed as **(user, path relative to the
home)** rather than by absolute path — the machine decides where a home lives,
and dasik reads its ``/etc/passwd`` to find out.

    home/
    └── andres/
        └── .config/config-saver/configs.d/zsh.yaml   -> ~andres/.config/…/zsh.yaml

The reason it exists: a captured config-saver document is a YAML file **with
comments**, and inline in JSON it becomes one escaped line nobody can read or
review. `sync` extracts into this tree, so what lands in Git is the file itself.

The first level is a user name, so a loose file directly under the tree root is
an error rather than a guess.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Set

from .etc_tree import ConfigTreeError, _read, _tree_root, _walk

HOME_TREE = "home_tree"
HOME_TREE_MODES = "home_tree_modes"


def _split(relative: str) -> "tuple[str, str]":
    """``andres/.zshrc`` -> ``("andres", ".zshrc")``."""
    user, _, rest = relative.partition("/")
    if not rest:
        raise ConfigTreeError(
            f"{HOME_TREE} entry {relative!r} is directly in the tree root, but "
            "the first level is a USER name — put it under <user>/")
    return user, rest


def expand_home_tree(config: Dict[str, Any], base_dir: "str | Path") -> Dict[str, Any]:
    """Return *config* with the tree's files added to ``home_files``."""
    if HOME_TREE not in config:
        return config

    root = _tree_root(config[HOME_TREE], Path(base_dir))
    modes: Dict[str, Any] = config.get(HOME_TREE_MODES) or {}

    declared = {(e.get("user"), e.get("path")) for e in config.get("home_files") or []
                if isinstance(e, dict)}
    relatives = _walk(root)

    unknown = sorted(set(modes) - set(relatives))
    if unknown:
        raise ConfigTreeError(
            f"{HOME_TREE_MODES} names {', '.join(unknown)}, which the tree does "
            "not hold — a mode nobody applies is a typo, and this one may be "
            "what keeps a secret unreadable")

    grown: List[Dict[str, Any]] = []
    for relative in relatives:
        user, path = _split(Path(relative).as_posix())
        if (user, path) in declared:
            continue
        full = root / relative
        entry: Dict[str, Any] = {"user": user, "path": path,
                                 "content": _read(full, relative)}
        mode = modes.get(relative)
        if mode is None and os.stat(full).st_mode & 0o111:
            mode = "0755"
        if mode is not None:
            entry["mode"] = mode
        grown.append(entry)

    if not grown:
        return config
    out = dict(config)
    out["home_files"] = list(config.get("home_files") or []) + grown
    return out


class Extraction(NamedTuple):
    """What `sync` should do with a capture, given a declared tree."""
    config: Dict[str, Any]
    writes: Dict[Path, str]
    deletions: Set[Path]
    modes: Dict[Path, int]


def extract_to_home_tree(config: Dict[str, Any],
                         base_dir: "str | Path") -> Extraction:
    """Move captured `home_files` bodies out of *config* and into the tree."""
    if not config.get(HOME_TREE):
        return Extraction(config, {}, set(), {})

    root = Path(base_dir) / config[HOME_TREE]
    writes: Dict[Path, str] = {}
    modes: Dict[Path, int] = {}
    declared_modes: Dict[str, str] = {}
    kept: List[Any] = []

    for entry in config.get("home_files") or []:
        user = entry.get("user") if isinstance(entry, dict) else None
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(user, str) or not isinstance(path, str):
            kept.append(entry)
            continue
        relative = f"{user}/{path}"
        full = root / relative
        writes[full] = entry.get("content", "")
        mode = entry.get("mode")
        if mode is not None:
            modes[full] = int(mode, 8)
            # 0755 survives in Git by itself; anything else has to be declared.
            if int(mode, 8) != 0o755:
                declared_modes[relative] = mode

    deletions: Set[Path] = set()
    if root.is_dir():
        deletions = {root / relative for relative in _walk(root)} - set(writes)

    out = dict(config)
    out["home_files"] = kept
    if declared_modes:
        out[HOME_TREE_MODES] = declared_modes
    else:
        out.pop(HOME_TREE_MODES, None)
    return Extraction(out, writes, deletions, modes)
