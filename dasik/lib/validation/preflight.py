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

import os
from difflib import get_close_matches
from typing import get_args
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel

from ..models.json_model import JsonModel
from ..actions.swap_encryption import (
    crypttab_line,
    random_swap_partitions,
    swap_names,
)

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

# Display-manager unit -> packages that provide it (a meta package that pulls the
# provider counts). ERROR when none is declared: an enabled DM unit no package
# ships means no graphical login at all, and `systemctl enable` fails outright —
# exactly the sddm.service / plasmalogin.service drift of 2026-07-19.
_DM_UNIT_PROVIDERS: Dict[str, Set[str]] = {
    "sddm.service": {"sddm", "sddm-git"},
    "gdm.service": {"gdm"},
    "lightdm.service": {"lightdm"},
    "ly.service": {"ly"},
    "plasmalogin.service": {"plasma-login-manager", "plasma-meta", "plasma"},
}

# Other units with a known provider. WARNING only: the provider is often present
# as somebody else's dependency (openssh, libvirt), so an undeclared name does
# not prove the unit will be missing — and blocking on it would be wrong.
_UNIT_PROVIDERS: Dict[str, Set[str]] = {
    "docker.service": {"docker"},
    "docker.socket": {"docker"},
    "libvirtd.service": {"libvirt"},
    "sshd.service": {"openssh"},
    "firewalld.service": {"firewalld"},
    "snapper-timeline.timer": {"snapper"},
    "snapper-cleanup.timer": {"snapper"},
    "snapper-boot.timer": {"snapper"},
    "power-profiles-daemon.service": {"power-profiles-daemon"},
    "cpupower.service": {"cpupower"},
    "reflector.timer": {"reflector"},
}

# Packages that ship /usr/bin/sudo (and visudo). `base` does NOT.
_SUDO_PROVIDERS: Set[str] = {"sudo", "base-devel"}

_DISPLAY_MANAGER_UNITS = set(_DM_UNIT_PROVIDERS)

# Config directories that belong to one specific display manager.
_DM_CONFIG_FIELDS = {"sddm_conf_d": "sddm.service"}

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
    # The 1 MiB ext2 label filesystem a random-key swap is addressed by. Without
    # it, dasik's own derived entry reads as pointing at an undeclared device —
    # and as DESTRUCTIVE, since a `swap` entry reformats whatever it names.
    for part in random_swap_partitions(config):
        ids.add(swap_names(part)[1])
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
        providers = _DM_UNIT_PROVIDERS.get(unit)
        if providers and not (providers & packages):
            issues.append(Issue(
                "error", "unit_without_provider",
                f"display manager unit {unit!r} is enabled but no declared package "
                f"provides it (provided by: {', '.join(sorted(providers))}). "
                "`systemctl enable` would fail and the system would have no "
                "graphical login."))
            continue
        providers = _UNIT_PROVIDERS.get(unit)
        if providers and not (providers & packages):
            issues.append(Issue(
                "warning", "unit_without_provider",
                f"unit {unit!r} is enabled but no declared package provides it "
                f"(provided by: {', '.join(sorted(providers))}); it will only work "
                "if something else pulls that package in."))
    dms = [u for u in units if u in _DISPLAY_MANAGER_UNITS]
    if len(dms) > 1:
        issues.append(Issue(
            "error", "multiple_display_managers",
            f"more than one display manager enabled ({', '.join(sorted(dms))}); "
            "display-manager.service can only be one of them."))
    for field, owner_unit in _DM_CONFIG_FIELDS.items():
        if config.get(field) and owner_unit not in units:
            issues.append(Issue(
                "warning", "display_manager_config_mismatch",
                f"{field!r} is declared but {owner_unit!r} is not enabled; those "
                "files are read by that display manager only (Plasma Login Manager "
                "reads /etc/plasmalogin.conf.d instead)."))
    return issues


def _check_sudo(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """A sudoers fragment is useless without sudo installed.

    An EXPLICIT `sudo` block is an error: the user asked for something the config
    cannot deliver (the fragment could not even be validated with visudo). The
    IMPLICIT default (no block, a user in `wheel`) only warns — a config that
    installs fine today must not start failing preflight because of a default it
    never asked for.
    """
    if _SUDO_PROVIDERS & packages:
        return []
    if config.get("sudo") is not None:
        return [Issue(
            "error", "sudo_without_provider",
            "a `sudo` block is declared but no declared package provides sudo "
            f"(provided by: {', '.join(sorted(_SUDO_PROVIDERS))}); the fragment "
            "could not even be validated with visudo.")]
    for user in config.get("users") or []:
        if isinstance(user, dict) and "wheel" in (user.get("groups") or []):
            return [Issue(
                "warning", "wheel_without_sudo",
                f"user {user.get('username')!r} is in `wheel` but no declared "
                "package provides sudo, so the group grants nothing.")]
    return []


def _check_unknown_keys(config: Dict[str, Any]) -> List[Issue]:
    """Name every key dasik does not know, and guess what it meant.

    The model deliberately IGNORES unknown keys so a config written for another
    version stays loadable (tests/lib/json_parser: unknown top-level keys are
    ignored). The cost is silence: a typo produces a machine quietly missing the
    feature, with `check` and `plan` both saying nothing. So dasik says it here
    — as a warning, which informs without aborting.

    One level deep as well as at the root: `sudo.whel` is the same mistake.
    `metadata` is free-form by design and never flagged.
    """
    known = set(JsonModel.model_fields)
    issues: List[Issue] = []
    for key, value in config.items():
        if key in known:
            nested = JsonModel.model_fields[key].annotation
            issues += _check_nested_keys(key, value, nested)
            continue
        if key.startswith("$"):        # $include and friends: handled elsewhere
            continue
        issues.append(Issue("warning", "unknown_config_key",
                            f"unknown key {key!r}: dasik ignores it, so whatever "
                            f"it declares will not happen.{_did_you_mean(key, known)}"))
    return issues


def _check_nested_keys(block: str, value: Any, annotation: Any) -> List[Issue]:
    """The same check inside one declared block, when its model is knowable."""
    if block == "metadata" or not isinstance(value, dict):
        return []
    model = next((a for a in _annotation_models(annotation)), None)
    if model is None:
        return []
    known = set(model.model_fields)
    return [Issue("warning", "unknown_config_key",
                  f"unknown key '{block}.{k}': dasik ignores it, so whatever it "
                  f"declares will not happen.{_did_you_mean(k, known)}")
            for k in value if k not in known]


def _annotation_models(annotation: Any):
    """The model an annotation IS, unwrapping Optional — never one it merely
    CONTAINS.

    `package_sources` is Dict[str, GitPackageSourceModel]: its keys are package
    names the user chose, not model fields. Descending into it flagged every
    real entry as a typo (`unknown key 'package_sources.config-saver'`), which
    is exactly the noise this check exists to avoid. Same for `zram`, keyed by
    device name. So: only a plain model, or Optional[model].
    """
    args = get_args(annotation)
    candidates = [annotation]
    if args and type(None) in args:            # Optional[X] / X | None
        candidates += [a for a in args if a is not type(None)]
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            yield candidate


def _did_you_mean(key: str, known: Set[str]) -> str:
    close = get_close_matches(key, sorted(known), n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


def _check_cpu(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """power-profiles-daemon owns the frequency policy it shares with nobody."""
    cpu = config.get("cpu") or {}
    if not cpu or not cpu.get("power_profiles_daemon", True):
        return []
    issues: List[Issue] = []
    if cpu.get("governor"):
        issues.append(Issue(
            "warning", "ppd_and_governor",
            "power-profiles-daemon manages the energy-performance preference, so a "
            "fixed cpupower governor will be fought over; declare one or the other."))
    if "tlp" in packages:
        issues.append(Issue(
            "error", "ppd_and_tlp",
            "power-profiles-daemon and tlp both manage power policy and conflict; "
            "keep one of them."))
    return issues


_FIREWALL_PACKAGES = {"firewalld": "firewalld", "ufw": "ufw"}


def _check_firewall_backend(config: Dict[str, Any], packages: Set[str]) -> List[Issue]:
    """Two netfilter front-ends must not be installed together.

    firewalld and ufw each own the whole rule set: whichever starts last wipes
    the other's rules, so the machine's actual policy depends on unit ordering.
    Provable from the config, hence an error.
    """
    cfg = config.get("firewall") or {}
    if not cfg.get("enable"):
        return []
    backend = cfg.get("backend", "firewalld")
    other = "ufw" if backend == "firewalld" else "firewalld"
    if other in packages:
        return [Issue(
            "error", "firewall_backend_conflict",
            f"the firewall backend is {backend!r} but {other!r} is also declared "
            f"in packages. Both are front-ends to netfilter and each rewrites the "
            f"other's rules on start, so the effective policy would depend on unit "
            f"ordering. Keep one.")]
    return []


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


def _check_random_swap(config: Dict[str, Any]) -> List[Issue]:
    """A random-key swap cannot coexist with hibernation, nor with a crypttab
    the config writes itself.

    The key is drawn from /dev/urandom at every boot and discarded at shutdown,
    so a resume image written under the previous key can never be decrypted —
    provable from the config alone, hence an error. It has to be an error
    because the failure is silent: hibernating works, and the session is gone on
    the way back.
    """
    parts = random_swap_partitions(config)
    if not parts:
        return []
    issues: List[Issue] = []
    resume = [word for token in config.get("kernel_cmdline") or []
              for word in str(token).split() if word.startswith("resume=")]
    if resume:
        issues.append(Issue(
            "error", "random_swap_hibernation",
            f"a swap declares swap_encryption='random' while the kernel cmdline "
            f"asks to resume from it ({resume[0]}): the random key is discarded at "
            f"shutdown, so the hibernation image can never be decrypted. Declare "
            f"the swap with `encrypt: true` (LUKS, one persistent key) to "
            f"hibernate, or drop the resume parameter."))

    verbatim = _crypttab_content(config)
    if verbatim:
        # dasik yields /etc/crypttab to a config that declares it, so a missing
        # entry there means the swap is never opened — and nothing says so.
        named = {line.strip().split()[0]
                 for line in verbatim.splitlines() if line.strip()
                 and not line.strip().startswith("#")}
        for part in random_swap_partitions(config):
            mapper, _ = swap_names(part)
            if mapper not in named:
                issues.append(Issue(
                    "error", "random_swap_crypttab_conflict",
                    f"the config declares its own /etc/crypttab in `files`, so dasik "
                    f"will not merge the derived entry into it and the random-key "
                    f"swap {mapper!r} would never be opened. Add this line to that "
                    f"file: {crypttab_line(part)}"))
    return issues


def _check_unlock_keyfile(config: Dict[str, Any]) -> List[Issue]:
    """Coherence of the keyfile unlock (`rd.luks.key`).

    Two ways to declare something that cannot work:

    * a key device with no keyfile on it — no ``rd.luks.key`` is ever emitted,
      so the declaration silently does nothing (error: provable from the config);
    * a key device whose filesystem is not declared — the initramfs may well
      lack the module and be unable to read the pendrive at boot, which is only
      a smell, because the root filesystem may happen to provide it (warning).
    """
    issues: List[Issue] = []
    disks = config.get("disks", {})
    if not isinstance(disks, dict):
        return issues
    for disk in disks.get("disks", []):
        for part in disk.get("partitions", []):
            keyfile = part.get("unlock_keyfile")
            keydev = part.get("unlock_keydev")
            label = part.get("label", "?")
            if keydev and not keyfile:
                issues.append(Issue(
                    "error", "keydev_without_keyfile",
                    f"partition {label!r} declares unlock_keydev={keydev!r} but no "
                    "unlock_keyfile: there is no key to look for on that device, "
                    "so no rd.luks.key is emitted and the unlock never happens."))
            elif keyfile and not keydev:
                issues.append(Issue(
                    "warning", "keyfile_embedded_in_initramfs",
                    f"partition {label!r} declares unlock_keyfile={keyfile!r} with no "
                    "unlock_keydev, so the key is baked into the initramfs — which "
                    "lives on the UNENCRYPTED ESP. Anyone with the disk can read it. "
                    "Put the keyfile on a removable device (unlock_keydev) unless "
                    "you deliberately only want to defend against a disk pulled from "
                    "a powered-off machine without its ESP."))
            elif keyfile and keydev and not part.get("unlock_keydev_fs"):
                issues.append(Issue(
                    "warning", "keydev_without_filesystem",
                    f"partition {label!r} unlocks from a key device but declares no "
                    "unlock_keydev_fs; unless the root filesystem already provides "
                    "that module, the initramfs cannot read the device and the "
                    "boot falls back to the passphrase."))
    return issues


# Both bootloaders dasik knows how to install are EFI-only: `bootctl install`,
# and `grub-install --target=x86_64-efi --efi-directory=/boot`.
_EFI_BOOTLOADERS = {"sd-boot", "systemd-boot", "grub"}


def _check_efi(config: Dict[str, Any], efi_boot: Optional[bool]) -> List[Issue]:
    """Refuse an EFI bootloader when the installer is not booted in EFI mode.

    `bootctl install` does not fail on a legacy-BIOS boot: it prints "Not booted
    with EFI, skipping EFI variable setup", writes the loader to the ESP and
    exits 0. The install therefore reports success and the machine reboots
    straight past it into whatever else the firmware finds — typically the ISO
    it was installed from. Only an install (a config that partitions disks) is
    affected; a day-2 run against a live system declares no `disks` and is
    already booting somehow.
    """
    if efi_boot is not False or not config.get("disks"):
        return []
    loader = str(config.get("bootloader", "grub"))
    if loader not in _EFI_BOOTLOADERS:
        return []
    return [Issue(
        "error", "no_efi_firmware",
        f"bootloader {loader!r} needs UEFI, but this system is not booted in EFI "
        "mode (/sys/firmware/efi is absent), so the installed loader could never "
        "be started — `bootctl install` would still exit 0 and the machine would "
        "reboot into the installer media. Boot the ISO in UEFI mode (in QEMU: "
        "OVMF firmware; in virt-manager: Customize before install → Overview → "
        "Firmware = UEFI).")]


def preflight(config: Dict[str, Any],
              efi_boot: Optional[bool] = None) -> List[Issue]:
    """Return every cross-field issue found in *config* (expanded form).

    *efi_boot* describes the environment the installer runs in; it is probed
    from /sys/firmware/efi when not given, and passed explicitly by tests.
    """
    if efi_boot is None:
        efi_boot = os.path.exists("/sys/firmware/efi")
    packages = _declared_packages(config)
    issues: List[Issue] = []
    issues += _check_groups(config, packages)
    issues += _check_units(config, packages)
    issues += _check_sudo(config, packages)
    issues += _check_unknown_keys(config)
    issues += _check_cpu(config, packages)
    issues += _check_firewall_backend(config, packages)
    issues += _check_crypttab(config)
    issues += _check_random_swap(config)
    issues += _check_unlock_keyfile(config)
    issues += _check_efi(config, efi_boot)
    return issues
