"""Read each declared tunnel's file, so the rest of dasik sees its content.

Same reason as :mod:`etc_tree`: only the loader knows where the config file is,
and therefore where a path relative to it points. After this, the expand
toggle, the preflight and every action see an ordinary ``content`` string.

The refusals are the interesting part. A tunnel file holds the interface's
private key, so a symlink is **not** followed — reading through it would pull a
file the config never named — and a missing one is reported with the tunnel's
name, because ``dasik check`` is where that mistake should surface, not the
middle of an install.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

from .etc_tree import ConfigTreeError

WIREGUARD = "wireguard"


def expand_wireguard_sources(config: Dict[str, Any],
                             base_dir: "str | Path") -> Dict[str, Any]:
    """Return *config* with every tunnel's ``content`` read from its ``source``."""
    tunnels = config.get(WIREGUARD)
    if not tunnels or not isinstance(tunnels, list):
        return config

    out = copy.deepcopy(config)
    for tunnel in out[WIREGUARD]:
        if not isinstance(tunnel, dict) or tunnel.get("content") is not None:
            continue
        name = tunnel.get("name", "?")
        source = tunnel.get("source")
        if not isinstance(source, str) or not source:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r} declares no source file")
        path = Path(base_dir) / source
        if path.is_symlink():
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: {source} is a symlink. A tunnel "
                "file holds a private key and is read verbatim, so it has to "
                "be the file itself")
        try:
            tunnel["content"] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: source file not found: "
                f"{source} ({path})")
        except UnicodeDecodeError:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: {source} is not UTF-8 text; a "
                "tunnel file is a wg-quick conf or a NetworkManager keyfile")
        except OSError as e:
            raise ConfigTreeError(
                f"wireguard tunnel {name!r}: cannot read {source}: {e}")
    return out
