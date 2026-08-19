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
from ..exceptions.exceptions import CommandExecutionError
from .wireguard_nm import nmcli_argv, wants_nm_conversion

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

    # --- the one thing this action applies -------------------------------- #
    #
    # Every other tunnel is placed by the expand toggle's `files` contribution,
    # because the file the backend reads IS the file the config declares. A
    # wg-quick conf asked to be served by NetworkManager is the exception: the
    # keyfile does not exist yet, and `nmcli --offline connection add` builds
    # it — on the TARGET, where `networkmanager` is installed, and without a
    # daemon, which is what lets it run inside a chroot.

    def _converted(self) -> "List[Tuple[str, str, bool]]":
        """`(name, conf, autoconnect)` for each tunnel that needs converting."""
        out: List[Tuple[str, str, bool]] = []
        for tunnel in self._cfg.get(self._DOMAIN) or []:
            if not isinstance(tunnel, dict):
                continue
            content = tunnel.get("content") or ""
            if not wants_nm_conversion(content, tunnel.get("backend", "auto")):
                continue
            out.append((tunnel.get("name", ""), content,
                        bool(tunnel.get("enable", True))))
        return out

    def _keyfile_path(self, name: str) -> str:
        return f"{_NM_DIR}/{name}.nmconnection"

    def _desired_keyfile(self, name: str, conf: str, autoconnect: bool) -> str:
        """What nmcli says this tunnel is, as a keyfile.

        A pure query — `--offline` prints and changes nothing — so `plan` may
        run it. Returns "" when nmcli cannot be reached, which makes the domain
        report no change rather than plan one it could not carry out.
        """
        try:
            result = Command.execute(
                "nmcli", nmcli_argv(name, conf, autoconnect),
                target=self._target())
        except Exception:      # noqa: BLE001 - no nmcli yet = nothing to say
            return ""
        if getattr(result, "returncode", 1) != 0:
            return ""
        out = getattr(result, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        return out

    def _current_keyfile(self, name: str) -> str:
        try:
            with open(self._abs(self._keyfile_path(name)), "r") as f:
                return f.read()
        except OSError:
            return ""

    def plan(self, managed: Any) -> list:
        """Only the converted tunnels; everything else the toggle writes.

        The keyfile's CONTENT comes from the target's nmcli, which on a fresh
        install is not there yet — `plan` runs before PackagesAction. Skipping
        the tunnel then (the old behaviour) meant a one-pass install finished
        rc=0 with no VPN on the machine and nothing said about it; the tunnel
        appeared only if somebody happened to run `apply` twice.

        So the plan is built from what IS knowable before the transaction —
        whether the keyfile exists at all — and the content comparison is kept
        for when nmcli can answer. `apply` runs long after the packages, so it
        can build what this announces; if it still cannot, it says so loudly
        instead of leaving a declared VPN quietly missing.
        """
        from ..state.change import Change, Op

        changes: list = []
        for name, conf, autoconnect in self._converted():
            current = self._current_keyfile(name)
            desired = self._desired_keyfile(name, conf, autoconnect)
            if not desired:
                # No nmcli to ask. A keyfile that is already there is not
                # evidence of drift — there is nothing to compare it against —
                # but one that is missing is a change this apply owes.
                if not current:
                    changes.append(Change(
                        self._DOMAIN, Op.MODIFY, name,
                        reason="nmcli keyfile is missing (nmcli is not on the "
                               "target yet; it is built during this apply)"))
                continue
            if current != desired:
                changes.append(Change(self._DOMAIN, Op.MODIFY, name,
                                      reason="nmcli keyfile"))
        return changes

    def apply(self, changes) -> None:
        by_name = {name: (conf, auto) for name, conf, auto in self._converted()}
        for change in changes:
            entry = by_name.get(change.item)
            if entry is None:
                continue
            content = self._desired_keyfile(change.item, *entry)
            if not content:
                raise CommandExecutionError(
                    f"wireguard tunnel {change.item!r}: nmcli could not build "
                    "the NetworkManager keyfile. It runs on the target, so "
                    "`networkmanager` has to be installed there first.")
            path = self._abs(self._keyfile_path(change.item))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            # NetworkManager ignores a world-readable keyfile in silence, and
            # this one holds the interface's private key.
            os.chmod(path, 0o600)

    def actual(self) -> set:
        return {name for name, _, _ in self._converted()
                if self._current_keyfile(name)}

    def managed_keys(self) -> dict:
        """Owns nothing unless a tunnel needs converting — a config that only
        declares files it already has keeps the old contract exactly."""
        names = [name for name, _, _ in self._converted()]
        return {self._DOMAIN: names} if names else {}

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
