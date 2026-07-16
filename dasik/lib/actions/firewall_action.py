"""Action: firewalld default-zone rules, written declaratively (idempotent).

Installing firewalld + enabling the service is the `firewall` expand toggle's job
(packages + systemd). This action owns the RULES the toggle can't express:
allowed services, rich rules, and services removed from the default zone.

It writes the complete ``/etc/firewalld/zones/public.xml`` — dasik owns the file
— instead of driving ``firewall-offline-cmd``. That avoids firewalld's
default-service quirk (``--remove-service`` does not strip a built-in default,
and ``--list-services`` reports defaults, so a remove_service re-fired on every
apply). The desired zone is: (default services − remove_services) + allowed
services + rich rules. Idempotent by construction: a change is planned only when
the on-disk file differs from the desired content.
"""
import os
import re
from typing import Any, List

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op

_ZONE_PATH = "/etc/firewalld/zones/public.xml"
# firewalld's upstream `public` zone default services.
_DEFAULT_SERVICES = ["dhcpv6-client", "ssh"]


def _rich_rule_to_xml(rule: str) -> str:
    """Convert a firewall-cmd rich-rule string to a zone-XML <rule> element.

    Tolerant of quoted/unquoted values. Supports the common grammar: family,
    source/destination address, service name, port+protocol, and the terminal
    action (accept|reject|drop). Unknown clauses are ignored, never dropped
    silently to a crash.
    """
    def grab(pattern: str):
        m = re.search(pattern, rule)
        return m.group(1) if m else None

    family = grab(r'family[=\s]"?([^"\s]+)"?')
    inner: List[str] = []
    src = grab(r'source\s+address[=\s]"?([^"\s]+)"?')
    if src:
        inner.append(f'<source address="{src}"/>')
    dst = grab(r'destination\s+address[=\s]"?([^"\s]+)"?')
    if dst:
        inner.append(f'<destination address="{dst}"/>')
    svc = grab(r'service\s+name[=\s]"?([^"\s]+)"?')
    if svc:
        inner.append(f'<service name="{svc}"/>')
    port = grab(r'\bport\s+port[=\s]"?([^"\s]+)"?')
    proto = grab(r'protocol[=\s]"?([^"\s]+)"?')
    if port and proto:
        inner.append(f'<port port="{port}" protocol="{proto}"/>')
    for action in ("accept", "reject", "drop"):
        if re.search(rf'\b{action}\b', rule):
            inner.append(f'<{action}/>')
            break
    attrs = f' family="{family}"' if family else ""
    return f'<rule{attrs}>' + "".join(inner) + "</rule>"


class FirewallAction(AbstractAction):
    """Own the firewalld public zone file declaratively."""

    _DOMAIN = "firewall"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg = config if isinstance(config, dict) else {}
        self.enable: bool = cfg.get("enable", False)
        self.allowed: List[str] = cfg.get("allowed_services", [])
        self.rich: List[str] = cfg.get("rich_rules", [])
        self.remove: List[str] = cfg.get("remove_services", [])

    @property
    def name(self) -> str:
        return "Firewall Rules"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _zone_file(self) -> str:
        t = self._target()
        return t.path(_ZONE_PATH) if t is not None else "/mnt" + _ZONE_PATH

    def _desired_xml(self) -> str:
        services = sorted((set(_DEFAULT_SERVICES) - set(self.remove)) | set(self.allowed))
        lines = ['<?xml version="1.0" encoding="utf-8"?>', "<zone>", "  <short>Public</short>"]
        lines += [f'  <service name="{s}"/>' for s in services]
        lines += ["  " + _rich_rule_to_xml(r) for r in self.rich]
        lines.append("</zone>")
        return "\n".join(lines) + "\n"

    def _current_xml(self):
        try:
            with open(self._zone_file(), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        return {"public"} if (self.enable and self._current_xml() is not None) else set()

    def plan(self, managed):
        if not self.enable:
            return []
        if self._current_xml() == self._desired_xml():
            return []
        return [Change(self._DOMAIN, Op.MODIFY, "public", reason="zone rules")]

    def apply(self, changes) -> None:
        if not changes:
            return
        path = self._zone_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(self._desired_xml())

    def managed_keys(self) -> dict:
        return {self._DOMAIN: ["public"] if self.enable else []}

    @staticmethod
    def _decode(out) -> str:
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")

    def _fw_query(self, *args) -> "Any":
        """Run `firewall-offline-cmd <args>` against the target; return its stdout
        text, or None if it fails (best-effort). Offline (not `firewall-cmd`) on
        purpose: it reads /etc/firewalld directly, so it needs no running daemon /
        D-Bus session — the reliable path for sync (root) and for a /mnt install
        target reached via arch-chroot. It DOES require root, which sync has."""
        try:
            res = Command.execute("firewall-offline-cmd", list(args), target=self._target())
        except Exception:
            return None
        if getattr(res, "returncode", 1) != 0:
            return None
        return self._decode(res.stdout)

    def import_state(self, managed=None) -> dict:
        """Capture the live firewalld permanent public zone back into a `firewall`
        block. `--list-rich-rules` returns rules in the same syntax `rich_rules`
        expects, so they round-trip; allowed/removed services are the diff against
        firewalld's upstream `public` defaults. Nothing captured when
        firewall-offline-cmd is unavailable (sync leaves the section untouched)."""
        services_txt = self._fw_query("--zone=public", "--list-services")
        if services_txt is None:
            return {}
        services = set(services_txt.split())
        rich_txt = self._fw_query("--zone=public", "--list-rich-rules") or ""
        rich = [ln.strip() for ln in rich_txt.splitlines() if ln.strip()]

        frag: dict = {"enable": True}
        allowed = sorted(services - set(_DEFAULT_SERVICES))
        removed = sorted(set(_DEFAULT_SERVICES) - services)
        if allowed:
            frag["allowed_services"] = allowed
        if removed:
            frag["remove_services"] = removed
        if rich:
            frag["rich_rules"] = rich
        return {"firewall": frag}

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
