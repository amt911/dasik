"""Action: apply firewalld zone rules declaratively (v3 domain "firewall").

Installing firewalld and enabling firewalld.service is handled by the `expand`
toggle (→ packages + systemd). This action applies the ZONE RULES that toggle
can't express: allowed services, rich rules, and services to remove from the
default zone. It uses ``firewall-offline-cmd`` — which works inside the chroot
without firewalld running and translates rich-rule syntax into the zone XML —
and is idempotent: a change is emitted only for a rule that is missing (or a
service that should be removed but is still present), so a converged firewall
re-plans to nothing.
"""
from typing import Any, List, Set

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..state.change import Change, Op


class FirewallAction(AbstractAction):
    """Reconcile firewalld zone rules for the default (public) zone."""

    _DOMAIN = "firewall"
    _ZONE = "public"

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

    # --- target-aware firewall-offline-cmd ---------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _offline(self, args: List[str]):
        return Command.execute("firewall-offline-cmd", args, target=self._target())

    def _list(self, kind: str) -> Set[str]:
        """Current zone entries. kind='services' (space-sep) | 'rich-rules'."""
        try:
            result = self._offline([f"--zone={self._ZONE}", f"--list-{kind}"])
        except Exception:
            return set()
        out = getattr(result, "stdout", b"") or b""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if kind == "services":
            return {s for s in out.split() if s}
        return {line.strip() for line in out.splitlines() if line.strip()}

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        if not self.enable or self._target() is None:
            return set()
        svc = {f"service:{s}" for s in self._list("services")}
        rich = {f"rich:{r}" for r in self._list("rich-rules")}
        return svc | rich

    def plan(self, managed):
        if not self.enable:
            return []
        changes: List[Change] = []
        current_svc = self._list("services")
        for s in self.allowed:
            if s not in current_svc:
                changes.append(Change(self._DOMAIN, Op.ENABLE, f"service:{s}",
                                      reason="allow service"))
        for s in self.remove:
            if s in current_svc:
                changes.append(Change(self._DOMAIN, Op.DISABLE, f"service:{s}",
                                      reason="remove service"))
        current_rich = self._list("rich-rules")
        for r in self.rich:
            if r not in current_rich:
                changes.append(Change(self._DOMAIN, Op.ENABLE, f"rich:{r}",
                                      reason="rich rule"))
        return changes

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        for c in changes:
            if c.item.startswith("service:"):
                svc = c.item[len("service:"):]
                flag = "--add-service" if c.op is Op.ENABLE else "--remove-service"
                self._offline([f"--zone={self._ZONE}", f"{flag}={svc}"])
            elif c.item.startswith("rich:"):
                rule = c.item[len("rich:"):]
                self._offline([f"--zone={self._ZONE}", f"--add-rich-rule={rule}"])

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [f"service:{s}" for s in self.allowed]
                + [f"rich:{r}" for r in self.rich]}

    def import_state(self, managed=None) -> dict:
        return {}

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
