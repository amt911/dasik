from unittest.mock import MagicMock, patch

from dasik.lib.actions.bluetooth_action import BluetoothAction


def test_disabled_is_never_needed():
    assert BluetoothAction({"enable": False}).is_needed() is False


def test_needed_when_pkg_missing():
    a = BluetoothAction({"enable": True})
    # _pkg_installed -> returncode != 0 (missing); _service_enabled not reached fully
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.bluetooth_action.subprocess.run", fake):
        assert a.is_needed() is True


def test_needed_when_service_not_enabled():
    a = BluetoothAction({"enable": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)  # installed
        return MagicMock(stdout=b"disabled\n", returncode=0)  # not enabled

    with patch("dasik.lib.actions.bluetooth_action.subprocess.run", side):
        assert a.is_needed() is True


def test_not_needed_when_installed_and_enabled():
    a = BluetoothAction({"enable": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.bluetooth_action.subprocess.run", side):
        assert a.is_needed() is False


def test_verify_true_only_when_installed_and_enabled():
    a = BluetoothAction({"enable": True})

    def side(cmd, **kw):
        if "pacman" in cmd:
            return MagicMock(stdout=b"", returncode=0)
        return MagicMock(stdout=b"enabled\n", returncode=0)

    with patch("dasik.lib.actions.bluetooth_action.subprocess.run", side):
        assert a.verify() is True


def test_custom_package_name():
    a = BluetoothAction({"enable": True, "package": "bluez-git"})
    assert a.package == "bluez-git"
    assert a.name == "Bluetooth"
    assert a.is_optional is True
