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
    # NOTE: no "iptables-nft" here. It CONFLICTS with the `iptables` that base/
    # systemd already pulls in, and `pacman -S iptables-nft` cannot swap it
    # non-interactively (the conflict prompt defaults to No under --noconfirm), so
    # declaring it left the install silently failing and the day-2 plan re-trying
    # forever. libvirt's iptables dependency is satisfied by the present iptables/
    # nftables; the NAT network works either way.
    "dnsmasq", "openbsd-netcat", "dmidecode",
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
    # mesa-vdpau was removed from the Arch repos (radeonsi VDPAU is in `mesa`).
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


MKINITCPIO_HOOKS = ["90-mkinitcpio-install.hook", "60-mkinitcpio-remove.hook"]
_MKINITCPIO_HOOKS = MKINITCPIO_HOOKS          # backwards-compatible alias

# The pacman Target= the neutralizer triggers on: no package can ever match it,
# and its presence in /etc/pacman.d/hooks is how `sync` recognises that dracut —
# not mkinitcpio — owns the initramfs on a target where both are installed.
NEUTRALIZER_MARKER = "dasik-initramfs-neutralizer-never-matches"


def _neutralizer_hook(name: str) -> str:
    # A same-named hook under /etc/pacman.d/hooks OVERRIDES the one mkinitcpio ships
    # in /usr/share/libalpm/hooks. This one triggers on a package that never exists
    # and runs /bin/true, so mkinitcpio never regenerates the initramfs — dracut is
    # the sole generator. Non-destructive (mkinitcpio stays installed) and reversible.
    return (
        "# Managed by dasik: disables mkinitcpio's initramfs regeneration because\n"
        f"# the 'initramfs' generator is dracut. Overrides /usr/.../{name}.\n"
        "[Trigger]\n"
        "Operation = Install\n"
        "Type = Package\n"
        f"Target = {NEUTRALIZER_MARKER}\n"
        "[Action]\n"
        "Description = mkinitcpio disabled by dasik (dracut manages the initramfs)\n"
        "When = PostTransaction\n"
        "Exec = /bin/true\n"
    )


def expand_initramfs(config: Dict[str, Any]) -> Dict[str, Any]:
    """When the initramfs generator is dracut, install the dracut package.

    Both generators ship pacman hooks that regenerate the initramfs on
    kernel/systemd updates, so leaving mkinitcpio's hooks active means BOTH run
    and clobber each other. mkinitcpio stays installed and its hooks are
    overridden with no-ops (safe/reversible) — by PacmanHooksAction, which runs
    before the first transaction (see ``_neutralizer_hook``).
    """
    if config.get("initramfs", "mkinitcpio") != "dracut":
        return {}
    # The neutralizer hooks are NOT contributed to `files`: DropFilesAction runs
    # after Packages, far too late — pacstrap and every package transaction would
    # already have re-run mkinitcpio and clobbered the dracut image.
    # PacmanHooksAction writes them in phase 1, before the first pacman call.
    # Here we only need the package.
    return {"packages": ["dracut"]}


def expand_zram(config: Dict[str, Any]) -> Dict[str, Any]:
    # A declared `zram` section needs the generator that reads
    # /etc/systemd/zram-generator.conf. ZramAction writes the file.
    if not (config.get("zram") or {}):
        return {}
    return {"packages": ["zram-generator"]}


_CPUPOWER_CONF = "/etc/default/cpupower"


def expand_cpu(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("cpu") or {}
    if not cfg:
        return {}
    packages: list = []
    units: list = []
    files: list = []
    if cfg.get("power_profiles_daemon", True):
        packages.append("power-profiles-daemon")
        units.append("power-profiles-daemon.service")
    governor = cfg.get("governor")
    if governor:
        # cpupower applies a fixed governor; power-profiles-daemon would fight
        # it, which is why preflight warns when both are declared.
        packages.append("cpupower")
        units.append("cpupower.service")
        files.append({"path": _CPUPOWER_CONF,
                      "content": f'# Managed by dasik\ngovernor="{governor}"\n'})
    out: Dict[str, Any] = {}
    if packages:
        out["packages"] = packages
    if units:
        out["units"] = units
    if files:
        out["files"] = files
    return out


_REFLECTOR_CONF = "/etc/xdg/reflector/reflector.conf"


def expand_reflector(config: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.get("reflector") or {}
    if not cfg:
        return {}
    lines = ["# Managed by dasik"]
    lines += [f"--country {c}" for c in cfg.get("countries") or []]
    lines += [f"--protocol {p}" for p in cfg.get("protocols") or ["https"]]
    latest = cfg.get("latest", 20)
    if latest:
        lines.append(f"--latest {latest}")
    lines.append(f"--sort {cfg.get('sort', 'rate')}")
    lines.append(f"--save {cfg.get('save', '/etc/pacman.d/mirrorlist')}")
    return {
        "packages": ["reflector"],
        # Only the timer: the one-shot service is what the timer triggers.
        "units": ["reflector.timer"],
        "files": [{"path": _REFLECTOR_CONF, "content": "\n".join(lines) + "\n"}],
    }


PLYMOUTHD_CONF = "/etc/plymouth/plymouthd.conf"


def expand_plymouth(config: Dict[str, Any]) -> Dict[str, Any]:
    """Boot splash: the package, plus the daemon config when a theme is declared.

    The old installer built plymouth from the AUR with `yay`; it lives in
    `extra` today, so a plain package is enough. The theme also has to reach the
    initramfs image — the wiki is explicit that a theme change requires
    regenerating it — which the initramfs backends handle (they add the
    hook/module and treat this file as an input to the image freshness check).
    """
    cfg = config.get("plymouth")
    if cfg is None:
        return {}
    out: Dict[str, Any] = {"packages": ["plymouth"]}
    theme = (cfg or {}).get("theme")
    if theme:
        out["files"] = [{"path": PLYMOUTHD_CONF,
                         "content": f"# Managed by dasik\n[Daemon]\nTheme={theme}\n"}]
    return out


def expand_sdboot_update(config: Dict[str, Any]) -> Dict[str, Any]:
    # systemd ships this unit itself: it runs `bootctl update` when the ESP's
    # loader is older than the installed systemd. The old imperative installer
    # built the AUR `systemd-boot-pacman-hook` for the same job; the native unit
    # needs no package at all.
    if config.get("bootloader") not in ("sd-boot", "systemd-boot"):
        return {}
    return {"units": ["systemd-boot-update.service"]}


# Order matters only for deterministic output; aggregation de-dups.
TOGGLES = [
    expand_bluetooth, expand_cups, expand_trim, expand_kvm,
    expand_wireguard, expand_firewall, expand_hwaccel, expand_snapper,
    expand_drivers, expand_initramfs, expand_zram, expand_cpu,
    expand_sdboot_update, expand_reflector, expand_plymouth,
]
