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
        # Every declared user gets the libvirt group so they can drive
        # virt-manager without root; UsersAction reconciles it idempotently.
        "user_groups": ["libvirt"],
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


def expand_snapper(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("snapper") or {}
    if not cfg.get("enable"):
        return {}
    # snapper + snap-pac (pacman-hook snapshots); timeline + cleanup timers.
    # The configs themselves are created by SnapperAction.
    return {
        "packages": ["snapper", "snap-pac"],
        "units": ["snapper-timeline.timer", "snapper-cleanup.timer"],
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
    "amd": ["libva-mesa-driver", "mesa-vdpau"],
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


# Canonical GPU driver packages (verified against the arch-wiki NVIDIA /
# Hardware_video_acceleration pages). `base` is always installed for a declared
# driver; `lib32` is added only when multilib is enabled (those packages live in
# the [multilib] repo and are needed for 32-bit apps like Steam).
# Unknown keys (e.g. a legacy "nvidia_old") are intentionally NOT mapped — a
# wrong package is worse than a documented no-op; list it in `packages` instead.
_DRIVER_PKGS = {
    "nvidia": {"base": ["nvidia", "nvidia-utils", "nvidia-settings"],
               "lib32": ["lib32-nvidia-utils"]},
    "nvidia-open": {"base": ["nvidia-open", "nvidia-utils", "nvidia-settings"],
                    "lib32": ["lib32-nvidia-utils"]},
    "nouveau": {"base": ["mesa", "vulkan-nouveau"],
                "lib32": ["lib32-mesa", "lib32-vulkan-nouveau"]},
    "intel": {"base": ["mesa", "vulkan-intel", "intel-media-driver"],
              "lib32": ["lib32-mesa", "lib32-vulkan-intel"]},
    "amd": {"base": ["mesa", "vulkan-radeon", "libva-mesa-driver"],
            "lib32": ["lib32-mesa", "lib32-vulkan-radeon"]},
}


def expand_drivers(config: Dict[str, Any]) -> Dict[str, Any]:
    multilib = bool((config.get("pacman") or {}).get("multilib"))
    pkgs: list = []
    for drv in config.get("drivers", []):
        spec = _DRIVER_PKGS.get(drv)
        if not spec:
            continue
        for p in spec["base"] + (spec["lib32"] if multilib else []):
            if p not in pkgs:
                pkgs.append(p)
    return {"packages": pkgs} if pkgs else {}


# Order matters only for deterministic output; aggregation de-dups.
TOGGLES = [
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
    expand_wireguard, expand_firewall, expand_hwaccel, expand_snapper,
    expand_drivers,
]
