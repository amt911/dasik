from unittest.mock import patch

from dasik.lib.actions.pacman_action import PacmanAction


_CONF_COMMENTED = """#ParallelDownloads = 5
#Color
#VerbosePkgLists
#[multilib]
#Include = /etc/pacman.d/mirrorlist
"""

_CONF_ACTIVE = """ParallelDownloads = 5
Color
VerbosePkgLists
[multilib]
Include = /etc/pacman.d/mirrorlist
"""


def _action(cfg, text):
    a = PacmanAction(cfg)
    return a, patch.object(PacmanAction, "_read_conf", return_value=text)


def test_needed_when_options_commented():
    a, p = _action({"options": {"Parallel": True, "Color": True}}, _CONF_COMMENTED)
    with p:
        assert a.is_needed() is True


def test_not_needed_when_options_active():
    a, p = _action(
        {"options": {"Parallel": True, "Color": True, "VerbosePkgLists": True}, "multilib": True},
        _CONF_ACTIVE,
    )
    with p:
        assert a.is_needed() is False
        assert a.verify() is True


def test_needed_when_multilib_requested_but_commented():
    a, p = _action({"options": {}, "multilib": True}, _CONF_COMMENTED)
    with p:
        assert a.is_needed() is True


def test_missing_conf_file_means_needed():
    a = PacmanAction({"options": {"Color": True}})
    with patch.object(PacmanAction, "_read_conf", side_effect=FileNotFoundError):
        assert a.is_needed() is True
        assert a.verify() is False


def test_multilib_active_detects_uncommented_block():
    a = PacmanAction({})
    assert a._multilib_active(_CONF_ACTIVE) is True
    assert a._multilib_active(_CONF_COMMENTED) is False


def test_option_active_regex():
    a = PacmanAction({})
    assert a._option_active("Color\n", "Color") is True
    assert a._option_active("#Color\n", "Color") is False


def test_name_and_optional():
    a = PacmanAction({})
    assert a.name == "Pacman Configuration"
    assert a.is_optional is True
