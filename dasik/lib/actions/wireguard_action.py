"""Action: capture declared WireGuard tunnels back from the machine.

CAPTURE-ONLY, like :class:`ReflectorAction` and :class:`CpuAction`: the expand
toggle writes the files, so ``plan()`` is empty and exists only to mark the
class as v3 so ``Reconciler.sync`` visits it.

It owns both directories a tunnel can live in, which is why
:class:`DropFilesAction` no longer discovers them. With two owners a bootstrap
sync captured the same private key twice — once as the ``wireguard`` block and
once as a ``files`` entry — because discovery reported the conf *with* mode
0600 while the toggle contributed it *without*, so the two dicts never compared
equal and ``subtract_contributions`` stripped nothing. The orphan entry then
kept writing the tunnel after the block was turned off.

`sync` writes the bodies to files next to the config; see
:mod:`dasik.lib.json_parser.wireguard_extract`.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command

_WG_DIR = "/etc/wireguard"
_NM_DIR = "/etc/NetworkManager/system-connections"
_CAPTURE_DIR = "wg"


class WireguardAction(AbstractAction):
    """Reconstruct the `wireguard` block from the tunnel files on the machine."""

    _DOMAIN = "wireguard"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "WireGuard"

    @property
    def is_optional(self) -> bool:
        return True

    def plan(self, managed: Any) -> list:
        """Nothing to converge — the toggle's `files` contribution writes it."""
        return []

    def managed_keys(self) -> dict:
        """Owns no manifest domain: it never applies anything."""
        return {}

    # --- capture -------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        target = self._target()
        return target.path(canonical) if target is not None else canonical

    def _declared_source(self, name: str) -> str:
        """Where the config already keeps this tunnel, if it declares it.

        A capture that re-homed an existing tunnel to `wg/` would leave the old
        file behind and rewrite every path in the repository for nothing.
        """
        for tunnel in self._cfg.get(self._DOMAIN) or []:
            if isinstance(tunnel, dict) and tunnel.get("name") == name:
                source = tunnel.get("source")
                if isinstance(source, str) and source:
                    return source
        return ""

    def _unit_enabled(self, unit: str) -> bool:
        try:
            res = Command.execute("systemctl", ["is-enabled", unit],
                                  target=self._target())
        except Exception:      # nosec B110 - no systemctl means "cannot tell"
            return False
        out = getattr(res, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return out.strip() in ("enabled", "enabled-runtime")

    def _read_dir(self, canonical: str, suffix: str) -> List[Tuple[str, str]]:
        """(name, body) for every real file with *suffix* in *canonical*.

        A symlink is skipped rather than read through: the capture copies the
        body verbatim into the config repository, and following a link would
        pull in a file the machine's own directory only points at.
        """
        base = self._abs(canonical)
        found: List[Tuple[str, str]] = []
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return found
        for entry in names:
            if not entry.endswith(suffix):
                continue
            path = os.path.join(base, entry)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, "r") as f:
                    found.append((entry[: -len(suffix)], f.read()))
            except (OSError, UnicodeDecodeError):
                continue
        return found

    def import_state(self, managed=None) -> dict:
        """The tunnels this machine holds, or ``{}`` when it holds none."""
        tunnels: List[Dict[str, Any]] = []
        for name, content in self._read_dir(_WG_DIR, ".conf"):
            tunnels.append({
                "name": name,
                "source": self._declared_source(name) or f"{_CAPTURE_DIR}/{name}.conf",
                "backend": "wg-quick",
                "enable": self._unit_enabled(f"wg-quick@{name}.service"),
                "content": content,
            })
        for name, content in self._read_dir(_NM_DIR, ".nmconnection"):
            if "type=wireguard" not in content.replace(" ", ""):
                continue
            tunnels.append({
                "name": name,
                "source": (self._declared_source(name)
                           or f"{_CAPTURE_DIR}/{name}.nmconnection"),
                "backend": "networkmanager",
                # NetworkManager's own autoconnect lives inside the keyfile, and
                # placing the file is the whole of what dasik does here, so
                # `enable` has nothing separate to report.
                "enable": True,
                "content": content,
            })
        return {self._DOMAIN: tunnels} if tunnels else {}


