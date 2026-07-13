from dasik.lib.expand.toggles import (
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
    expand_wireguard, expand_firewall, expand_hwaccel,
)


def test_bluetooth_disabled_empty():
    assert expand_bluetooth({}) == {}
    assert expand_bluetooth({"bluetooth": {"enable": False}}) == {}


def test_bluetooth_enabled():
    out = expand_bluetooth({"bluetooth": {"enable": True, "package": "bluez"}})
    assert out["packages"] == ["bluez", "bluez-utils"]
    assert out["units"] == ["bluetooth.service"]


def test_cups_disabled_empty():
    assert expand_cups({}) == {}
    assert expand_cups({"cups": {"install": False}}) == {}


def test_cups_enabled():
    out = expand_cups({"cups": {"install": True}})
    assert "cups" in out["packages"] and "sane" in out["packages"]
    assert out["sockets"] == ["cups.socket"]


def test_trim_disabled_empty():
    assert expand_trim({}) == {}
    assert expand_trim({"enable_trim": False}) == {}


def test_trim_enabled():
    assert expand_trim({"enable_trim": True}) == {"units": ["fstrim.timer"]}


def test_kvm_disabled_empty():
    assert expand_kvm({}) == {}
    assert expand_kvm({"kvm": {"install": False}}) == {}


def test_kvm_enabled():
    out = expand_kvm({"kvm": {"install": True}})
    assert "qemu-full" in out["packages"] and "libvirt" in out["packages"]
    assert out["units"] == ["libvirtd.service", "virtlogd.service"]
    assert out["modprobe_conf"][0]["name"] == "dasik-nested-virt.conf"
    assert "nested=1" in out["modprobe_conf"][0]["content"]


def test_wireguard_disabled_empty():
    assert expand_wireguard({}) == {}
    assert expand_wireguard({"wireguard": {"enable": False}}) == {}


def test_wireguard_enabled():
    out = expand_wireguard({"wireguard": {
        "enable": True, "interface_name": "wg0", "config_content": "[Interface]\n",
    }})
    assert out["packages"] == ["wireguard-tools"]
    assert out["units"] == ["wg-quick@wg0.service"]
    assert out["files"][0]["path"] == "/etc/wireguard/wg0.conf"
    assert out["files"][0]["content"] == "[Interface]\n"


def test_firewall_disabled_empty():
    assert expand_firewall({}) == {}
    assert expand_firewall({"firewall": {"enable": False}}) == {}


def test_firewall_enabled():
    out = expand_firewall({"firewall": {"enable": True}})
    assert out["packages"] == ["firewalld"]
    assert out["units"] == ["firewalld.service"]


def test_hwaccel_disabled_empty():
    assert expand_hwaccel({}) == {}
    assert expand_hwaccel({"hardware_acceleration": {"enable": False}}) == {}


def test_hwaccel_enabled_uses_drivers():
    out = expand_hwaccel({
        "hardware_acceleration": {"enable": True, "install_codecs": True},
        "drivers": ["intel", "amd"],
    })
    assert "intel-media-driver" in out["packages"]
    assert "libva-mesa-driver" in out["packages"]
    assert "libva-utils" in out["packages"]  # common


def test_hwaccel_enabled_no_drivers_only_common():
    out = expand_hwaccel({"hardware_acceleration": {"enable": True}, "drivers": []})
    assert out["packages"] == ["libva-utils", "vdpauinfo"]


def test_hwaccel_amd_does_not_pull_removed_mesa_vdpau():
    # mesa-vdpau was removed from the Arch repos (radeonsi VDPAU now ships in
    # `mesa`). Emitting it made `pacman -S` abort with "target not found",
    # breaking an AMD hwaccel install. libva-mesa-driver (VA-API) stays.
    out = expand_hwaccel({
        "hardware_acceleration": {"enable": True},
        "drivers": ["amd"],
    })
    assert "mesa-vdpau" not in out["packages"]
    assert "libva-mesa-driver" in out["packages"]
