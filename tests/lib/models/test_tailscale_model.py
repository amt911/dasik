"""TailscaleModel: the config boundary in front of an undocumented schema.

tailscaled rejects a malformed conffile by refusing to start, so a bad value is
a machine with no VPN and a daemon in a restart loop. Catching it here makes it a
config error instead.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.json_model import JsonModel
from dasik.lib.models.tailscale_model import TailscaleModel


def test_empty_block_is_valid():
    """Declaring nothing is meaningful: it is how the block is switched off
    without being removed."""
    assert TailscaleModel().accept_routes is None


def test_unset_is_not_the_same_as_false():
    """None means 'leave it to tailscale'; False means 'dasik owns this and it is
    off', which also locks the CLI out of it."""
    assert TailscaleModel().accept_routes is None
    assert TailscaleModel(accept_routes=False).accept_routes is False


def test_the_block_is_optional_on_the_root_model():
    assert JsonModel.model_validate({"hostname": "box"}).tailscale is None


def test_accepted_through_the_root_model():
    cfg = JsonModel.model_validate({"tailscale": {"accept_routes": True}})
    assert cfg.tailscale is not None and cfg.tailscale.accept_routes is True


def test_no_auth_key_field():
    """A tailnet credential must not be declarable in a config `dasik save`
    commits to Git."""
    assert "auth_key" not in TailscaleModel.model_fields
    with pytest.raises(ValidationError):
        JsonModel.model_validate(
            {"tailscale": {"accept_routes": True, "auth_key": "tskey-abc"}})


def test_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        JsonModel.model_validate({"tailscale": {"accpet_routes": True}})


# --- values that would make tailscaled refuse the file ------------------- #

@pytest.mark.parametrize("routes", [["10.0.0.0/8"], ["192.168.1.0/24", "::/0"], []])
def test_valid_advertise_routes(routes):
    assert TailscaleModel(advertise_routes=routes).advertise_routes == routes


@pytest.mark.parametrize("bad", ["10.0.0.0", "not-a-network", "10.0.0.0/99",
                                 "10.0.0.0/8 10.1.0.0/16"])
def test_invalid_advertise_routes_refused(bad):
    with pytest.raises(ValidationError):
        TailscaleModel(advertise_routes=[bad])


@pytest.mark.parametrize("name", ["box", "archlinux-p14s", "a", "a1-b2"])
def test_valid_hostnames(name):
    assert TailscaleModel(hostname=name).hostname == name


@pytest.mark.parametrize("bad", ["-leading", "trailing-", "has space",
                                 "under_score", "a" * 64, ""])
def test_invalid_hostnames_refused(bad):
    with pytest.raises(ValidationError):
        TailscaleModel(hostname=bad)


@pytest.mark.parametrize("user", ["andres", "_svc", "a-b"])
def test_valid_operators(user):
    assert TailscaleModel(operator=user).operator == user


@pytest.mark.parametrize("bad", ["Andres", "1st", "has space", ""])
def test_invalid_operators_refused(bad):
    with pytest.raises(ValidationError):
        TailscaleModel(operator=bad)


@pytest.mark.parametrize("node", ["100.64.0.1", "auto:any", "my-exit"])
def test_valid_exit_nodes(node):
    assert TailscaleModel(exit_node=node).exit_node == node


@pytest.mark.parametrize("bad", ["two tokens", "  ", ""])
def test_invalid_exit_nodes_refused(bad):
    with pytest.raises(ValidationError):
        TailscaleModel(exit_node=bad)


def test_netfilter_mode_is_constrained_to_what_tailscale_accepts():
    assert TailscaleModel(netfilter_mode="nodivert").netfilter_mode == "nodivert"
    with pytest.raises(ValidationError):
        TailscaleModel(netfilter_mode="sometimes")


def test_server_url_must_be_a_url():
    assert TailscaleModel(server_url="https://hs.example.net").server_url
    with pytest.raises(ValidationError):
        TailscaleModel(server_url="hs.example.net")
