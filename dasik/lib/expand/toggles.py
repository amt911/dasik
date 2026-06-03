"""Pure expansion functions: one per feature toggle.

Each takes the full config dict and returns a contribution dict with any of:
packages (list[str]), units (list[str]), sockets (list[str]),
modprobe_conf (list[{name, content}]), files (list[{path, content}]).
Returns {} (no contribution) when the toggle is absent or disabled.
"""
from __future__ import annotations
from typing import Any, Dict


def expand_bluetooth(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("bluetooth") or {}
    if not cfg.get("enable"):
        return {}
    pkg = cfg.get("package", "bluez")
    return {"packages": [pkg, "bluez-utils"], "units": ["bluetooth.service"]}


def expand_cups(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("cups") or {}
    if not cfg.get("install"):
        return {}
    return {
        "packages": ["cups", "cups-pdf", "system-config-printer", "sane", "sane-airscan"],
        "sockets": ["cups.socket"],
    }


def expand_trim(config: Dict[str, Any]) -> Dict[str, Any]:
    if not config.get("enable_trim"):
        return {}
    return {"units": ["fstrim.timer"]}


_KVM_PKGS = [
    "qemu-full", "qemu-block-gluster", "qemu-block-iscsi", "samba",
    "qemu-guest-agent", "qemu-user-static",
    "edk2-ovmf", "swtpm", "virt-firmware",
    "libvirt", "virt-manager",
    "iptables-nft", "dnsmasq", "openbsd-netcat", "dmidecode",
]


def expand_kvm(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("kvm") or {}
    if not cfg.get("install"):
        return {}
    return {
        "packages": list(_KVM_PKGS),
        "units": ["libvirtd.service", "virtlogd.service"],
        "modprobe_conf": [{
            "name": "dasik-nested-virt.conf",
            "content": "options kvm_intel nested=1\noptions kvm_amd nested=1\n",
        }],
    }


def expand_wireguard(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("wireguard") or {}
    if not cfg.get("enable"):
        return {}
    iface = cfg.get("interface_name", "wg0")
    return {
        "packages": ["wireguard-tools"],
        "units": [f"wg-quick@{iface}.service"],
        "files": [{
            "path": f"/etc/wireguard/{iface}.conf",
            "content": cfg.get("config_content", ""),
        }],
    }


def expand_firewall(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("firewall") or {}
    if not cfg.get("enable"):
        return {}
    return {"packages": ["firewalld"], "units": ["firewalld.service"]}


# common HW-accel packages + per-driver extras (mirrors the old action)
_HWACCEL_COMMON = ["libva-utils", "vdpauinfo"]
_HWACCEL_DRIVER_PKGS = {
    "nvidia": ["libva-nvidia-driver", "nvtop"],
    "intel": ["intel-media-driver", "intel-gpu-tools", "libvdpau-va-gl"],
    "amd": ["libva-mesa-driver"],
}


def expand_hwaccel(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("hardware_acceleration") or {}
    if not cfg.get("enable"):
        return {}
    pkgs = list(_HWACCEL_COMMON)
    for drv in config.get("drivers", []):
        for p in _HWACCEL_DRIVER_PKGS.get(drv, []):
            if p not in pkgs:
                pkgs.append(p)
    return {"packages": pkgs}


# Order matters only for deterministic output; aggregation de-dups.
TOGGLES = [
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
    expand_wireguard, expand_firewall, expand_hwaccel,
]
