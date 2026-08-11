from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_cpu


def test_absent_block_contributes_nothing():
    assert expand_cpu({}) == {}


def test_ppd_package_and_unit():
    out = expand_cpu({"cpu": {"scaling_driver": "amd_pstate"}})
    assert out["packages"] == ["power-profiles-daemon"]
    assert out["units"] == ["power-profiles-daemon.service"]
    assert "files" not in out


def test_ppd_can_be_turned_off():
    out = expand_cpu({"cpu": {"power_profiles_daemon": False}})
    assert out == {}


def test_governor_pulls_cpupower_and_writes_its_default_file():
    out = expand_cpu({"cpu": {"power_profiles_daemon": False, "governor": "performance"}})
    assert out["packages"] == ["cpupower"]
    assert out["units"] == ["cpupower.service"]
    assert out["files"] == [{
        "path": "/etc/default/cpupower",
        "content": '# Managed by dasik\ngovernor="performance"\n',
    }]


def test_expand_config_merges_the_contribution():
    merged = expand_config({"cpu": {"scaling_driver": "auto"}, "packages": ["base"]})
    assert "power-profiles-daemon" in merged["packages"]
    assert "power-profiles-daemon.service" in merged["systemd"]["enable_units"]
