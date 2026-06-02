from unittest.mock import mock_open, patch

import pytest

from dasik.lib.actions.base_install_action import BaseInstallAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target
from dasik.lib.state.change import Op


def _ctx(root):
    return ActionContext(target=Target(root=str(root)))


def _marker(tmp_path):
    d = tmp_path / "usr" / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pacman").write_text("")


# --- microcode detection (unchanged behavior) ----------------------------- #

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


# --- v3 contract ---------------------------------------------------------- #

def test_is_v3_true():
    assert BaseInstallAction.is_v3() is True


def test_actual_empty_when_not_installed(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == set()


def test_actual_present_when_marker(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.actual() == {"base"}


def test_plan_install_when_absent(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    changes = a.plan(managed=[])
    assert changes and changes[0].op is Op.INSTALL and changes[0].item == "base"


def test_plan_empty_when_present(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.plan(managed=[]) == []


def test_apply_runs_install_when_changes(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_called_once()


def test_apply_noop_when_no_changes(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    with patch.object(BaseInstallAction, "_install") as inst:
        a.apply(a.plan(managed=[]))
        inst.assert_not_called()


def test_is_needed_and_verify_track_marker(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.is_needed() is True and a.verify() is False
    _marker(tmp_path)
    assert a.is_needed() is False and a.verify() is True


def test_managed_keys(tmp_path):
    _marker(tmp_path)
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.managed_keys() == {"base": ["base"]}


def test_import_state_empty(tmp_path):
    a = BaseInstallAction({"enable_microcode": False}, _ctx(tmp_path))
    assert a.import_state(managed=[]) == {}


def test_name_and_optional():
    a = BaseInstallAction({"enable_microcode": False})
    assert a.name == "Base Installation"
    assert a.is_optional is False
