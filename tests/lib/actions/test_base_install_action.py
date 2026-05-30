from unittest.mock import mock_open, patch

import pytest

from dasik.lib.actions.base_install_action import BaseInstallAction


def test_base_packages_without_microcode():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.packages == ["base", "linux", "linux-firmware"]


def test_adds_amd_ucode_on_amd():
    with patch("builtins.open", mock_open(read_data="vendor_id : AuthenticAMD")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "amd-ucode" in a.packages


def test_adds_intel_ucode_on_intel():
    with patch("builtins.open", mock_open(read_data="vendor_id : GenuineIntel")):
        a = BaseInstallAction({"enable_microcode": True})
    assert "intel-ucode" in a.packages


def test_unknown_vendor_exits():
    with patch("builtins.open", mock_open(read_data="vendor_id : Cyrix")):
        with pytest.raises(SystemExit):
            BaseInstallAction({"enable_microcode": True})


def test_is_needed_true_when_target_empty():
    a = BaseInstallAction({"enable_microcode": False})
    with patch("dasik.lib.actions.base_install_action.os.path.exists", return_value=False):
        assert a.is_needed() is True


def test_is_needed_false_when_base_present():
    a = BaseInstallAction({"enable_microcode": False})
    with patch("dasik.lib.actions.base_install_action.os.path.exists", return_value=True):
        assert a.is_needed() is False
        assert a.verify() is True


def test_name():
    assert BaseInstallAction({"enable_microcode": False}).name == "Base Installation"
