from unittest.mock import MagicMock, patch

from dasik.lib.actions.cups_action import CupsAction


def test_disabled_is_never_needed():
    assert CupsAction({"install": False}).is_needed() is False


def test_needed_when_packages_missing():
    a = CupsAction({"install": True})
    # every pacman -Qi returns missing
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.cups_action.subprocess.run", fake):
        assert a.is_needed() is True
        assert len(a._missing_pkgs()) == 5


def test_needed_when_socket_disabled_but_pkgs_present():
    a = CupsAction({"install": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)  # installed
        return MagicMock(stdout=b"disabled\n", returncode=0)

    with patch("dasik.lib.actions.cups_action.subprocess.run", side):
        assert a._missing_pkgs() == []
        assert a.is_needed() is True


def test_not_needed_when_all_present_and_socket_enabled():
    a = CupsAction({"install": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.cups_action.subprocess.run", side):
        assert a.is_needed() is False
        assert a.verify() is True


def test_name_and_optional():
    a = CupsAction({"install": True})
    assert a.name == "CUPS / Scanning"
    assert a.is_optional is True
