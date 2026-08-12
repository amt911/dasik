"""The `firewall` block gained a backend, and the two are not interchangeable.

firewalld has named services and zones; ufw has an ordered rule list and app
profiles. Declaring a firewalld-only field under ufw (or the reverse) is a
validation error rather than a silent drop — an access rule that cannot be
represented must fail closed, the same rule `_rich_rule_to_xml` already follows.
"""
import pytest
from pydantic import ValidationError

from dasik.lib.models.firewall_model import FirewallModel


def test_the_default_backend_is_firewalld():
    """Every config written before this existed keeps its meaning."""
    assert FirewallModel().backend == "firewalld"


def test_ufw_is_accepted():
    assert FirewallModel(enable=True, backend="ufw").backend == "ufw"


def test_an_unknown_backend_is_rejected():
    with pytest.raises(ValidationError):
        FirewallModel(backend="iptables")


def test_rich_rules_are_firewalld_only():
    with pytest.raises(ValidationError, match="rich_rules"):
        FirewallModel(enable=True, backend="ufw",
                      rich_rules=['rule service name="ssh" accept'])


def test_remove_services_is_firewalld_only():
    """ufw denies incoming by default — there is nothing to remove."""
    with pytest.raises(ValidationError, match="remove_services"):
        FirewallModel(enable=True, backend="ufw", remove_services=["ssh"])


def test_rules_are_ufw_only():
    with pytest.raises(ValidationError, match="rules"):
        FirewallModel(enable=True, backend="firewalld", rules=["allow 22/tcp"])


@pytest.mark.parametrize("rule", [
    "allow 22/tcp", "deny 80/tcp", "limit 22/tcp", "reject 443/tcp",
    "allow 6000:6007/udp", "allow Syncthing",
])
def test_valid_ufw_rules(rule):
    assert FirewallModel(enable=True, backend="ufw", rules=[rule]).rules == [rule]


@pytest.mark.parametrize("rule", [
    "allow ssh",              # ufw reports it as 22/tcp; dasik could never match
    "22/tcp",                 # no action
    "allow",                  # no target
    "allow 22/tcp extra",     # trailing junk
    "shout 22/tcp",           # not an action
])
def test_rejected_ufw_rules(rule):
    with pytest.raises(ValidationError):
        FirewallModel(enable=True, backend="ufw", rules=[rule])
