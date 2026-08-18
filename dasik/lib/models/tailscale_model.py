"""Model for the tailscale block — preferences written to the tailscaled conffile.

Every field maps to a key ``tailscaled --config`` accepts; the map lives in
:mod:`dasik.lib.actions.tailscale_action` and was pinned against the binary,
since the ``alpha0`` schema is undocumented.

``None`` means "not declared", which is deliberately different from declaring
the default: an absent key leaves the preference to tailscale, while a declared
one takes it away from ``tailscale set`` for as long as the block exists.

There is no ``auth_key`` field. The conffile accepts one, but it is a tailnet
credential and this config is meant to live in Git — ``dasik save`` commits it.
Logging in stays a manual ``tailscale up``: the node key in
``/var/lib/tailscale/tailscaled.state`` is that machine's identity in the
tailnet, so it is not portable between machines even in principle.
"""
import ipaddress
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A DNS label as the control plane will accept it, and as `tailscale set
# --hostname` documents: letters, digits and hyphens, not starting or ending
# with one.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
# useradd(8)'s NAME_REGEX, which is what an operator has to be for tailscaled to
# resolve it.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*\$?$")

NetfilterMode = Literal["on", "nodivert", "off"]


class TailscaleModel(BaseModel):
    """Preferences rendered into /etc/tailscale/tailscaled.conf."""

    # tailscaled itself refuses a conffile with an unknown field, and a pref
    # dasik silently dropped would be the same bug one layer up: `accpet_routes`
    # would validate, render to nothing, converge, and never route a packet.
    # Safe to be strict here because the block is new — no existing config can
    # carry an extra key.
    model_config = ConfigDict(extra="forbid")

    accept_routes: Optional[bool] = Field(
        None, description="Accept subnet routes advertised by other nodes")
    accept_dns: Optional[bool] = Field(
        None, description="Accept DNS configuration from the admin panel")
    ssh: Optional[bool] = Field(
        None, description="Run the Tailscale SSH server (conffile RunSSHServer)")
    web_client: Optional[bool] = Field(
        None, description="Run the local web client (conffile RunWebClient)")
    shields_up: Optional[bool] = Field(
        None, description="Block incoming connections from the tailnet")
    exit_node: Optional[str] = Field(
        None, description="Exit node to route traffic through (IP, base name, or auto:any)")
    exit_node_allow_lan_access: Optional[bool] = Field(
        None, description="Reach the local network while using an exit node "
                          "(conffile AllowLANWhileUsingExitNode)")
    advertise_routes: List[str] = Field(
        default_factory=list, description="Subnets to advertise, as CIDR")
    advertise_exit_node: Optional[bool] = Field(
        None, description="Offer this machine as an exit node")
    hostname: Optional[str] = Field(
        None, description="Hostname to use instead of the OS one")
    operator: Optional[str] = Field(
        None, description="Unix user allowed to run tailscale without sudo "
                          "(conffile OperatorUser)")
    netfilter_mode: Optional[NetfilterMode] = None
    posture_checking: Optional[bool] = Field(
        None, description="Allow the control plane to collect device posture")
    server_url: Optional[str] = Field(
        None, description="Control plane URL; only for a self-hosted coordinator")
    auth_key_file: Optional[str] = Field(
        None, description="Absolute path (on the target) of a file holding a "
                          "tailnet auth key; rendered as the conffile's "
                          "AuthKey file: reference. The PATH may live in Git; "
                          "the key itself never does — conffile mode has no "
                          "interactive login (issue #318)")
    # Not a conffile key: this is the daemon's listening port, which lives in
    # /etc/default/tailscaled next to the --config flag. dasik has to write it
    # because the vendor unit interpolates ${PORT} and an empty one is not a
    # working command line.
    port: Optional[int] = Field(
        None, ge=1, le=65535,
        description="UDP port tailscaled listens on (default 41641)")

    @field_validator("advertise_routes")
    @classmethod
    def _real_cidrs(cls, v: List[str]) -> List[str]:
        # tailscaled rejects the file outright on a bad prefix, which is a daemon
        # that will not start. Catch it while it is still a config error.
        for route in v:
            # `ipaddress` happily reads a bare address as a /32, but tailscaled
            # parses these with netip.ParsePrefix, which requires the length —
            # so accepting "10.0.0.0" here would hand the daemon a file it
            # refuses to start with.
            if "/" not in route:
                raise ValueError(
                    f"advertise_routes entry {route!r} needs an explicit prefix "
                    "length (e.g. '10.0.0.0/8'); tailscaled rejects a bare address")
            try:
                ipaddress.ip_network(route, strict=False)
            except ValueError as exc:
                raise ValueError(f"advertise_routes entry {route!r} is not a CIDR "
                                 f"network: {exc}") from exc
        return v

    @field_validator("auth_key_file")
    @classmethod
    def _absolute_path_not_a_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v.startswith("tskey-"):
            # Somebody pasted the key itself: exactly the secret-in-Git this
            # field exists to avoid.
            raise ValueError(
                "auth_key_file takes the PATH of a file holding the key, "
                "never the key itself — a synced config is committed to Git")
        if not v.startswith("/"):
            raise ValueError(
                f"auth_key_file must be an absolute path on the target, got {v!r}")
        return v

    @field_validator("hostname")
    @classmethod
    def _dns_label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _HOSTNAME_RE.match(v):
            raise ValueError(
                f"hostname {v!r} is not a DNS label (letters, digits and "
                "hyphens, not leading or trailing)")
        return v

    @field_validator("operator")
    @classmethod
    def _unix_user(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _USERNAME_RE.match(v):
            raise ValueError(f"operator {v!r} is not a valid Unix username")
        return v

    @field_validator("exit_node")
    @classmethod
    def _single_line_exit_node(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (not v.strip() or len(v.split()) != 1):
            raise ValueError("exit_node must be a single token (IP, base name, "
                             "or auto:any)")
        return v

    @field_validator("server_url")
    @classmethod
    def _https_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.startswith(("https://", "http://")) or len(v.split()) != 1:
            raise ValueError("server_url must be a single http(s) URL")
        return v
