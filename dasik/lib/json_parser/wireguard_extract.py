"""Move captured tunnel bodies out of the JSON and into files beside it.

The mirror of :mod:`wireguard_source`, for the reason
:func:`~dasik.lib.json_parser.etc_tree.extract_to_etc_tree` exists: a capture
must not undo the split from the other side. A tunnel inline in JSON is an
escaped one-liner holding a private key — unreviewable in a diff, and a JSON
string cannot be kept at 0600 the way the file it came from was.

The writes ride along with the config rewrite (``write_back``'s
``extra_writes``), so the JSON and the files can never disagree.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, NamedTuple, Set

WIREGUARD = "wireguard"
_CAPTURE_DIR = "wg"
_MODE = 0o600


class Extraction(NamedTuple):
    """What the caller must write, delete and chmod alongside the config."""

    config: Dict[str, Any]
    writes: Dict[Path, str]
    deletions: Set[Path]
    modes: Dict[Path, int]


def _default_source(tunnel: Dict[str, Any]) -> str:
    suffix = (".nmconnection" if tunnel.get("backend") == "networkmanager"
              else ".conf")
    return f"{_CAPTURE_DIR}/{tunnel.get('name')}{suffix}"


def extract_to_wireguard_dir(config: Dict[str, Any],
                             base_dir: "str | Path") -> Extraction:
    """Return *config* without tunnel bodies, plus the files to write."""
    tunnels = config.get(WIREGUARD)
    if not tunnels or not isinstance(tunnels, list):
        return Extraction(config, {}, set(), {})

    root = Path(base_dir)
    writes: Dict[Path, str] = {}
    modes: Dict[Path, int] = {}
    out = copy.deepcopy(config)
    for tunnel in out[WIREGUARD]:
        if not isinstance(tunnel, dict):
            continue
        content = tunnel.pop("content", None)
        if content is None:
            continue
        source = tunnel.get("source") or _default_source(tunnel)
        tunnel["source"] = source
        path = root / source
        writes[path] = content
        modes[path] = _MODE

    # Only sweep the directory dasik itself writes into. A tunnel the config
    # keeps elsewhere is the user's own filing, and deleting from an arbitrary
    # path named by `source` would be a capture removing its neighbours.
    deletions: Set[Path] = set()
    capture_root = root / _CAPTURE_DIR
    if capture_root.is_dir():
        deletions = {p for p in capture_root.iterdir() if p.is_file()} - set(writes)
    return Extraction(out, writes, deletions, modes)
