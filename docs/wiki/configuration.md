# JSON configuration reference

This page describes the JSON surface accepted by the **current `JsonModel` and nested Pydantic models on `main`**. Defaults below are code defaults, not merely examples.

## Important validation behavior

Dasik resolves config-split directives first, then validates the assembled object with Pydantic, then expands feature blocks and runs cross-field preflight.

Pydantic's current models do **not** set `extra="forbid"`. Therefore an unknown key is ignored by model validation rather than rejected. Do not rely on that as an extension mechanism: a misspelled key can be silently ineffective. Prefer `dasik check` and compare the key with this reference.

Everything at the root is optional/defaulted. A minimal `{}` is schema-valid, although whether it is useful depends on the verb and target.

## Top-level fields

| Field | Type | Default | Purpose |
| --- | --- | --- | --- |
| `metadata` | object or null | `null` | Free-form metadata; not applied. |
| `notes` | string or null | `null` | Free-form notes; not applied. |
| `disks` | object or null | `null` | Partitioning, filesystems, mounts and LUKS. |
| `timezone` | object or null | `null` | `/etc/localtime`. |
| `locales` | object or null | `null` | locale generation/default locale/vconsole keymap. |
| `network` | object or null | `null` | Network backend and default hosts. |
| `hostname` | string | `""` | `/etc/hostname`; empty means no hostname work. |
| `users` | list | `[]` | User accounts. |
| `packages` | list | `[]` | Packages by real package name. |
| `package_policy` | object | `{"unknown":"warn-and-skip"}` | Unknown-package policy. |
| `package_sources` | object | `{}` | Pinned Git PKGBUILD sources outside repos/AUR. |
| `drivers` | list[string] | `[]` | GPU driver families used by expansion. |
| `bootloader` | enum string | `"grub"` | `grub`, `sd-boot` or alias `systemd-boot`. |
| `initramfs` | enum string | `"mkinitcpio"` | `mkinitcpio` or `dracut`. |
| `kernel_cmdline` | list[string] | `[]` | Explicit extra kernel parameters. |
| `systemd` | object or null | `null` | Unit/socket enablement and unit disabling. |
| `pacman` | object or null | `null` | pacman options and multilib. |
| `udev_rules` | list | `[]` | Managed files in `/etc/udev/rules.d`. |
| `modprobe_conf` | list | `[]` | Managed files in `/etc/modprobe.d`. |
| `modules_load` | list | `[]` | Managed files in `/etc/modules-load.d`. |
| `sysctl_d` | list | `[]` | Managed files in `/etc/sysctl.d`. |
| `tmpfiles_d` | list | `[]` | Managed files in `/etc/tmpfiles.d`. |
| `sddm_conf_d` | list | `[]` | Managed files in `/etc/sddm.conf.d`. |
| `profile_d` | list | `[]` | Managed files in `/etc/profile.d`. |
| `etc_environment` | list[string] | `[]` | Lines in `/etc/environment`. |
| `files` | list | `[]` | Arbitrary managed absolute-path files. |
| `zram` | object or null | `null` | `zram-generator.conf` mapping. |
| `sudo` | object or null | `null` | Dasik-owned sudoers fragment. |
| `cpu` | object or null | `null` | CPU scaling policy. |
| `reflector` | object or null | `null` | Periodic mirrorlist refresh. |
| `bluetooth` | object or null | `null` | Bluetooth feature block. |
| `hardware_acceleration` | object or null | `null` | VA-API/VDPAU helper packages. |
| `kvm` | object or null | `null` | QEMU/libvirt stack. |
| `cups` | object or null | `null` | Printing/scanning stack. |
| `microsoft_fonts` | object or null | `null` | Microsoft fonts installation. |
| `firewall` | object or null | `null` | firewalld public-zone policy. |
| `wireguard` | object or null | `null` | wg-quick style WireGuard feature. |
| `snapper` | object or null | `null` | Snapper configs/timers. |
| `enable_trim` | bool | `false` | SSD trim behavior. |
| `enable_microcode` | bool | `false` | CPU microcode package/boot image. |
| `remove_home_on_delete` | bool | `false` | Remove home when a managed user is deleted. |
| `sysrq` | bool | `false` | Derive `sysrq_always_enabled=1` for REISUB. |

---

## `disks`

Shape:

```json
{
  "disks": {
    "disks": [
      {
        "device": "/dev/nvme0n1",
        "partition_table": "gpt",
        "wipe_disk": false,
        "partitions": []
      }
    ]
  }
}
```

`disks.disks` must contain at least one disk when the section is present.

### Disk layout

| Field | Type | Default | Validation / behavior |
| --- | --- | --- | --- |
| `device` | string | required | Must start with `/dev/`. No automatic target-disk selection. |
| `partition_table` | `gpt` or `msdos` | `gpt` | Partition table type. |
| `wipe_disk` | bool | `false` | **Destructive** whole-disk wipe/repartition intent. |
| `partitions` | list | required | At least one partition. Labels must be unique per disk. |

### Partition

| Field | Type | Default | Validation / behavior |
| --- | --- | --- | --- |
| `label` | string | required | `[A-Za-z0-9_.-]{1,36}`; no whitespace/slashes. |
| `size` | string | required | Unit size (`512MiB`, `1GiB`, `100MB`, etc.), `1%`–`100%`, or `rest`. |
| `filesystem` | enum | required | `ext4`, `btrfs`, `fat32`, `swap`, `xfs`. |
| `partition_type` | enum | `linux` | `esp`, `linux`, `linux-swap`, `lvm`. |
| `mountpoint` | string or null | `null` | `/`, `/boot`, `/home`, etc. Btrfs roots may mount via subvolumes. |
| `format` | bool | `true` | Whether an existing partition is formatted. Newly created partitions still need a filesystem. |
| `encrypt` | bool | `false` | LUKS encryption. |
| `luks_name` | string or null | `null` | Required when `encrypt=true`; `[A-Za-z0-9_-]+`. |
| `luks_password` | string or null | `null` | Plaintext passphrase declaration. Prefer a gitignored `$include_line` secret. |
| `luks_keyfile` | string or null | `null` | LUKS key file used instead of `luks_password`. |
| `luks_uuid` | string or null | `null` | Explicit header UUID; when absent dasik can derive one deterministically for creation. `sync` can capture the real UUID. |
| `unlock_keyfile` | string or null | `null` | Extra key used for automatic boot unlock via `rd.luks.key`. |
| `unlock_keydev` | string or null | `null` | Filesystem UUID/device identifier associated with the unlock key file in the current implementation. |
| `unlock_tpm2` | bool | `false` | Enroll/use TPM2 token unlock. |
| `unlock_fido2` | bool | `false` | Enroll/use FIDO2 token unlock. |
| `luks_options` | list[string] | `[]` | Extra verbatim `rd.luks.options` tokens, e.g. `token-timeout=10s`. |
| `mount_options` | list[string] | `[]` | Partition-level mount options. For Btrfs these are merged into subvolume mounts. |
| `btrfs_subvolumes` | list | `[]` | Only valid when `filesystem="btrfs"`. |

`rest` has two structural rules: it may appear on only one partition in a disk and that partition must be the last one.

If `encrypt` is `true`, omitting `luks_name` is a schema error. A `luks_name` containing spaces, `/`, `=` or other non-identifier characters is rejected because it reaches device-mapper and the kernel command line.

### Btrfs subvolume

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Subvolume name such as `@` or `@home`. |
| `mountpoint` | string | required | Mountpoint such as `/` or `/home`. |
| `mount_options` | list[string] | `["compress-force=zstd"]` | Per-subvolume mount options. Explicit `[]` disables that model default. |

A `btrfs_subvolumes` list on a non-Btrfs partition is rejected.

### Disk safety and `sync`

Disk declarations can be the most destructive part of a config. Always inspect `wipe_disk` and `format` before `apply`.

`sync` is intentionally conservative for discovered disks: captured layouts use preservation-oriented values such as `wipe_disk:false` / `format:false`, and secrets such as plaintext LUKS passwords are not reconstructed from the machine.

---

## Time, locale and network

### `timezone`

```json
"timezone": {"region": "Europe", "city": "Madrid"}
```

Both `region` and `city` are required if the object is present.

### `locales`

| Field | Type | Required |
| --- | --- | --- |
| `selected_locales` | list[string] | yes |
| `desired_locale` | string | yes |
| `desired_tty_layout` | string | yes |

Example:

```json
"locales": {
  "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
  "desired_locale": "es_ES.UTF-8",
  "desired_tty_layout": "es"
}
```

### `network` and `hostname`

| Field | Type | Default |
| --- | --- | --- |
| `network.type` | `NetworkManager` or `systemd-networkd` | required when `network` is present |
| `network.add_default_hosts` | bool | `false` |
| `hostname` | string | `""` |

The network action reads root-level `hostname` as well as the optional network block. A hostname can therefore be managed without declaring a network backend.

---

## `users`

Each item:

```json
{
  "username": "andres",
  "hashed_password": "$y$...",
  "shell": "/bin/zsh",
  "groups": ["wheel", "libvirt"]
}
```

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `username` | string | required | Login name; further system tooling may impose its own constraints. |
| `hashed_password` | string | required | Must begin with `$`; plaintext is rejected. Generate with `dasik hash-password`. |
| `shell` | string | `/bin/bash` | Login shell path. |
| `groups` | list[string] | `[]` | Supplementary groups. |

`remove_home_on_delete` is a root-level switch controlling whether deletion of a managed user removes its home.

### User/group preflight

Preflight knows a set of base Arch groups and providers for common non-base groups. If a user requests a known package-created group such as `docker` without a declared provider package, that is an error before mutation. Unknown non-base groups produce a warning because dasik cannot prove who will create them.

The `kvm` feature contributes `libvirt` membership to every declared user through expansion.

---

## `packages`

A package entry is either a real package-name string:

```json
"packages": ["base", "linux", "firefox", "yay"]
```

or an object:

```json
{"name": "sunshine", "reason": "explicit", "optional": true}
```

### Package object

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Real package name. |
| `reason` | `explicit` or `dep` | `explicit` | pacman install reason intent. |
| `optional` | bool | `false` | Failure does not abort the whole convergence; failed optional package is not claimed as installed and is retried later. |

Package origin is resolved automatically (repo/group, explicit `package_sources`, AUR). New configs should use clean names rather than encoding origin in the name. The historical `aur-` prefix remains compatibility input but is deprecated.

### `package_policy`

```json
"package_policy": {"unknown": "warn-and-skip"}
```

`unknown` is either:

- `warn-and-skip` (default): a package proven to exist nowhere is skipped visibly and retried on a later apply;
- `error`: abort on such a name.

A lookup source that is unreachable is different from a confirmed unknown package and remains a blocking failure.

### `package_sources`

Map package name to a pinned Git PKGBUILD source:

```json
"package_sources": {
  "config-saver": {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd",
    "subdir": "."
  }
}
```

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `type` | literal `pkgbuild-git` | required | Only source type implemented. |
| `url` | string | required | Must use `https://`, host `github.com`, and end in `.git`. |
| `ref` | string | required | Exactly 40 hexadecimal characters (full SHA-1 commit id). |
| `subdir` | string | `.` | Relative path; cannot escape clone root with `..`. |

Every `package_sources` key must itself be a valid package name and must also appear in `packages`; otherwise validation fails.

---

## `drivers`

`drivers` is a list of strings. The model itself does not restrict the strings, but expansion currently knows these effective keys:

| Driver | Derived base packages |
| --- | --- |
| `nvidia` | `nvidia`, `nvidia-utils`, `nvidia-settings` |
| `nvidia-open` | `nvidia-open`, `nvidia-utils`, `nvidia-settings` |
| `nouveau` | `mesa`, `vulkan-nouveau` |
| `intel` | `mesa`, `vulkan-intel`, `intel-media-driver` |
| `amd` | `mesa`, `vulkan-radeon`, `libva-mesa-driver` |

When `pacman.multilib=true`, matching `lib32-*` driver packages are added. An unrecognised driver string validates but contributes no driver packages, so treat the table above as the supported set.

`hardware_acceleration` also uses the driver list to choose additional VA-API/diagnostic packages.

---

## Boot and kernel

### `bootloader`

Accepted values:

- `grub` (default)
- `sd-boot`
- `systemd-boot` (accepted alias)

Both implemented install paths are UEFI-oriented. For an installation config containing `disks`, preflight errors if the installer environment is not booted in EFI mode; this prevents an apparently successful bootloader install that firmware could never start.

For sd-boot, dasik manages the main and fallback/rescue entries and enables systemd's `systemd-boot-update.service` through expansion.

### `initramfs`

- `mkinitcpio` (default)
- `dracut`

Selecting dracut derives the `dracut` package and causes dasik to neutralize mkinitcpio regeneration hooks so the two generators do not clobber each other's output.

### `kernel_cmdline`

`list[string]` of parameters you explicitly want to own, for example:

```json
"kernel_cmdline": ["quiet", "resume=/dev/mapper/cryptswap", "intel_iommu=on"]
```

Disk/LUKS boot parameters, CPU pstate parameters and `sysrq_always_enabled=1` can be derived from their higher-level blocks. Do not duplicate derived policy unless you intentionally want an explicit override.

Some LUKS keys (`rd.luks.name`, `rd.luks.key`, `rd.luks.options`) are repeatable per device; ordinary single-valued kernel arguments use one effective value.

---

## `systemd`

```json
"systemd": {
  "enable_units": ["sshd.service", "reflector.timer"],
  "enable_sockets": ["cups.socket"],
  "disable_units": []
}
```

| Field | Type | Default |
| --- | --- | --- |
| `enable_units` | list[string] | `[]` |
| `enable_sockets` | list[string] | `[]` |
| `disable_units` | list[string] | `[]` |

A unit/socket may not also appear in `disable_units`; overlap is a schema error.

Preflight has stronger checks for display managers: a known display-manager unit without a declared provider package is an error, and enabling multiple display managers is an error. Other known unit/provider mismatches normally warn because a dependency may still provide the unit.

---

## `pacman`

```json
"pacman": {
  "options": {
    "Parallel": true,
    "Color": true,
    "VerbosePkgLists": false
  },
  "multilib": true
}
```

| Field | Type | Default |
| --- | --- | --- |
| `options.Parallel` | bool | `true` |
| `options.Color` | bool | `true` |
| `options.VerbosePkgLists` | bool | `false` |
| `multilib` | bool | `false` |

---

## Managed local files

### Directory snippet fields

These fields all contain `FileEntry` items:

```json
{"name": "99-example.conf", "content": "verbatim\ncontent\n"}
```

| Field | Destination directory |
| --- | --- |
| `udev_rules` | `/etc/udev/rules.d` |
| `modprobe_conf` | `/etc/modprobe.d` |
| `modules_load` | `/etc/modules-load.d` |
| `sysctl_d` | `/etc/sysctl.d` |
| `tmpfiles_d` | `/etc/tmpfiles.d` |
| `sddm_conf_d` | `/etc/sddm.conf.d` |
| `profile_d` | `/etc/profile.d` |

`name` must be non-empty and may not contain `/`. `content` is verbatim text.

### `etc_environment`

A list of lines for `/etc/environment`:

```json
"etc_environment": ["EDITOR=nvim", "MOZ_ENABLE_WAYLAND=1"]
```

### `files`

Arbitrary absolute managed files:

```json
{
  "path": "/etc/wireguard/wg0.conf",
  "content": "[Interface]\n...",
  "mode": "0600"
}
```

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `path` | string | required | Must be absolute and contain no `..` path segment. |
| `content` | string | required | Verbatim. |
| `mode` | string or null | `null` | Octal string such as `0600`; parsed as base 8. |

`sync` can discover several categories of local `/etc` state. WireGuard and NetworkManager WireGuard keyfiles are captured with `0600`; those files contain private keys, so a synced JSON may itself be secret.

---

## `zram`

An arbitrary nested mapping matching sections/options of `/etc/systemd/zram-generator.conf`:

```json
"zram": {
  "zram0": {
    "zram-size": "min(ram / 2, 8192)",
    "swap-priority": 100
  }
}
```

A non-empty `zram` block derives the `zram-generator` package.

---

## `sudo`

```json
"sudo": {
  "wheel": true,
  "nopasswd": false,
  "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"]
}
```

| Field | Type | Default |
| --- | --- | --- |
| `wheel` | bool | `true` |
| `nopasswd` | bool | `false` |
| `rules` | list[string] | `[]` |

Rules must be non-empty, single-line strings. `@include` and `#include` directives are rejected so the owned fragment remains self-contained.

An explicit `sudo` block without a declared package that provides `sudo`/`visudo` is a preflight error. Even without an explicit block, a declared user in `wheel` triggers an implicit password-protected wheel sudo rule; if no sudo provider is declared, preflight warns.

---

## `cpu`

```json
"cpu": {
  "scaling_driver": "auto",
  "mode": "active",
  "power_profiles_daemon": true,
  "governor": null
}
```

| Field | Type | Default | Validation / effect |
| --- | --- | --- | --- |
| `scaling_driver` | enum | `auto` | `auto`, `amd_pstate`, `intel_pstate`, `acpi_cpufreq`, `none`. |
| `mode` | enum | `active` | `active`, `guided`, `passive`, `disable`. |
| `power_profiles_daemon` | bool | `true` | Derives package + service. |
| `governor` | string or null | `null` | Plain lowercase/underscore identifier; derives `cpupower`, service and `/etc/default/cpupower`. |

Restrictions:

- `amd_pstate` + `mode=disable` is rejected; use `scaling_driver="none"`.
- `intel_pstate` accepts only `active`, `passive`, `disable`; `guided` is rejected.
- A governor containing characters outside `[a-z_]` is rejected.
- `power_profiles_daemon=true` with a fixed governor warns because both compete for policy.
- `power_profiles_daemon=true` with package `tlp` is a preflight error.

CPU pstate kernel arguments are high-level derived policy and are captured back into the `cpu` block rather than left as raw `kernel_cmdline` noise.

---

## `reflector`

```json
"reflector": {
  "countries": ["ES"],
  "protocols": ["https"],
  "latest": 20,
  "sort": "rate",
  "save": "/etc/pacman.d/mirrorlist"
}
```

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `countries` | list[string] | `[]` | Country names use letters plus spaces/`.`/`'`/`-`; no newline/flag injection. |
| `protocols` | list[enum] | `["https"]` | `https`, `http`, `rsync`, `ftp`. |
| `latest` | integer or null | `20` | When integer, must be at least 1. `null` means do not emit `--latest`. |
| `sort` | enum | `rate` | `rate`, `age`, `score`, `delay`, `country`. |
| `save` | string | `/etc/pacman.d/mirrorlist` | Absolute, single-line path. |

Declaring the block derives the `reflector` package, `reflector.timer`, and `/etc/xdg/reflector/reflector.conf`.

---

## Feature blocks

### `bluetooth`

| Field | Type | Default | Effect |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Derives selected package + `bluez-utils` + `bluetooth.service`. |
| `package` | string | `bluez` | Bluetooth package to install. |
| `in_initramfs` | bool | `false` | Include Bluetooth stack in dracut initramfs for early input/unlock. |

### `hardware_acceleration`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `install_codecs` | bool | `true` |

When enabled, expansion adds common VA-API/VDPAU utilities and driver-specific packages. The current expansion uses `drivers`; `install_codecs` is part of the declared model but does not currently select a separate codec bundle in the shown expansion logic.

### `kvm`

```json
"kvm": {"install": true}
```

`install` defaults to `false`. Enabling it derives the QEMU/libvirt stack, `libvirtd.service`, `virtlogd.service`, a nested-virtualization modprobe file, and the `libvirt` group for every declared user.

### `cups`

```json
"cups": {"install": true}
```

`install` defaults to `false`. Enabling it derives CUPS/scanning packages and `cups.socket`.

### `microsoft_fonts`

| Field | Type | Default |
| --- | --- | --- |
| `install` | bool | `false` |
| `source_iso` | string or null | `null` |

`source_iso` is the Windows ISO path used by the fonts action.

### `firewall`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `remove_services` | list[string] | `[]` |
| `rich_rules` | list[string] | `[]` |
| `allowed_services` | list[string] | `[]` |

When enabled, expansion derives `firewalld` + `firewalld.service`; the firewall action owns public-zone policy. Rich-rule parsing is intentionally lossless/conservative: rules dasik cannot represent exactly are rejected rather than silently widened.

### `wireguard`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `interface_name` | string | `wg0` |
| `config_content` | string or null | `null` |

When enabled, expansion derives `wireguard-tools`, `wg-quick@<interface>.service` and `/etc/wireguard/<interface>.conf`.

If the config contains private keys, protect the JSON (or split the secret material into a private fragment where appropriate).

### `snapper`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `configs` | list | `[{"name":"root","subvolume":"/"}]` |

Each config has required `name` and `subvolume` strings. When enabled, expansion adds `snapper`, `snap-pac`, `snapper-timeline.timer` and `snapper-cleanup.timer`; the dedicated action creates/captures the snapper configs and is ordered before the normal package transaction so snap-pac can protect later transactions.

---

## Simple bool toggles

### `enable_trim`

Default `false`. Derives `fstrim.timer`; disk/LUKS handling may also derive discard-related boot/mount policy where applicable.

### `enable_microcode`

Default `false`. Causes CPU vendor microcode to be installed/included in the boot path.

### `remove_home_on_delete`

Default `false`. Alters user deletion semantics.

### `sysrq`

Default `false`. When true, derives `sysrq_always_enabled=1` on the kernel command line. `sync` captures that policy back as `sysrq` rather than duplicating the raw token in `kernel_cmdline`.

---

## Cross-field preflight checks worth knowing

After model validation, `check`, `plan` and `apply` run preflight on the **expanded** config. That matters because packages/units contributed by feature blocks count as declared.

Important checks include:

- known non-base user group with no declared provider package → **error**;
- unknown non-base user group → warning;
- known display-manager unit with no provider package → **error**;
- more than one display manager enabled → **error**;
- display-manager-specific config files without that display manager → warning;
- explicit `sudo` block without a sudo provider → **error**;
- wheel user without sudo provider and no explicit block → warning;
- `power-profiles-daemon` + fixed governor → warning;
- `power-profiles-daemon` + `tlp` → **error**;
- unknown `/etc/crypttab` option → **error**;
- crypttab entry targeting an undeclared device → warning, or **error** when `swap` would reformat that undeclared device;
- install config with an EFI bootloader while the installer is not booted in UEFI mode → **error**.

Warnings do not block; errors abort before mutation.

## High-level feature expansion

Several convenient blocks do not own a unique low-level domain themselves. Instead `expand_config()` contributes resources to shared domains before plan/apply. Examples:

- `bluetooth` → packages + systemd unit;
- `kvm` → packages + units + modprobe file + user group;
- `cups` → packages + socket;
- `wireguard` → package + unit + file;
- `firewall` → package + unit, while its action handles rules;
- `snapper` → packages + timers, while its action handles snapper configs;
- `drivers` → GPU packages;
- `initramfs=dracut` → `dracut` package;
- `cpu` → packages/units/files plus derived pstate kernel argument;
- `reflector` → package + timer + config file;
- `bootloader=sd-boot` → `systemd-boot-update.service`;
- `sysrq=true` → kernel argument.

`sync` subtracts resources already attributable to these declarations so captured JSON does not contain both the high-level block and duplicate low-level packages/units/files.

See [Workflows and state](workflows.md) for that ownership model and [Config splitting and secrets](config-splitting.md) for keeping large/secret values out of one monolithic JSON.