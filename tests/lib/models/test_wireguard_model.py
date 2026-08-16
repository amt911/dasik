"""The tunnel model: a name, and the file that defines it.

The guards here are the ones that fail LATE and badly if they are not caught in
the schema: a name over IFNAMSIZ fails at `ip link add`, after the config was
written; an absolute or `..` source lets a config pull a file from outside its
own directory, and a tunnel file holds a private key.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.wireguard_model import WireguardTunnel
from dasik.lib.models.json_model import JsonModel


def test_minimal_tunnel_defaults_to_auto_and_enabled():
    t = WireguardTunnel(name="eu-mad", source="wg/eu-mad.conf")
    assert t.backend == "auto" and t.enable is True and t.content is None


def test_name_over_ifnamsiz_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="a" * 16, source="wg/x.conf")


def test_name_at_ifnamsiz_is_accepted():
    assert WireguardTunnel(name="a" * 15, source="wg/x.conf").name == "a" * 15


def test_name_with_a_slash_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu/mad", source="wg/x.conf")


def test_empty_name_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="", source="wg/x.conf")


def test_absolute_source_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="/etc/wireguard/wg0.conf")


def test_parent_traversal_in_source_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="../secrets/wg0.conf")


def test_a_source_named_dotdot_deeper_in_is_also_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="wg/../../secrets/wg0.conf")


def test_unknown_backend_is_rejected():
    with pytest.raises(ValidationError):
        WireguardTunnel(name="eu-mad", source="wg/x.conf", backend="netctl")


def test_json_model_takes_a_list_of_tunnels():
    m = JsonModel(hostname="box",
                  wireguard=[{"name": "eu-mad", "source": "wg/eu-mad.conf"}])
    assert m.wireguard[0].name == "eu-mad"


def test_the_old_inline_shape_is_refused():
    # It never worked as a dict of (enable, interface_name, config_content) —
    # it wrote the key world-readable and captured it twice. A silent
    # re-interpretation of a block holding a private key is worse than an error.
    with pytest.raises(ValidationError):
        JsonModel(hostname="box",
                  wireguard={"enable": True, "interface_name": "wg0",
                             "config_content": "[Interface]\n"})
