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
from ..exceptions.exceptions import NetworkTypeNotFoundException

_HOSTNAME = "/etc/hostname"
_HOSTS = "/etc/hosts"


class NetworkAction(CompositeV3Action):
    """Configure hostname and hosts file declaratively (composite v3 domain)."""

    _DOMAIN = "network"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        net: Dict[str, Any] = cfg.get("network", {}) or {}
        self.type: str = net.get("type", "")
        self.hostname: str = cfg.get("hostname", "")
        self.add_default_hosts: bool = net.get("add_default_hosts", False)

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
        if not self._declared():
            return {}
        st = self._actual_state() or self._desired_state()
        return {
            "hostname": st["hostname"],
            "network": {"type": self.type, "add_default_hosts": st["default_hosts"]},
        }

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
