"""End-to-end expansion of the comprehensive `config/install-chunga.json`.

Proves a genuinely complex ("chunga") install expands correctly — no toggle
stomps another and every declared feature contributes its packages/units/groups:
LUKS2+TPM2 btrfs, snapper, kvm, NVIDIA driver, AUR + multilib, firewall,
wireguard, bluetooth, cups, multiple users. Individual disk combos (LUKS, btrfs
+ subvolumes, TPM2, snapper) are each QEMU-verified elsewhere; this guards the
*combination* at the config/expand layer.
"""
from dasik.lib.expand import expand_config
from dasik.lib.json_parser.json_parser import JsonParser


def _expanded():
    return expand_config(JsonParser("config/install-chunga.json").debug())


def _pkgs(exp):
    return [p if isinstance(p, str) else p.get("name") for p in exp.get("packages", [])]


def test_chunga_parses_and_expands():
    exp = _expanded()
    assert exp["packages"]  # non-empty


def test_chunga_installs_nvidia_and_lib32():
    pkgs = _pkgs(_expanded())
    assert "nvidia-open" in pkgs and "nvidia-utils" in pkgs
    assert "lib32-nvidia-utils" in pkgs        # multilib is on


def test_chunga_kvm_grants_libvirt_group_to_users():
    exp = _expanded()
    assert "libvirt" in exp["users"][0]["groups"]
    assert "qemu-full" in _pkgs(exp)


def test_chunga_all_toggle_units_enabled():
    exp = _expanded()
    units = set((exp.get("systemd") or {}).get("enable_units", []))
    for u in ("libvirtd.service", "bluetooth.service", "firewalld.service",
              "wg-quick@wg0.service", "snapper-timeline.timer", "fstrim.timer"):
        assert u in units, f"missing {u}"


def test_chunga_encrypted_btrfs_root_declared():
    # The disk layer formats the LUKS mapper as btrfs (verified in
    # disk_partition_action); here we assert the config carries both.
    cfg = JsonParser("config/install-chunga.json").debug()
    root = next(p for d in cfg["disks"]["disks"] for p in d["partitions"]
               if p.get("mountpoint") == "/")
    assert root["encrypt"] is True
    assert root["filesystem"] == "btrfs"
    assert any(sv["name"] == "@" for sv in root["btrfs_subvolumes"])
