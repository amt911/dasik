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


def test_kvm_does_not_pull_conflicting_iptables_nft():
    # iptables-nft conflicts with the base `iptables` and cannot be swapped
    # non-interactively, so the toggle must not declare it (it made the install
    # silently fail + the day-2 plan re-try forever). libvirt uses the present
    # iptables/nftables.
    out = expand_kvm({"kvm": {"install": True}})
    assert "iptables-nft" not in out["packages"]


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


# --- initramfs generator: dracut installs dracut + neutralizes mkinitcpio --- #
from dasik.lib.expand.toggles import expand_initramfs


def test_initramfs_default_mkinitcpio_is_noop():
    assert expand_initramfs({}) == {}
    assert expand_initramfs({"initramfs": "mkinitcpio"}) == {}


def test_initramfs_dracut_installs_dracut_and_neutralizes_mkinitcpio():
    out = expand_initramfs({"initramfs": "dracut"})
    assert "dracut" in out["packages"]
    paths = [f["path"] for f in out["files"]]
    assert "/etc/pacman.d/hooks/90-mkinitcpio-install.hook" in paths
    assert "/etc/pacman.d/hooks/60-mkinitcpio-remove.hook" in paths
    # each override is a valid no-op hook (Exec = /bin/true), so mkinitcpio never runs
    for f in out["files"]:
        assert "Exec = /bin/true" in f["content"]


def test_initramfs_dracut_merges_into_config_via_expand():
    from dasik.lib.expand import expand_config
    merged = expand_config({"initramfs": "dracut", "packages": ["base"]})
    assert "dracut" in merged["packages"]
    assert any("mkinitcpio-install.hook" in f["path"] for f in merged["files"])


from dasik.lib.expand.toggles import expand_zram


def test_zram_absent_empty():
    assert expand_zram({}) == {}
    assert expand_zram({"zram": {}}) == {}


def test_zram_present_installs_generator():
    out = expand_zram({"zram": {"zram0": {"zram-size": "ram / 2"}}})
    assert out == {"packages": ["zram-generator"]}


def test_zram_merges_into_config_via_expand():
    from dasik.lib.expand import expand_config
    merged = expand_config({"zram": {"zram0": {"zram-size": "ram / 2"}}, "packages": ["base"]})
    assert "zram-generator" in merged["packages"]
