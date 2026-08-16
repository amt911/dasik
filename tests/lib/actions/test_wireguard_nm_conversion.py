"""A wg-quick `.conf` declared `backend: networkmanager` is converted, not refused.

dasik used to reject that pair outright, on the reasoning that translating
between the two formats means writing a second copy of a private key that
nobody reviewed, and that `nmcli connection import` needs a running daemon and
D-Bus — neither of which exists inside `arch-chroot /mnt`.

The first half stands. The second half was only true of `import`:

    $ nmcli --offline connection import type wireguard file x.conf
    Error: command doesn't support --offline mode.
    $ nmcli --offline connection add type wireguard …
    [connection] … [wireguard] … [wireguard-peer.<key>] …

`--offline add` is a pure function: no daemon, no D-Bus, a keyfile on stdout.
So nmcli still writes the secret — dasik only maps fields onto its arguments —
and it works in a chroot, which is what makes an install-time apply possible.

The `.conf` stays the file the repository keeps: the conversion is a fact about
the target, not about the config.
"""
import re

import pytest

from dasik.lib.actions.wireguard_nm import (
    nmcli_argv, stable_uuid, wants_nm_conversion)
from dasik.lib.expand.toggles import resolve_backend


CONF = """[Interface]
# Key for Thinkpad
PrivateKey = qOaJ8ZQ1vXWJKcVX0mZ3nR5tYuIoPaSdFgHjKlZxCv0=
Address = 10.2.0.2/32, 2a07:b944::2:2/128
DNS = 10.2.0.1, 2a07:b944::2:1

[Peer]
# ES#287
PublicKey = DY16h7yFAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 79.127.139.158:51820
PersistentKeepalive = 25
"""

NMCONNECTION = """[connection]
id=vpn
type=wireguard
interface-name=vpn

[wireguard]
private-key=qOaJ8ZQ1vXWJKcVX0mZ3nR5tYuIoPaSdFgHjKlZxCv0=
"""


def _argv(**kwargs):
    kwargs.setdefault("name", "vpn")
    kwargs.setdefault("conf", CONF)
    kwargs.setdefault("autoconnect", True)
    return nmcli_argv(**kwargs)


def _value_after(argv, key):
    return argv[argv.index(key) + 1]


# --------------------------------------------------------------------------- #
#  the pair is accepted now
# --------------------------------------------------------------------------- #

def test_a_conf_declared_networkmanager_is_no_longer_refused():
    assert resolve_backend(CONF, "networkmanager", "vpn") == "networkmanager"


def test_a_conf_still_resolves_to_wg_quick_on_auto():
    """`auto` keeps meaning "whatever the file is"; conversion is opt-in."""
    assert resolve_backend(CONF, "auto", "vpn") == "wg-quick"


def test_a_keyfile_declared_wg_quick_is_still_refused():
    """There is no conversion the other way — nmcli cannot emit a wg-quick conf,
    and wg-quick cannot read a keyfile. Refusing beats inventing one."""
    with pytest.raises(ValueError, match="does not convert"):
        resolve_backend(NMCONNECTION, "wg-quick", "vpn")


def test_only_the_conf_to_nm_direction_asks_for_conversion():
    assert wants_nm_conversion(CONF, "networkmanager") is True
    assert wants_nm_conversion(CONF, "auto") is False
    assert wants_nm_conversion(CONF, "wg-quick") is False
    assert wants_nm_conversion(NMCONNECTION, "networkmanager") is False


# --------------------------------------------------------------------------- #
#  the argv nmcli is handed
# --------------------------------------------------------------------------- #

def test_the_interface_fields_are_mapped():
    argv = _argv()

    assert _value_after(argv, "wireguard.private-key") == (
        "qOaJ8ZQ1vXWJKcVX0mZ3nR5tYuIoPaSdFgHjKlZxCv0=")
    assert _value_after(argv, "ipv4.addresses") == "10.2.0.2/32"
    assert _value_after(argv, "ipv6.addresses") == "2a07:b944::2:2/128"
    assert _value_after(argv, "ipv4.dns") == "10.2.0.1"
    assert _value_after(argv, "ipv6.dns") == "2a07:b944::2:1"


def test_addresses_are_split_by_family():
    """`Address` is one comma-separated list in a conf and two properties in a
    keyfile; putting an IPv6 address in ipv4.addresses is rejected by nmcli."""
    argv = _argv()

    assert ":" not in _value_after(argv, "ipv4.addresses")
    assert ":" in _value_after(argv, "ipv6.addresses")


def test_the_peer_carries_its_endpoint_and_allowed_ips():
    argv = _argv()
    peer = _value_after(argv, "+wireguard.peers")

    assert peer.startswith("DY16h7yFAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0=")
    assert "endpoint=79.127.139.158:51820" in peer
    assert "persistent-keepalive=25" in peer
    # nmcli separates allowed-ips with ';' and rejects a trailing empty one
    assert "allowed-ips=0.0.0.0/0;::/0" in peer
    assert not peer.rstrip().endswith(";")


def test_every_peer_gets_its_own_argument():
    two = CONF + """
[Peer]
PublicKey = mJ0AogpjAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0=
AllowedIPs = 10.0.0.0/8
Endpoint = 5.6.7.8:51820
"""
    argv = _argv(conf=two)

    assert argv.count("+wireguard.peers") == 2


def test_a_preshared_key_survives():
    argv = _argv(conf=CONF.replace(
        "PersistentKeepalive = 25",
        "PresharedKey = mJ0AogpjAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0=\n"
        "PersistentKeepalive = 25"))
    peer = _value_after(argv, "+wireguard.peers")

    assert "preshared-key=mJ0AogpjAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0=" in peer
    # without the flag NetworkManager treats the secret as agent-owned and asks
    # for it interactively, which no boot can answer
    assert "preshared-key-flags=0" in peer


def test_comments_in_the_conf_are_ignored():
    """ProtonVPN ships `# Bouncing = 7` style comments; a naive parser turns
    them into properties nmcli has never heard of."""
    argv = _argv(conf=CONF.replace("[Interface]", "[Interface]\n# NetShield = 2"))

    assert not any("NetShield" in a for a in argv)


def test_enable_becomes_autoconnect():
    assert _value_after(_argv(autoconnect=True), "connection.autoconnect") == "yes"
    assert _value_after(_argv(autoconnect=False), "connection.autoconnect") == "no"


def test_a_conf_with_no_private_key_is_refused():
    with pytest.raises(ValueError, match="PrivateKey"):
        _argv(conf="[Peer]\nPublicKey = x\n")


def test_a_conf_with_no_peer_is_refused():
    with pytest.raises(ValueError, match="Peer"):
        _argv(conf="[Interface]\nPrivateKey = k\n")


# --------------------------------------------------------------------------- #
#  idempotency
# --------------------------------------------------------------------------- #

def test_the_uuid_is_derived_from_the_name():
    """nmcli invents a random uuid per run, which would make every plan see a
    different file forever. Deriving it from the name makes the keyfile a pure
    function of the config."""
    assert stable_uuid("vpn") == stable_uuid("vpn")
    assert stable_uuid("vpn") != stable_uuid("other")
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        stable_uuid("vpn"))


def test_the_uuid_is_passed_to_nmcli():
    assert _value_after(_argv(), "connection.uuid") == stable_uuid("vpn")


def test_the_argv_is_a_pure_function_of_its_inputs():
    assert _argv() == _argv()


def test_the_command_never_touches_the_daemon():
    """`--offline` is what makes this usable inside a chroot; without it nmcli
    talks to NetworkManager over D-Bus and fails where it matters most."""
    argv = _argv()

    assert argv[0] == "--offline"
    assert "import" not in argv
