"""Cross-field preflight validation — run before the first mutation.

Pydantic (``JsonModel``) validates each field's *shape*. It cannot see that a
user demands the ``docker`` group while only ``podman-docker`` is declared (no
package creates that group, so ``useradd -G docker`` fails — after the disk has
already been wiped), that ``sddm.service`` is enabled while Plasma now ships
``plasmalogin.service``, or that ``/etc/crypttab`` carries a ``swap`` entry for a
label no declared partition provides (``crypttab(5)``: the ``swap`` option
REFORMATS the named device on every boot).

The checks are deliberately conservative:

* **error** — a deterministic failure we can prove from the config alone.
* **warning** — a coherence smell we cannot prove (unknown group, config files
  for a display manager that is not the one being enabled).

Run it on the EXPANDED config (after ``expand_config``) so packages and units
contributed by toggles count as declared.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

# Groups that exist on every Arch system (filesystem/systemd/base), so no
# package needs to be declared for them.
_BASE_GROUPS: Set[str] = {
    "adm", "audio", "bin", "daemon", "dbus", "disk", "floppy", "ftp", "games",
    "http", "input", "kmem", "kvm", "log", "lp", "mail", "man", "network",
    "nobody", "optical", "power", "proc", "render", "rfkill", "root", "sgx",
    "storage", "sys", "systemd-journal", "tty", "users", "utmp", "uucp",
    "video", "wheel",
}

# group -> packages that create it (any one of them declared is enough).
# Sourced from each package's /usr/lib/sysusers.d entry.
_GROUP_PROVIDERS: Dict[str, Set[str]] = {
    "docker": {"docker"},
    "libvirt": {"libvirt"},
    "libvirt-qemu": {"libvirt"},
    "vboxusers": {"virtualbox", "virtualbox-bin"},
    "wireshark": {"wireshark-cli", "wireshark-qt"},
    "scanner": {"sane", "sane-airscan"},
    "plugdev": {"plugdev"},
    "adbusers": {"android-udev"},
    "gamemode": {"gamemode"},
    "realtime": {"realtime-privileges"},
    "i2c": {"i2c-tools"},
}

# unit -> packages that provide it. Meta packages that pull the provider count.
_UNIT_PROVIDERS: Dict[str, Set[str]] = {
    "sddm.service": {"sddm", "sddm-git"},
    "gdm.service": {"gdm"},
    "lightdm.service": {"lightdm"},
    "ly.service": {"ly"},
    "plasmalogin.service": {"plasma-login-manager", "plasma-meta", "plasma"},
    "docker.service": {"docker"},
    "docker.socket": {"docker"},
    "libvirtd.service": {"libvirt"},
    "sshd.service": {"openssh"},
    "firewalld.service": {"firewalld"},
    "snapper-timeline.timer": {"snapper"},
    "snapper-cleanup.timer": {"snapper"},
    "snapper-boot.timer": {"snapper"},
}

_DISPLAY_MANAGER_UNITS = {"sddm.service", "gdm.service", "lightdm.service",
                          "ly.service", "plasmalogin.service"}

# crypttab(5) options. Bare flags vs key=value options — `size512` (the token
# seen in the 2026-07-19 config) is neither, so it is rejected.
_CRYPTTAB_FLAGS: Set[str] = {
    "discard", "luks", "swap", "tmp", "noauto", "nofail", "none", "plain",
    "readonly", "read-only", "verify", "bitlk", "fvault2", "tcrypt",
    "tcrypt-hidden", "tcrypt-system", "tcrypt-veracrypt", "same-cpu-crypt",
    "submit-from-crypt-cpus", "no-read-workqueue", "no-write-workqueue",
    "_netdev", "netdev", "headless", "try-empty-password",
}
_CRYPTTAB_KEYS: Set[str] = {
    "cipher", "hash", "header", "key-slot", "keyfile-offset", "keyfile-size",
    "keyfile-erase", "offset", "sector-size", "size", "skip", "timeout",
    "tries", "token-timeout", "pkcs11-uri", "fido2-device", "fido2-cid",
    "fido2-rp", "tpm2-device", "tpm2-pcrs", "tpm2-signature", "tpm2-measure-pcr",
    "x-systemd.device-timeout", "x-initrd.attach", "veracrypt-pim",
}


@dataclass(frozen=True)
class Issue:
    """One preflight finding. ``level`` is "error" or "warning"."""
    level: str
    code: str
    message: str


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


def render(issues: Iterable[Issue]) -> str:
    """Human-readable block, errors first."""
    items = sorted(issues, key=lambda i: 0 if i.level == "error" else 1)
    return "\n".join(f"  [{i.level}] {i.code}: {i.message}" for i in items)


# --- helpers --------------------------------------------------------------- #

def _declared_packages(config: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    for pkg in config.get("packages") or []:
        if isinstance(pkg, str):
            name = pkg
        elif isinstance(pkg, dict):
            name = pkg.get("name", "")
        else:
            name = getattr(pkg, "name", "")
        if name.startswith("aur-"):          # deprecated prefix, still accepted
            name = name[4:]
        if name:
            names.add(name)
    return names


def _partitions(config: Dict[str, Any]):
    disks = config.get("disks") or {}
    if isinstance(disks, dict):
        entries = disks.get("disks") or []
    elif isinstance(disks, list):            # legacy bare-list shape
        entries = disks
    else:
        entries = []
    for disk in entries:
        if not isinstance(disk, dict):
            continue
        for part in disk.get("partitions") or []:
            if isinstance(part, dict):
                yield disk, part


def _declared_block_ids(config: Dict[str, Any]) -> Set[str]:
    """Every identifier a crypttab/fstab entry could legitimately name."""
    ids: Set[str] = set()
    for disk, part in _partitions(config):
        for key in ("label", "luks_name", "luks_uuid", "uuid"):
            value = part.get(key)
            if value:
                ids.add(str(value))
        device = disk.get("device")
        if device:
            ids.add(str(device))
    return ids


def _enabled_units(config: Dict[str, Any]) -> List[str]:
    systemd = config.get("systemd") or {}
    if not isinstance(systemd, dict):
        return []
    return list(systemd.get("enable_units") or []) + list(systemd.get("enable_sockets") or [])


def _crypttab_content(config: Dict[str, Any]) -> Optional[str]:
    for entry in config.get("files") or []:
        path = entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", "")
        if path == "/etc/crypttab":
            return entry.get("content") if isinstance(entry, dict) else getattr(entry, "content", "")
    return None


# --- checks ---------------------------------------------------------------- #

def _check_groups(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    issues: List[Issue] = []
    seen: Set[str] = set()
    for user in config.get("users") or []:
        if not isinstance(user, dict):
            continue
        for group in user.get("groups") or []:
            if group in seen or group in _BASE_GROUPS:
                continue
            seen.add(group)
            providers = _GROUP_PROVIDERS.get(group)
            if providers is None:
                issues.append(Issue(
                    "warning", "unknown_group",
                    f"group {group!r} is not a base group and dasik does not know "
                    "which package creates it; useradd will fail if nothing does."))
            elif not (providers & packages):
                issues.append(Issue(
                    "error", "group_without_provider",
                    f"user {user.get('username')!r} requires group {group!r}, but "
                    f"no declared package creates it (provided by: "
                    f"{', '.join(sorted(providers))}). Declare one of them or drop "
                    "the group — `useradd -G` fails on a missing group."))
    return issues


def _check_units(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    issues: List[Issue] = []
    units = _enabled_units(config)
    for unit in units:
        providers = _UNIT_PROVIDERS.get(unit)
        if providers and not (providers & packages):
            issues.append(Issue(
                "error", "unit_without_provider",
                f"unit {unit!r} is enabled but no declared package provides it "
                f"(provided by: {', '.join(sorted(providers))}). `systemctl enable` "
                "would fail on the target."))
    dms = [u for u in units if u in _DISPLAY_MANAGER_UNITS]
    if len(dms) > 1:
        issues.append(Issue(
            "error", "multiple_display_managers",
            f"more than one display manager enabled ({', '.join(sorted(dms))}); "
            "display-manager.service can only be one of them."))
    return issues


def _check_crypttab(config: Dict[str, Any]) -> List[Issue]:
    content = _crypttab_content(config)
    if not content:
        return []
    issues: List[Issue] = []
    known_ids = _declared_block_ids(config)
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        name, device = fields[0], (fields[1] if len(fields) > 1 else "")
        options = fields[3].split(",") if len(fields) > 3 else []
        for opt in options:
            opt = opt.strip()
            if not opt:
                continue
            key, sep, _value = opt.partition("=")
            known = key in _CRYPTTAB_KEYS if sep else opt in _CRYPTTAB_FLAGS
            if not known:
                issues.append(Issue(
                    "error", "crypttab_bad_option",
                    f"/etc/crypttab entry {name!r}: unknown option {opt!r} "
                    "(crypttab(5) uses `key=value`, e.g. `size=512`)."))
        ident = device.split("=", 1)[1] if "=" in device else device
        if device and ident not in known_ids:
            destructive = "swap" in options
            issues.append(Issue(
                "error" if destructive else "warning", "crypttab_undeclared_device",
                f"/etc/crypttab entry {name!r} references {device!r}, which no "
                "declared partition provides"
                + (" — the `swap` option REFORMATS that device on every boot."
                   if destructive else ".")))
    return issues


def preflight(config: Dict[str, Any]) -> List[Issue]:
    """Return every cross-field issue found in *config* (expanded form)."""
    packages = _declared_packages(config)
    issues: List[Issue] = []
    issues += _check_groups(config, packages)
    issues += _check_units(config, packages)
    issues += _check_crypttab(config)
    return issues
