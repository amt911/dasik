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


# Order matters only for deterministic output; aggregation de-dups.
TOGGLES = [expand_bluetooth, expand_cups, expand_trim, expand_kvm]
