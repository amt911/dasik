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
    """The firewall the `backend` field asks for.

    Only one of them: firewalld and ufw are both front-ends to netfilter, and
    running the pair means each rewrites the other's rules on every restart.
    """
    cfg = config.get("firewall") or {}
    if not cfg.get("enable"):
        return {}
    if cfg.get("backend", "firewalld") == "ufw":
        return {"packages": ["ufw"], "units": ["ufw.service"]}
    return {"packages": ["firewalld"], "units": ["firewalld.service"]}


# common HW-accel packages + per-driver extras (mirrors the old action)
_HWACCEL_COMMON = ["libva-utils", "vdpauinfo"]
_HWACCEL_DRIVER_PKGS = {
    "nvidia": ["libva-nvidia-driver", "nvtop"],
    "intel": ["intel-media-driver", "intel-gpu-tools", "libvdpau-va-gl"],
    # mesa-vdpau was removed from the Arch repos (radeonsi VDPAU is in `mesa`),
    # and as of mesa 1:24.2.7 so was libva-mesa-driver — `mesa` now *provides*
    # and *replaces* it, so naming it aborts the transaction with "target not
    # found". The VA-API driver comes with mesa itself.
    "amd": ["mesa"],
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
    # `nvidia` (the proprietary kernel module) is GONE from the repos: NVIDIA
    # stopped shipping it and nvidia-open `Replaces: nvidia<=580.119.02-2`. The
    # key stays so existing configs keep working, but it can only mean the open
    # modules now — declaring the old name aborts the whole install.
    "nvidia": {"base": ["nvidia-open", "nvidia-utils", "nvidia-settings"],
               "lib32": ["lib32-nvidia-utils"]},
    "nvidia-open": {"base": ["nvidia-open", "nvidia-utils", "nvidia-settings"],
                    "lib32": ["lib32-nvidia-utils"]},
    "nouveau": {"base": ["mesa", "vulkan-nouveau"],
                "lib32": ["lib32-mesa", "lib32-vulkan-nouveau"]},
    "intel": {"base": ["mesa", "vulkan-intel", "intel-media-driver"],
              "lib32": ["lib32-mesa", "lib32-vulkan-intel"]},
    # No libva-mesa-driver: `mesa` provides and replaces it (see above).
    "amd": {"base": ["mesa", "vulkan-radeon"],
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


def expand_oomd(config: Dict[str, Any]) -> Dict[str, Any]:
    # Declared oomd settings need the daemon that reads them; systemd ships it,
    # so there is no package — only the unit. system.conf/user.conf configure
    # the managers themselves and need nothing enabled.
    if not (config.get("oomd") or {}):
        return {}
    return {"units": ["systemd-oomd.service"]}


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


_APPARMOR_PROFILE_DIR = "/etc/apparmor.d"
_AUDIT_TMPFILES = "/etc/tmpfiles.d/audit.conf"
# aa-notify ships with the `apparmor` package; these are its optdepends, and
# without them it exits instead of notifying.
_AA_NOTIFY_PACKAGES = ["python-notify2", "python-psutil", "tk"]
_AA_NOTIFY_AUTOSTART = ".config/autostart/apparmor-notify.desktop"
AA_NOTIFY_DESKTOP = (
    "[Desktop Entry]\n"
    "# Managed by dasik\n"
    "Type=Application\n"
    "Name=AppArmor Notify\n"
    "Comment=Receive on-screen notifications of AppArmor denials\n"
    "TryExec=aa-notify\n"
    "Exec=aa-notify -p -s 1 -w 60 -f /var/log/audit/audit.log\n"
    "StartupNotify=false\n"
    "NoDisplay=true\n"
)


def expand_apparmor(config: Dict[str, Any]) -> Dict[str, Any]:
    """AppArmor: the package, the unit, and optionally the audit framework.

    The kernel parameter that actually turns AppArmor on is derived by
    KernelCmdlineAction. Without `lsm=` naming it, the package installs, the
    unit starts, and every profile is inert — which looks exactly like a
    working setup until someone runs `aa-enabled`.

    The log group is ``adm``, not a fresh ``audit`` one. NOTHING on Arch creates
    an `audit` group — the wiki tells you to run ``groupadd -r audit`` by hand —
    and dasik never creates groups, so declaring it would only make
    ``useradd -G audit`` fail after the disk was already partitioned. The wiki's
    own tip is to reuse an existing system group; ``adm`` is the traditional
    log-reading one and exists on every Arch install.

    The tmpfiles override is not decoration: Arch ships
    ``z /var/log/audit 700 root root``, re-applied by systemd-tmpfiles on every
    upgrade, so a user in the log group can read the denials until the next
    ``pacman -Syu`` and never again.
    """
    cfg = config.get("apparmor")
    if cfg is None or not cfg.get("enable", True):
        return {}
    packages = ["apparmor"]
    units = ["apparmor.service"]
    files = [{"path": f"{_APPARMOR_PROFILE_DIR}/{p['name']}", "content": p["content"]}
             for p in cfg.get("extra_profiles") or []]
    out: Dict[str, Any] = {}
    if cfg.get("audit"):
        packages.append("audit")
        units.append("auditd.service")
        out["user_groups"] = ["adm"]
        files.append({
            "path": _AUDIT_TMPFILES,
            "content": ("# Managed by dasik: Arch's own tmpfiles entry resets\n"
                        "# /var/log/audit to 700 on every upgrade, which locks the\n"
                        "# log group back out of the denial log.\n"
                        "z /var/log/audit 750 root adm - -\n"),
        })
    if cfg.get("desktop_notifications"):
        # The wiki's own recipe: aa-notify's optional dependencies, plus an
        # autostart entry per desktop user. root is skipped — it has no session
        # to notify, and the entry would sit in /root doing nothing.
        packages += _AA_NOTIFY_PACKAGES
        out["home_files"] = [
            {"user": u["username"], "path": _AA_NOTIFY_AUTOSTART,
             "content": AA_NOTIFY_DESKTOP}
            for u in config.get("users") or []
            if isinstance(u, dict) and u.get("username") and u["username"] != "root"
        ]
    out["packages"] = packages
    out["units"] = units
    if files:
        out["files"] = files
    return out


def expand_pam(config: Dict[str, Any]) -> Dict[str, Any]:
    """The password-quality library, when the policy is declared.

    faillock and limits need no package at all: pam_faillock is already in
    Arch's stack and pam_limits ships with pam itself. Only pwquality adds a
    dependency, and without it `pam_pwquality.so` in /etc/pam.d/passwd would
    break the `passwd` command outright.
    """
    pwquality = (config.get("pam") or {}).get("pwquality")
    if pwquality is None or not pwquality.get("enable", True):
        return {}
    return {"packages": ["libpwquality"]}


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
    expand_drivers, expand_initramfs, expand_zram, expand_oomd, expand_cpu,
    expand_sdboot_update, expand_reflector, expand_plymouth,
    expand_apparmor, expand_pam,
]
