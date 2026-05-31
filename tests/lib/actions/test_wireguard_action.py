from unittest.mock import MagicMock, patch

from dasik.lib.actions.wireguard_action import WireguardAction, _sha256


def test_sha256_is_deterministic():
    assert _sha256("hello") == _sha256("hello")
    assert _sha256("a") != _sha256("b")


def test_disabled_is_never_needed():
    assert WireguardAction({"enable": False}).is_needed() is False


def test_conf_path_and_service_name_use_interface():
    a = WireguardAction({"enable": True, "interface_name": "wg1"})
    assert a._conf_path() == "/mnt/etc/wireguard/wg1.conf"
    assert a._service_name() == "wg-quick@wg1.service"


def test_needed_when_pkg_missing():
    a = WireguardAction({"enable": True})
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.wireguard_action.subprocess.run", fake):
        assert a.is_needed() is True


def test_needed_when_config_differs():
    a = WireguardAction({"enable": True, "config_content": "[Interface]"})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)  # installed
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.wireguard_action.subprocess.run", side), \
         patch("dasik.lib.actions.wireguard_action.os.path.exists", return_value=False):
        assert a.is_needed() is True  # config_matches False (file absent)


def test_config_matches_true_when_file_equals_desired():
    content = "[Interface]\nAddress = 10.0.0.1"
    a = WireguardAction({"enable": True, "config_content": content})
    from unittest.mock import mock_open
    with patch("dasik.lib.actions.wireguard_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=content + "\n")):
        assert a._config_matches() is True


def test_not_needed_when_pkg_config_and_service_ok():
    content = "[Interface]"
    a = WireguardAction({"enable": True, "config_content": content})
    from unittest.mock import mock_open

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.wireguard_action.subprocess.run", side), \
         patch("dasik.lib.actions.wireguard_action.os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=content + "\n")):
        assert a.is_needed() is False
        assert a.verify() is True


def test_name_and_optional():
    a = WireguardAction({"enable": True})
    assert a.name == "WireGuard"
    assert a.is_optional is True
