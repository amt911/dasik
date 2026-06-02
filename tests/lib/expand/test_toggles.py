from dasik.lib.expand.toggles import (
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
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
