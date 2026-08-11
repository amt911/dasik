import pytest
from pydantic import ValidationError

from dasik.lib.models.cpu_model import CpuModel
from dasik.lib.models.json_model import JsonModel


def test_defaults_are_auto_active_with_ppd():
    m = CpuModel()
    assert m.scaling_driver == "auto"
    assert m.mode == "active"
    assert m.power_profiles_daemon is True
    assert m.governor is None


def test_amd_pstate_accepts_guided():
    assert CpuModel(scaling_driver="amd_pstate", mode="guided").mode == "guided"


def test_intel_pstate_rejects_guided():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="intel_pstate", mode="guided")


def test_amd_pstate_rejects_disable_mode():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="amd_pstate", mode="disable")


def test_unknown_driver_is_rejected():
    with pytest.raises(ValidationError):
        CpuModel(scaling_driver="pstate9000")


def test_governor_must_be_a_plain_identifier():
    assert CpuModel(governor="performance").governor == "performance"
    with pytest.raises(ValidationError):
        CpuModel(governor="performance; rm -rf /")


def test_json_model_accepts_cpu_and_sysrq():
    cfg = JsonModel(**{"cpu": {"scaling_driver": "amd_pstate"}, "sysrq": True})
    assert cfg.cpu is not None and cfg.cpu.scaling_driver == "amd_pstate"
    assert cfg.sysrq is True


def test_json_model_cpu_defaults_to_none_and_sysrq_false():
    cfg = JsonModel()
    assert cfg.cpu is None
    assert cfg.sysrq is False
