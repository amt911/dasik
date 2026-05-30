from unittest.mock import MagicMock, patch

from dasik.lib.actions.firewall_action import FirewallAction


def test_disabled_is_never_needed():
    assert FirewallAction({"enable": False}).is_needed() is False


def test_needed_when_pkg_missing():
    a = FirewallAction({"enable": True})
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.firewall_action.subprocess.run", fake):
        assert a.is_needed() is True


def test_needed_when_service_disabled():
    a = FirewallAction({"enable": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)  # installed
        return MagicMock(stdout=b"disabled\n", returncode=0)

    with patch("dasik.lib.actions.firewall_action.subprocess.run", side):
        assert a.is_needed() is True


def _installed_enabled_with(services, rich_rules):
    """pacman installed, service enabled, offline-cmd returns given lists."""
    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        if "is-enabled" in cmd:
            return MagicMock(stdout=b"enabled\n", returncode=0)
        if "--list-services" in cmd:
            return MagicMock(stdout=(" ".join(services)).encode() + b"\n", returncode=0)
        if "--list-rich-rules" in cmd:
            return MagicMock(stdout=("\n".join(rich_rules)).encode() + b"\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)
    return side


def test_needed_when_service_to_remove_still_active():
    a = FirewallAction({"enable": True, "remove_services": ["ssh"]})
    with patch("dasik.lib.actions.firewall_action.subprocess.run",
               _installed_enabled_with(["ssh", "dhcpv6-client"], [])):
        assert a.is_needed() is True


def test_needed_when_allowed_service_missing():
    a = FirewallAction({"enable": True, "allowed_services": ["http"]})
    with patch("dasik.lib.actions.firewall_action.subprocess.run",
               _installed_enabled_with(["dhcpv6-client"], [])):
        assert a.is_needed() is True


def test_needed_when_rich_rule_missing():
    rule = 'rule family="ipv4" port port="22" protocol="tcp" accept'
    a = FirewallAction({"enable": True, "rich_rules": [rule]})
    with patch("dasik.lib.actions.firewall_action.subprocess.run",
               _installed_enabled_with([], [])):
        assert a.is_needed() is True


def test_not_needed_when_fully_converged():
    rule = 'rule x accept'
    a = FirewallAction({
        "enable": True,
        "remove_services": ["ssh"],
        "allowed_services": ["http"],
        "rich_rules": [rule],
    })
    with patch("dasik.lib.actions.firewall_action.subprocess.run",
               _installed_enabled_with(["http"], [rule])):
        assert a.is_needed() is False
        assert a.verify() is True


def test_offline_cmd_failure_returns_empty_lists():
    a = FirewallAction({"enable": True})
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.firewall_action.subprocess.run", fake):
        assert a._get_active_services() == []
        assert a._get_rich_rules() == []
    assert a.name == "Firewall (firewalld)"
    assert a.is_optional is True
