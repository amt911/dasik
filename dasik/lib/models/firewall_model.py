"""Models for firewall configuration (firewalld or ufw)."""
import re
from typing import Dict, List, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# A ufw rule as dasik accepts it: an action plus a target that ufw REPORTS the
# same way it was written. `allow ssh` is refused on purpose — ufw resolves it
# and then prints `22/tcp`, so dasik could never tell a converged machine from a
# drifted one. Targets are a port (or range) with an optional protocol, or an
# application-profile name from /etc/ufw/applications.d.
_UFW_RULE_RE = re.compile(
    r"(allow|deny|reject|limit)\s+"
    r"(\d+(?::\d+)?(?:/(?:tcp|udp))?|[A-Za-z][A-Za-z0-9._+-]*)$"
)
# A bare word target must be an app profile, not a service name from
# /etc/services — those are the ones ufw rewrites.
_SERVICE_NAME_TRAP = {"ssh", "http", "https", "ftp", "smtp", "dns", "domain",
                      "telnet", "imap", "pop3", "ntp", "snmp", "ldap", "smb"}


class FirewallZoneModel(BaseModel):
    """One firewalld zone other than `public`.

    ``allowed_services`` is the zone's **complete** service list, not a diff.
    ``remove_services`` exists at the top level only because firewalld's
    upstream `public` allows `ssh` and `dhcpv6-client` out of the box; naming a
    zone explicitly is already the whole statement, so there is nothing to
    subtract and the field is refused here rather than mean something different
    per zone.
    """
    model_config = {"extra": "forbid"}

    allowed_services: List[str] = Field(
        default_factory=list,
        description="The complete list of services this zone allows.")
    rich_rules: List[str] = Field(
        default_factory=list,
        description="Rich rules for this zone (firewall-cmd --add-rich-rule syntax).")


class FirewallModel(BaseModel):
    """Firewall rules. `backend` picks which tool applies them."""
    enable: bool = Field(default=False)
    backend: Literal["firewalld", "ufw"] = Field(
        default="firewalld",
        description="Which firewall to install and drive. Default firewalld, so "
                    "configs written before this field keep their meaning.",
    )
    remove_services: List[str] = Field(
        default_factory=list,
        description="firewalld only: services to remove from the default zone "
                    "(e.g. ssh, which firewalld's `public` zone allows by default)"
    )
    rich_rules: List[str] = Field(
        default_factory=list,
        description="firewalld only: rich rules (firewall-cmd --add-rich-rule syntax)"
    )
    allowed_services: List[str] = Field(
        default_factory=list,
        description="Services to allow. firewalld: a service name it knows "
                    "(samba, syncthing). ufw: an application profile from "
                    "/etc/ufw/applications.d."
    )
    rules: List[str] = Field(
        default_factory=list,
        description="ufw only: verbatim rules, '<action> <target>' — e.g. "
                    "'allow 22/tcp', 'limit 22/tcp', 'allow Syncthing'."
    )
    zones: Dict[str, FirewallZoneModel] = Field(
        default_factory=dict,
        description="firewalld only: zones OTHER than `public`, each owned "
                    "completely by dasik. The top-level fields are the public "
                    "zone; this is for a machine that also customises `home`, "
                    "`work`, `internal`…"
    )

    @field_validator("rules")
    @classmethod
    def _validate_rules(cls, v: List[str]) -> List[str]:
        for rule in v:
            match = _UFW_RULE_RE.fullmatch(rule.strip())
            if not match:
                raise ValueError(
                    f"Invalid ufw rule {rule!r}: expected '<action> <target>' where "
                    f"action is allow/deny/reject/limit and target is a port "
                    f"(22/tcp, 6000:6007/udp) or an application profile name."
                )
            target = match.group(2)
            if target.lower() in _SERVICE_NAME_TRAP:
                raise ValueError(
                    f"Invalid ufw rule {rule!r}: ufw resolves the service name "
                    f"{target!r} and then reports the rule as a port, so dasik "
                    f"could never tell an applied rule from a missing one. Write "
                    f"the port instead (e.g. 'allow 22/tcp')."
                )
        return v

    @model_validator(mode="after")
    def _validate_backend_fields(self) -> "FirewallModel":
        """Fields belong to one backend or the other; the wrong one fails closed.

        Silently ignoring `rich_rules` under ufw would widen access without
        saying so — the rate limit in `accept limit value="2/m"` is exactly the
        clause that keeps such a rule narrow.
        """
        if self.backend == "ufw":
            if self.rich_rules:
                raise ValueError(
                    "rich_rules are firewalld syntax and cannot be applied by "
                    "ufw; express them as `rules` entries instead."
                )
            if self.remove_services:
                raise ValueError(
                    "remove_services is firewalld-only: it exists because "
                    "firewalld's `public` zone allows some services by default. "
                    "ufw denies all incoming traffic by default, so there is "
                    "nothing to remove."
                )
            if self.zones:
                raise ValueError(
                    "zones is firewalld-only: ufw has no concept of a zone. "
                    "Express the rules as `rules` entries instead."
                )
        elif self.rules:
            raise ValueError(
                "rules is ufw-only syntax; with the firewalld backend use "
                "allowed_services and rich_rules."
            )
        if "public" in self.zones:
            raise ValueError(
                "the public zone is the top-level allowed_services / "
                "remove_services / rich_rules; declaring it again under `zones` "
                "would let one config contradict itself."
            )
        return self
