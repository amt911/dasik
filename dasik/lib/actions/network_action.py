"""Action: configure hostname + /etc/hosts (composite v3 domain "network").

Registered under ``__root__``: reads root-level ``hostname`` plus the
``network`` section. The comparison record is (hostname, default_hosts
presence); ``network.type`` is validated on apply but excluded from the record
(no on-disk file) and passed through verbatim on import. Target-aware.

Nothing-declared guard: with no ``hostname`` the action is a no-op (empty plan,
import_state {}, _set_value returns without validating type) so minimal /
package-only configs do not write an empty hostname or raise on an absent type.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, Optional
from .composite_action import CompositeV3Action
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError, NetworkTypeNotFoundException

_HOSTNAME = "/etc/hostname"
_HOSTS = "/etc/hosts"
# RFC-1123 host: dot-separated labels, each starting/ending alphanumeric, <=63.
# The value is written verbatim to /etc/hostname AND into an /etc/hosts line
# (`127.0.1.1 <hostname>`), so a space/newline would corrupt those files.
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*")


def _validate_hostname(hostname: str) -> None:
    # Empty is valid: it means "no hostname configured" (the action no-ops).
    if not hostname:
        return
    if len(hostname) > 253 or not _HOSTNAME_RE.fullmatch(hostname):
        raise ConfigValidationError(
            f"Invalid hostname {hostname!r}: must be dot-separated RFC-1123 labels "
            f"([A-Za-z0-9-], no leading/trailing '-', <=63 each, no spaces/newlines)."
        )


class NetworkAction(CompositeV3Action):
    """Configure hostname and hosts file declaratively (composite v3 domain)."""

    _DOMAIN = "network"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        net: Dict[str, Any] = cfg.get("network", {}) or {}
        self.type: str = net.get("type", "")
        self.hostname: str = cfg.get("hostname", "")
        _validate_hostname(self.hostname)
        # Same default as NetworkModel, and it has to be repeated here because
        # the action reads the RAW dict: a model-only default would leave the
        # schema promising a block the action then never writes.
        self.add_default_hosts: bool = net.get("add_default_hosts", True)

    @property
    def name(self) -> str:
        return "Network Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _declared(self) -> bool:
        return bool(self.hostname)

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _default_block(self) -> str:
        return (
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
            f"127.0.1.1 {self.hostname}\n"
        )

    def _read(self, canonical: str) -> Optional[str]:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {"hostname": self.hostname, "default_hosts": bool(self.add_default_hosts)}

    def _actual_state(self) -> Optional[dict]:
        hn = self._read(_HOSTNAME)
        if hn is None:
            return None
        hosts = self._read(_HOSTS) or ""
        present = re.search(re.escape(self._default_block()), hosts) is not None
        return {"hostname": hn.strip(), "default_hosts": present}

    # --- guards over the base contract -------------------------------- #

    def plan(self, managed):
        if not self._declared():
            return []
        return super().plan(managed)

    def import_state(self, managed=None) -> dict:
        """Report the hostname, and the network manager the MACHINE runs.

        The type is probed, not copied from the config: `sync` reports reality.
        And when nothing answers — no manager enabled, no declaration to fall
        back on — the `network` key is OMITTED rather than emitted empty. An
        empty type is not one of the two values the schema accepts, so a capture
        carrying it is a capture `dasik check` then rejects (issue #196): the
        round trip breaks silently, and only when someone tries to use the file.
        """
        # NOT gated on `_declared()`: that asks whether the CONFIG names a
        # hostname, and a bootstrap `sync` starts from `{}` — the way you adopt
        # a machine you did not install. The machine's own /etc/hostname is the
        # answer, and gating on the config lost the name of every machine
        # captured that way.
        st = self._actual_state()
        if st is None:
            if not self._declared():
                return {}
            st = self._desired_state()
        if not st["hostname"]:
            return {}
        captured: Dict[str, Any] = {"hostname": st["hostname"]}
        net_type = self._live_type() or self.type
        if net_type:
            captured["network"] = {"type": net_type,
                                   "add_default_hosts": st["default_hosts"]}
        return captured

    # The unit each manager is enabled as. Order is the answer to "both
    # installed": NetworkManager is the one that owns the interfaces when it
    # runs, so it wins.
    _MANAGER_UNITS = (("NetworkManager.service", "NetworkManager"),
                      ("systemd-networkd.service", "systemd-networkd"))

    def _live_type(self) -> str:
        """Which network manager this machine actually starts, or ''."""
        if self._target() is None:
            return ""
        for unit, name in self._MANAGER_UNITS:
            if self._unit_enabled(unit):
                return name
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

    def _import_fragment(self, value) -> dict:  # pragma: no cover - import_state overridden
        return self.import_state()

    def _set_value(self) -> None:
        if not self._declared():
            return
        # An absent type (minimal / hostname-only config) is fine — just write
        # the hostname, per this module's contract. Only a non-empty, unknown
        # type (a typo like "networkmanager") is an error. Requiring a network
        # manager to be declared just to set a hostname blocked otherwise-valid
        # installs at the network step (found by the QEMU install harness).
        if self.type and self.type not in ("NetworkManager", "systemd-networkd"):
            raise NetworkTypeNotFoundException
        self._clear_loopback()
        with open(self._p(_HOSTNAME), "w") as f:
            f.write(self.hostname)
        if self.add_default_hosts:
            with open(self._p(_HOSTS), "a") as f:
                f.write(self._default_block())

    def _clear_loopback(self) -> None:
        path = self._p(_HOSTS)
        if not os.path.exists(path):
            return
        with open(path, "r+") as hf:
            lines = hf.readlines()
            hf.seek(0)
            for line in lines:
                if not re.match(r"^(127\.0\.0\.1|::1|127\.0\.1\.1)", line):
                    hf.write(line)
            hf.truncate()
