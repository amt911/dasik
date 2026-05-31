"""Action: configure hostname + /etc/hosts (and network manager choice).

Ported from the legacy ``_before_check``/``do_action`` form to the
AbstractAction contract (issue #66). Needs the root-level ``hostname`` as
well as the ``network`` section, so it is registered with config_key
``__root__`` and reads both from the root config.

Idempotent: only rewrites when /mnt/etc/hostname differs or the default
loopback hosts block is missing (when ``add_default_hosts`` is set).
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict
from .abstract_action import AbstractAction
from ..exceptions.exceptions import NetworkTypeNotFoundException

_HOSTNAME_FILE = "/mnt/etc/hostname"
_HOSTS_FILE = "/mnt/etc/hosts"


class NetworkAction(AbstractAction):
    """Configure hostname and hosts file declaratively."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        net: Dict[str, Any] = cfg.get("network", {}) or {}
        self.type: str = net.get("type", "")
        self.hostname: str = cfg.get("hostname", "")
        self.add_default_hosts: bool = net.get("add_default_hosts", False)
        self.DEFAULT_HOSTS = (
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
            f"127.0.1.1 {self.hostname}\n"
        )

    @property
    def name(self) -> str:
        return "Network Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _hostname_needs_write(self) -> bool:
        if not os.path.exists(_HOSTNAME_FILE):
            return True
        with open(_HOSTNAME_FILE, "r") as f:
            return f.read().strip() != self.hostname

    def _hosts_needs_write(self) -> bool:
        if not self.add_default_hosts:
            return False
        if not os.path.exists(_HOSTS_FILE):
            return True
        with open(_HOSTS_FILE, "r") as f:
            return re.search(
                rf"^{re.escape(self.DEFAULT_HOSTS)}", f.read(), re.MULTILINE
            ) is None

    def is_needed(self) -> bool:
        return self._hostname_needs_write() or self._hosts_needs_write()

    def execute(self) -> None:  # pragma: no cover - writes /mnt/etc files
        self._clear_hosts_file()
        with open(_HOSTNAME_FILE, "w") as f:
            f.write(self.hostname)
        if self.add_default_hosts:
            with open(_HOSTS_FILE, "a") as f:
                f.write(self.DEFAULT_HOSTS)

        if self.type not in ("NetworkManager", "systemd-networkd"):
            raise NetworkTypeNotFoundException

    def _clear_hosts_file(self) -> None:  # pragma: no cover - writes /mnt/etc
        if not os.path.exists(_HOSTS_FILE):
            return
        with open(_HOSTS_FILE, "r+") as hosts_file:
            lines = hosts_file.readlines()
            hosts_file.seek(0)
            for line in lines:
                if not re.match(r"^(127\.0\.0\.1|::1|127\.0\.1\.1)", line):
                    hosts_file.write(line)
            hosts_file.truncate()

    def verify(self) -> bool:
        return not self.is_needed()
