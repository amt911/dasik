from dasik.lib.expand import expand_config, contributions


def test_contributions_aggregates_and_dedups():
    cfg = {"bluetooth": {"enable": True}, "cups": {"install": True}}
    c = contributions(cfg)
    assert "bluez" in c["packages"] and "cups" in c["packages"]
    assert "bluetooth.service" in c["units"]
    assert "cups.socket" in c["sockets"]


def test_expand_merges_into_packages_and_systemd():
    cfg = {
        "packages": ["firefox"],
        "systemd": {"enable_units": ["NetworkManager.service"]},
        "bluetooth": {"enable": True},
        "enable_trim": True,
    }
    out = expand_config(cfg)
    assert out["packages"] == ["firefox", "bluez", "bluez-utils"]
    assert "NetworkManager.service" in out["systemd"]["enable_units"]
    assert "bluetooth.service" in out["systemd"]["enable_units"]
    assert "fstrim.timer" in out["systemd"]["enable_units"]


def test_expand_merges_modprobe_conf_for_kvm():
    out = expand_config({"kvm": {"install": True}})
    names = [e["name"] for e in out["modprobe_conf"]]
    assert "dasik-nested-virt.conf" in names


def test_expand_does_not_mutate_input():
    cfg = {"packages": ["firefox"], "bluetooth": {"enable": True}}
    expand_config(cfg)
    assert cfg["packages"] == ["firefox"]  # original untouched


def test_expand_noop_when_no_toggles():
    cfg = {"packages": ["firefox"]}
    assert expand_config(cfg) == cfg
