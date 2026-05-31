from pathlib import PosixPath
from unittest.mock import MagicMock, patch

from dasik.lib.actions.timezone_action import TimezoneAction


def _link(exists=True, is_symlink=True, target="/usr/share/zoneinfo/Europe/Madrid"):
    m = MagicMock()
    m.exists.return_value = exists
    m.is_symlink.return_value = is_symlink
    m.readlink.return_value = PosixPath(target)
    return m


def _cfg():
    return {"region": "Europe", "city": "Madrid"}


def test_needed_when_link_absent():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link(exists=False)):
        assert a.is_needed() is True


def test_needed_when_not_a_symlink():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a.is_needed() is True


def test_not_needed_when_link_matches():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.is_needed() is False


def test_needed_when_link_points_elsewhere():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/America/New_York")):
        assert a.is_needed() is True


def test_verify_true_only_on_exact_match():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path", return_value=_link()):
        assert a.verify() is True
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo/Asia/Tokyo")):
        assert a.verify() is False


def test_needed_when_link_target_too_short():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(target="/usr/share/zoneinfo")):
        assert a.is_needed() is True


def test_needed_when_readlink_raises():
    a = TimezoneAction(_cfg())
    link = _link()
    link.readlink.side_effect = OSError("nope")
    with patch("dasik.lib.actions.timezone_action.Path", return_value=link):
        assert a.is_needed() is True


def test_verify_false_when_not_symlink():
    a = TimezoneAction(_cfg())
    with patch("dasik.lib.actions.timezone_action.Path",
               return_value=_link(is_symlink=False)):
        assert a.verify() is False


def test_verify_false_when_readlink_raises():
    a = TimezoneAction(_cfg())
    link = _link()
    link.readlink.side_effect = OSError("nope")
    with patch("dasik.lib.actions.timezone_action.Path", return_value=link):
        assert a.verify() is False


def test_name():
    assert TimezoneAction(_cfg()).name == "Timezone Configuration"
