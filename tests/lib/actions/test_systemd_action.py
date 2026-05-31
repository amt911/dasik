from unittest.mock import MagicMock, patch

from dasik.lib.actions.systemd_action import SystemdAction


def _enabled_map(enabled):
    """subprocess.run side-effect: 'enabled' for units in *enabled*, else 'disabled'."""
    def side(cmd, **kw):
        unit = cmd[-1]
        state = b"enabled\n" if unit in enabled else b"disabled\n"
        return MagicMock(stdout=state, returncode=0)
    return side


def test_not_needed_when_all_units_enabled():
    a = SystemdAction({"enable_units": ["sshd.service"], "enable_sockets": ["cups.socket"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"sshd.service", "cups.socket"})):
        assert a.is_needed() is False
        assert a.verify() is True


def test_needed_when_a_unit_pending():
    a = SystemdAction({"enable_units": ["sshd.service", "fail2ban.service"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"sshd.service"})):
        assert a.is_needed() is True
        assert a.verify() is False


def test_pending_lists_only_disabled_units():
    a = SystemdAction({"enable_units": ["a.service", "b.service"], "enable_sockets": ["c.socket"]})
    with patch("dasik.lib.actions.systemd_action.subprocess.run",
               _enabled_map({"a.service"})):
        assert a._pending() == ["b.service", "c.socket"]


def test_empty_config_is_noop():
    a = SystemdAction({})
    assert a.is_needed() is False
    assert a.name == "Systemd Units"
    assert a.is_optional is True
