"""Turn a wg-quick ``.conf`` into a NetworkManager keyfile — via nmcli.

dasik used to refuse the pair (``.conf`` + ``backend: networkmanager``) for two
reasons. The first still holds: the two formats carry the same private key in
different shapes, and hand-translating one into the other is a second copy of a
secret nobody reviewed. The second — that ``nmcli`` needs a running daemon, so
there is no way to do it inside ``arch-chroot /mnt`` — turned out to be true
only of ``import``::

    $ nmcli --offline connection import type wireguard file x.conf
    Error: command doesn't support --offline mode.
    $ nmcli --offline connection add type wireguard …
    [connection] … [wireguard] … [wireguard-peer.<key>] …

``--offline add`` is a pure function: no daemon, no D-Bus, a keyfile on stdout.
So **nmcli** still writes the secret and this module only maps wg-quick fields
onto its arguments, which is the whole point — the objection was never about
converting, it was about who does it.

The uuid is derived from the tunnel name rather than left to nmcli, which
invents a random one per run. Without that, the generated keyfile differs on
every call and the domain never converges.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Dict, List, Tuple

# A fixed namespace, so the same tunnel name yields the same uuid on every
# machine and every run. Any constant would do; this one is dasik's.
_NAMESPACE = _uuid.UUID("6f5902ac-237b-4bd8-9a3e-1d5a0b6f7c21")


def stable_uuid(name: str) -> str:
    """The connection uuid for *name*: same input, same uuid, forever."""
    return str(_uuid.uuid5(_NAMESPACE, f"dasik-wireguard:{name}"))


def wants_nm_conversion(content: str, declared: str) -> bool:
    """True for the one pair that needs converting: a wg-quick conf asked to be
    served by NetworkManager. Everything else is served by the file as it is."""
    return declared == "networkmanager" and "[Interface]" in content and not (
        "[connection]" in content.replace(" ", ""))


def _parse(conf: str) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """``([Interface] fields, [Peer] sections)`` from a wg-quick conf.

    Comments are dropped rather than parsed: ProtonVPN ships ``# Bouncing = 7``
    and friends, and a parser that keeps them hands nmcli properties it has
    never heard of.
    """
    interface: Dict[str, str] = {}
    peers: List[Dict[str, str]] = []
    current: Dict[str, str] | None = None
    for raw in conf.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower() == "[interface]":
            current = interface
            continue
        if line.lower() == "[peer]":
            current = {}
            peers.append(current)
            continue
        if "=" not in line or current is None:
            continue
        key, _, value = line.partition("=")
        current[key.strip().lower()] = value.strip()
    return interface, peers


def _split_families(values: str) -> Tuple[List[str], List[str]]:
    """A comma-separated address/DNS list, split into (IPv4, IPv6).

    One list in a conf is two properties in a keyfile, and nmcli rejects an
    IPv6 address handed to ``ipv4.addresses``.
    """
    v4: List[str] = []
    v6: List[str] = []
    for item in (p.strip() for p in values.split(",")):
        if not item:
            continue
        (v6 if ":" in item else v4).append(item)
    return v4, v6


def _peer_argument(peer: Dict[str, str], name: str) -> str:
    """One ``+wireguard.peers`` value: the public key, then its attributes."""
    key = peer.get("publickey")
    if not key:
        raise ValueError(
            f"wireguard tunnel {name!r}: a [Peer] section without a PublicKey")
    parts = [key]
    if peer.get("endpoint"):
        parts.append(f"endpoint={peer['endpoint']}")
    if peer.get("persistentkeepalive"):
        parts.append(f"persistent-keepalive={peer['persistentkeepalive']}")
    if peer.get("presharedkey"):
        # flags=0 means "the secret is in this file". Without it NetworkManager
        # treats it as agent-owned and asks a human, which no boot can answer.
        parts.append(f"preshared-key={peer['presharedkey']}")
        parts.append("preshared-key-flags=0")
    allowed = [a.strip() for a in peer.get("allowedips", "").split(",") if a.strip()]
    if allowed:
        # ';' is nmcli's separator here, and a trailing one is an empty entry it
        # rejects with "invalid allowed-ip ''".
        parts.append("allowed-ips=" + ";".join(allowed))
    return " ".join(parts)


def nmcli_argv(name: str, conf: str, autoconnect: bool) -> List[str]:
    """The ``nmcli`` arguments that print the keyfile for *conf* on stdout.

    Deliberately returns the argv rather than running it: the caller runs it on
    the TARGET (where `networkmanager` is installed), and a pure function is
    what makes the mapping testable without nmcli present.
    """
    interface, peers = _parse(conf)
    private_key = interface.get("privatekey")
    if not private_key:
        raise ValueError(
            f"wireguard tunnel {name!r}: the conf has no PrivateKey in its "
            "[Interface] section")
    if not peers:
        raise ValueError(
            f"wireguard tunnel {name!r}: the conf has no [Peer] section, so "
            "there is nothing to connect to")

    argv: List[str] = [
        "--offline", "connection", "add", "type", "wireguard",
        "con-name", name, "ifname", name,
        "connection.uuid", stable_uuid(name),
        "connection.autoconnect", "yes" if autoconnect else "no",
        "wireguard.private-key", private_key,
    ]
    if interface.get("listenport"):
        argv += ["wireguard.listen-port", interface["listenport"]]
    if interface.get("mtu"):
        argv += ["wireguard.mtu", interface["mtu"]]

    v4, v6 = _split_families(interface.get("address", ""))
    dns4, dns6 = _split_families(interface.get("dns", ""))
    if v4:
        argv += ["ipv4.method", "manual", "ipv4.addresses", ",".join(v4)]
    if dns4:
        argv += ["ipv4.dns", ",".join(dns4)]
    if v6:
        argv += ["ipv6.method", "manual", "ipv6.addresses", ",".join(v6)]
    if dns6:
        argv += ["ipv6.dns", ",".join(dns6)]

    for peer in peers:
        argv += ["+wireguard.peers", _peer_argument(peer, name)]
    return argv
