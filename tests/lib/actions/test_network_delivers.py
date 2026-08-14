"""Declaring a network manager must produce a machine with a network.

The `network` block wrote /etc/hostname and /etc/hosts, validated the type
string, and stopped there: no package, no unit, no DHCP profile. A config whose
only networking statement was

    "network": {"type": "NetworkManager"}

installed a machine with no NetworkManager on it. Every VM this repo installs
has been network-less for months for exactly this reason, and it was written
down as a harness quirk rather than read as what it was.

The two sample configs that hide it: `install-chunga.json` and
`install-megamix.json` enable `NetworkManager.service` by hand while no
declared package provides it — `systemctl enable` on a unit that does not exist
fails, with the disk already partitioned.
"""
import pytest

from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.validation.preflight import preflight

_DHCP = "/etc/systemd/network/20-dasik-dhcp.network"


def _expanded(**over):
    cfg = {"hostname": "arch", **over}
    return expand_config(cfg)


# --- NetworkManager --------------------------------------------------------- #

def test_networkmanager_brings_its_package_and_unit():
    e = _expanded(network={"type": "NetworkManager"})

    assert "networkmanager" in e["packages"]
    assert "NetworkManager.service" in e["systemd"]["enable_units"]


def test_networkmanager_writes_no_networkd_profile():
    e = _expanded(network={"type": "NetworkManager"})

    assert _DHCP not in [f["path"] for f in e.get("files", [])]


# --- systemd-networkd -------------------------------------------------------- #

def test_networkd_enables_the_units_systemd_already_ships():
    e = _expanded(network={"type": "systemd-networkd"})
    units = e["systemd"]["enable_units"]

    assert "systemd-networkd.service" in units
    assert "systemd-resolved.service" in units
    assert "networkmanager" not in e.get("packages", [])


def test_networkd_gets_a_dhcp_profile_or_it_configures_nothing():
    """`systemd-networkd` with no .network file brings up no interface at all —
    the unit runs and the machine still has no address."""
    e = _expanded(network={"type": "systemd-networkd"})
    profile = [f for f in e["files"] if f["path"] == _DHCP]

    assert profile, "no DHCP profile derived"
    assert "DHCP=yes" in profile[0]["content"]


def test_a_hand_written_profile_wins():
    """Somebody with a static address must not have it overwritten."""
    mine = {"path": "/etc/systemd/network/10-static.network",
            "content": "[Match]\nName=en*\n[Network]\nAddress=10.0.0.5/24\n"}
    e = _expanded(network={"type": "systemd-networkd"}, files=[mine])
    paths = [f["path"] for f in e["files"]]

    assert mine["path"] in paths
    assert _DHCP not in paths


# --- nothing declared -------------------------------------------------------- #

def test_no_network_block_derives_nothing():
    e = _expanded()

    assert not e.get("packages")
    assert "systemd" not in e or not e["systemd"]["enable_units"]


def test_an_empty_type_derives_nothing():
    """A hostname-only config is valid and must stay a hostname-only config."""
    e = _expanded(network={"add_default_hosts": True})

    assert not e.get("packages")


# --- the capture ------------------------------------------------------------- #

def test_the_derived_pieces_are_attributed_to_the_block():
    cfg = {"hostname": "arch", "network": {"type": "NetworkManager"}}
    captured = subtract_contributions(expand_config(cfg), cfg)

    assert "networkmanager" not in captured.get("packages", [])
    assert "NetworkManager.service" not in captured["systemd"]["enable_units"]


# --- preflight --------------------------------------------------------------- #

def test_enabling_the_unit_without_a_provider_warns():
    """What `install-chunga.json` does today: enable the unit, declare no
    package. `systemctl enable` then fails with the disk already partitioned."""
    codes = {i.code for i in preflight(
        {"systemd": {"enable_units": ["NetworkManager.service"]}, "packages": ["base"]},
        efi_boot=True)}

    assert "unit_without_provider" in codes


def test_the_block_itself_satisfies_that_check():
    cfg = expand_config({"hostname": "arch", "network": {"type": "NetworkManager"},
                         "systemd": {"enable_units": ["NetworkManager.service"]}})

    assert "unit_without_provider" not in {i.code for i in preflight(cfg, efi_boot=True)}
