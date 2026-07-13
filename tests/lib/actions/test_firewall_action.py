"""FirewallAction — declarative firewalld public.xml, idempotent.

Owns /etc/firewalld/zones/public.xml (services = defaults − remove + allowed,
plus rich rules). Idempotent by content compare — fixes the firewalld
default-service quirk where `--remove-service=ssh` re-fired every apply. Verified
with tests (pure file generation), no firewalld/QEMU needed.
"""
from types import SimpleNamespace

from dasik.lib.actions.firewall_action import FirewallAction, _rich_rule_to_xml
from dasik.lib.state.change import Op


def _fw(current=None, **cfg):
    cfg.setdefault("enable", True)
    a = FirewallAction(cfg, context=SimpleNamespace(target=object()))
    a._current_xml = lambda: current
    return a


# --- rich-rule converter -------------------------------------------------- #

def test_rich_rule_source_accept():
    xml = _rich_rule_to_xml('rule family=ipv4 source address=192.168.1.0/24 accept')
    assert xml == '<rule family="ipv4"><source address="192.168.1.0/24"/><accept/></rule>'


def test_rich_rule_quoted_and_port():
    xml = _rich_rule_to_xml('rule family="ipv6" port port="443" protocol="tcp" reject')
    assert xml == '<rule family="ipv6"><port port="443" protocol="tcp"/><reject/></rule>'


def test_rich_rule_service_drop():
    xml = _rich_rule_to_xml('rule service name=ssh drop')
    assert xml == '<rule><service name="ssh"/><drop/></rule>'


# --- zone XML + idempotent plan ------------------------------------------- #

def test_removed_default_service_absent_allowed_present():
    a = _fw(allowed_services=["syncthing"], remove_services=["ssh"])
    xml = a._desired_xml()
    assert '<service name="ssh"/>' not in xml           # removed default gone
    assert '<service name="dhcpv6-client"/>' in xml     # other default kept
    assert '<service name="syncthing"/>' in xml         # allowed present


def test_converged_zone_is_a_noop():
    a = _fw(allowed_services=["syncthing"], remove_services=["ssh"])
    a._current_xml = lambda: a._desired_xml()            # file already matches
    assert a.plan([]) == []
    assert a.is_needed() is False


def test_divergent_zone_plans_one_modify():
    a = _fw(current="<zone></zone>", allowed_services=["syncthing"])
    changes = a.plan([])
    assert [(c.op, c.item) for c in changes] == [(Op.MODIFY, "public")]


def test_remove_service_is_idempotent_across_applies():
    """The bug the megamix caught: after applying, a re-plan must be empty (the
    removed service stays absent from the written file)."""
    a = _fw(remove_services=["ssh"])
    written = a._desired_xml()
    a._current_xml = lambda: written                     # simulate post-apply state
    assert a.plan([]) == []                              # no re-fire
    assert '<service name="ssh"/>' not in written


def test_disabled_plans_nothing():
    assert FirewallAction({"enable": False}, context=SimpleNamespace(target=object())).plan([]) == []


def test_apply_writes_zone_file(tmp_path):
    a = FirewallAction({"enable": True, "allowed_services": ["syncthing"]},
                       context=SimpleNamespace(target=None))
    a._zone_file = lambda: str(tmp_path / "public.xml")
    a.apply(a.plan([]))
    written = (tmp_path / "public.xml").read_text()
    assert '<service name="syncthing"/>' in written
    # second apply is a no-op (content already matches)
    a._current_xml = lambda: written
    assert a.plan([]) == []
