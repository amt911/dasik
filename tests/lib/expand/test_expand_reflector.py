from dasik.lib.expand import expand_config
from dasik.lib.expand.toggles import expand_reflector


def test_absent_block_contributes_nothing():
    assert expand_reflector({}) == {}


def test_package_timer_and_conf():
    out = expand_reflector({"reflector": {"countries": ["ES", "France"],
                                          "protocols": ["https"],
                                          "latest": 10, "sort": "rate"}})
    assert out["packages"] == ["reflector"]
    assert out["units"] == ["reflector.timer"]
    conf = out["files"][0]
    assert conf["path"] == "/etc/xdg/reflector/reflector.conf"
    assert conf["content"] == (
        "# Managed by dasik\n"
        "--country ES\n"
        "--country France\n"
        "--protocol https\n"
        "--latest 10\n"
        "--sort rate\n"
        "--save /etc/pacman.d/mirrorlist\n")


def test_defaults_when_only_countries_are_given():
    out = expand_reflector({"reflector": {"countries": ["ES"]}})
    content = out["files"][0]["content"]
    assert "--protocol https\n" in content
    assert "--latest 20\n" in content
    assert "--sort rate\n" in content


def test_expand_config_merges_package_unit_and_file():
    merged = expand_config({"reflector": {"countries": ["ES"]}})
    assert "reflector" in merged["packages"]
    assert "reflector.timer" in merged["systemd"]["enable_units"]
    assert any(f["path"] == "/etc/xdg/reflector/reflector.conf" for f in merged["files"])
