from unittest.mock import MagicMock, patch

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.hw_accel_action import HardwareAccelAction


def _ctx_with_drivers(drivers):
    ctx = ActionContext()
    ctx.set("drivers", drivers)
    return ctx


def test_disabled_is_never_needed():
    a = HardwareAccelAction({"enable": False}, _ctx_with_drivers(["intel"]))
    assert a.is_needed() is False


def test_install_codecs_false_is_never_needed():
    a = HardwareAccelAction({"enable": True, "install_codecs": False}, _ctx_with_drivers(["intel"]))
    assert a.is_needed() is False


def test_desired_pkgs_maps_drivers_and_dedups():
    a = HardwareAccelAction({"enable": True}, _ctx_with_drivers(["intel", "amd"]))
    pkgs = a._desired_pkgs()
    assert "intel-media-driver" in pkgs
    assert "libva-mesa-driver" in pkgs
    # libva-utils appears in both maps but must be deduped
    assert pkgs.count("libva-utils") == 1


def test_unknown_driver_contributes_no_pkgs():
    a = HardwareAccelAction({"enable": True}, _ctx_with_drivers(["s3virge"]))
    assert a._desired_pkgs() == []


def test_needed_when_pkg_missing():
    a = HardwareAccelAction({"enable": True}, _ctx_with_drivers(["nvidia"]))
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=1))
    with patch("dasik.lib.actions.hw_accel_action.subprocess.run", fake):
        assert a.is_needed() is True


def test_not_needed_when_all_present():
    a = HardwareAccelAction({"enable": True}, _ctx_with_drivers(["nvidia"]))
    fake = MagicMock(return_value=MagicMock(stdout=b"", returncode=0))
    with patch("dasik.lib.actions.hw_accel_action.subprocess.run", fake):
        assert a.is_needed() is False
        assert a.verify() is True


def test_no_drivers_means_nothing_to_do():
    a = HardwareAccelAction({"enable": True}, ActionContext())
    assert a._desired_pkgs() == []
    assert a.name == "Hardware Acceleration"
    assert a.is_optional is True
