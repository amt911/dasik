# Configuration reference

Every field dasik understands, as defined by `dasik/lib/models/`. One JSON
object at the root; **every section is optional**. A config can be as small as:

```json
{ "packages": ["htop"] }
```

Deep dives live on their own pages — [Disks](Disks.md), [Boot](Boot.md),
[Packages](Packages.md), [Features](Features.md) — this page is the index of
fields.

> **Unknown keys are ignored, not rejected.** The models do not set
> `extra="forbid"`, so `"hostnamee": "x"` validates fine and does nothing at
> all. If a block seems to have no effect, check the spelling here first. Same
> for `drivers`: an unrecognised driver name is a deliberate no-op rather than a
> guess at the wrong package.

---

## Root fields

### Identity and basics

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `hostname` | string | `""` | `/etc/hostname` + the `127.0.1.1` line. Empty ⇒ the whole network action no-ops. RFC-1123 validated on apply. |
| `metadata` | object | `null` | Free-form. dasik never reads it. Use it for a note about the machine. |
| `notes` | string | `null` | Same, as prose. |

### System locale and time

| Field | Type | Required inside | Writes |
| --- | --- | --- | --- |
| `locales` | object | `selected_locales`, `desired_locale`, `desired_tty_layout` | `/etc/locale.gen`, `/etc/locale.conf`, `/etc/vconsole.conf` |
| `timezone` | object | `region`, `city` | `/etc/localtime` symlink into `/usr/share/zoneinfo` |

```json
"locales": {
  "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
  "desired_locale": "en_US.UTF-8",
  "desired_tty_layout": "es"
},
"timezone": { "region": "Europe", "city": "Madrid" }
```

`selected_locales` entries are **locale.gen lines** (locale + charset), while
`desired_locale` is the `LANG=` value and `desired_tty_layout` the `KEYMAP=`.
`city` may carry a subpath: `{"region": "America", "city": "Argentina/Buenos_Aires"}`.
Each path component must be a plain zoneinfo token, so a value can never turn
`/etc/localtime` into a traversal.

The domain is a **record**: any drift in the three values produces one `~ modify`.

### Network

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `network.type` | `"NetworkManager"` \| `"systemd-networkd"` | *(required if the block exists)* | validated on apply; not part of the drift record (it has no file of its own) |
| `network.add_default_hosts` | bool | `false` | writes the standard `localhost` block into `/etc/hosts` |

Declaring `network` does **not** install NetworkManager. Put `networkmanager` in
`packages` and `NetworkManager.service` in `systemd.enable_units`.

### Users

`users` is a list. Passwords are **always** hashes — a plaintext value is
rejected by the schema.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `username` | string | — | must match `[a-z_][a-z0-9_-]*\$?` |
| `hashed_password` | string | — | must start with `$`; generate with `dasik hash-password` |
| `shell` | string | `/bin/bash` | |
| `groups` | list of strings | `[]` | supplementary groups |

```json
"users": [
  { "username": "root", "hashed_password": "$y$j9T$…" },
  { "username": "andres", "hashed_password": "$y$j9T$…",
    "shell": "/bin/zsh", "groups": ["wheel", "libvirt"] }
]
```

- **`root` may declare a password and nothing else.** dasik runs `usermod -p`
  for root and never `useradd`/`usermod -s`/`-G`, so a shell or group list would
  be accepted and silently ignored — the schema rejects it instead.
- Regular users are reconciled by set math on `uid >= 1000`: create, delete,
  and modify on shell/groups/password drift.
- A group nothing creates makes `useradd -G` fail *after* the disk is wiped, so
  [preflight](Validation.md#group_without_provider) checks it up front.
- Being in `wheel` grants nothing by itself on Arch — see [`sudo`](#sudo-in-detail).

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `remove_home_on_delete` | bool | `false` | root-level flag: delete a removed user's home too |

### Packages

Full treatment: **[Packages](Packages.md)**.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `packages` | list of string \| object | `[]` | real names — **no `aur-` prefix**; the source is resolved automatically |
| `package_policy.unknown` | `"warn-and-skip"` \| `"error"` | `"warn-and-skip"` | what to do with a name that exists nowhere |
| `package_sources` | map name → object | `{}` | Git PKGBUILD for packages outside repos and AUR |

The object form of a package entry:

```json
{ "name": "linux-headers", "reason": "dep", "optional": false }
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | — | Arch package name grammar `[a-zA-Z0-9][a-zA-Z0-9@._+-]*` |
| `reason` | `"explicit"` \| `"dep"` | `"explicit"` | pacman install reason; `dep` lets it be orphan-pruned |
| `optional` | bool | `false` | its failure must not abort the apply; it is then left **out** of the manifest so the divergence stays visible and the next apply retries |

`package_sources` entries (`type` is currently always `pkgbuild-git`):

```json
"package_sources": {
  "config-saver": {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver.git",
    "ref": "3f2b1c0d4e5f60718293a4b5c6d7e8f901234567",
    "subdir": "."
  }
}
```

`url` must be `https://github.com/….git`, `ref` a **full 40-char commit SHA**
(reproducible builds), `subdir` relative and non-escaping. Every key must also
appear in `packages` — a source nobody declares would never be built, so the
schema rejects it.

### Disks

Full treatment: **[Disks and encryption](Disks.md)**.

```json
"disks": { "disks": [ { "device": "/dev/nvme0n1", "partitions": [ … ] } ] }
```

Note the doubled key: the block is an object with one `disks` list inside it.

### Boot

Full treatment: **[Boot chain](Boot.md)**.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `bootloader` | `"grub"` \| `"sd-boot"` \| `"systemd-boot"` | `"grub"` | `systemd-boot` is an accepted alias of `sd-boot`. Both loaders are EFI-only. |
| `initramfs` | `"mkinitcpio"` \| `"dracut"` | `"mkinitcpio"` | choosing dracut also neutralises mkinitcpio's pacman hooks |
| `enable_microcode` | bool | `false` | adds `amd-ucode`/`intel-ucode` and wires the initrd into the boot entry |
| `kernel_cmdline` | list of strings | `[]` | **extra** parameters; dasik derives the LUKS/cpu/sysrq/splash ones itself |
| `sysrq` | bool | `false` | derives `sysrq_always_enabled=1` (the old installer's REISUB) |
| `plymouth` | object | `null` | boot splash; an absent block means no splash at all |
| `plymouth.theme` | string | `null` | `[A-Za-z0-9_.-]{1,64}`; unset keeps plymouth's own default |

### Files dropped into `/etc`

Seven directory sections, each a list of `{name, content}` where `name` is a
filename with **no** path separator:

| Field | Directory |
| --- | --- |
| `udev_rules` | `/etc/udev/rules.d` |
| `modprobe_conf` | `/etc/modprobe.d` |
| `modules_load` | `/etc/modules-load.d` |
| `sysctl_d` | `/etc/sysctl.d` |
| `tmpfiles_d` | `/etc/tmpfiles.d` |
| `sddm_conf_d` | `/etc/sddm.conf.d` |
| `profile_d` | `/etc/profile.d` |

Plus two free-form ones:

| Field | Type | Notes |
| --- | --- | --- |
| `etc_environment` | list of strings | lines appended to `/etc/environment` |
| `files` | list of objects | arbitrary absolute paths |

A `files` entry:

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `path` | string | — | absolute, no `..` segment |
| `content` | string | — | verbatim |
| `mode` | octal string | `null` | e.g. `"0600"`. Required in practice for secrets: NetworkManager and `wg-quick` **ignore** world-readable keyfiles. |

```json
"files": [
  { "path": "/etc/wireguard/wg0.conf", "content": "[Interface]\n…", "mode": "0600" }
]
```

Long bodies belong in real files — see [Config splitting](Config-splitting.md):

```json
{ "path": "/etc/pam.d/sudo", "content": { "$include_text": "parts/pam-sudo" } }
```

`/etc/crypttab` is special: when the generator is dracut **and** encryption is
declared, the dracut backend owns that file exclusively and `files` yields it,
so the two never fight over alternating applies.

### systemd units

| Field | Type | Default |
| --- | --- | --- |
| `systemd.enable_units` | list of strings | `[]` |
| `systemd.enable_sockets` | list of strings | `[]` |
| `systemd.disable_units` | list of strings | `[]` |

A unit that appears in both an enable list and `disable_units` is a schema
error. Feature blocks contribute their own units (see [Features](Features.md)) —
you do not need to list `bluetooth.service` next to `"bluetooth": {"enable": true}`.

### `/etc/systemd/*.conf`

Three mappings, each writing one section of one pacman-owned file — dasik writes
a `<conf>.d/10-dasik.conf` drop-in (systemd's supported override mechanism)
rather than editing the packaged file, and reads back the **effective**
configuration.

| Field | File | Section |
| --- | --- | --- |
| `oomd` | `/etc/systemd/oomd.conf` | `[OOM]` |
| `systemd_system_conf` | `/etc/systemd/system.conf` | `[Manager]` |
| `systemd_user_conf` | `/etc/systemd/user.conf` | `[Manager]` |

```json
"oomd": { "DefaultMemoryPressureDurationSec": "20s" },
"systemd_system_conf": { "DefaultTimeoutStopSec": "15s" }
```

Keys must be systemd directive names (`[A-Za-z][A-Za-z0-9]*`); values must be a
string or a number, and may not contain a line break — a newline would smuggle
in a directive nobody declared. A declared `oomd` block also enables
`systemd-oomd.service`.

### zram

```json
"zram": { "zram0": { "zram-size": "ram / 2", "compression-algorithm": "zstd" } }
```

`{device: {option: value}}`, mirroring `/etc/systemd/zram-generator.conf`.
Declaring it pulls in `zram-generator`. Comparison is semantic (canonical ini),
so whitespace differences do not produce phantom changes.

### Feature blocks

Each is optional; each derives packages, units and files. Full table of what
each one pulls in: **[Features](Features.md)**.

| Field | Shape | Turns on |
| --- | --- | --- |
| `bluetooth` | `{enable, package="bluez", in_initramfs=false}` | bluez + `bluetooth.service`; `in_initramfs` puts the BT stack in the initramfs so a paired keyboard works at the LUKS prompt (dracut) |
| `cups` | `{install}` | cups, cups-pdf, system-config-printer, sane, sane-airscan + `cups.socket` |
| `kvm` | `{install}` | the QEMU/libvirt stack, `libvirtd`/`virtlogd`, nested-virt modprobe conf, `libvirt` group for every user |
| `firewall` | `{enable, allowed_services, remove_services, rich_rules}` | firewalld + the public zone rules |
| `wireguard` | `{enable, interface_name="wg0", config_content}` | wireguard-tools, `wg-quick@<iface>.service`, `/etc/wireguard/<iface>.conf` |
| `snapper` | `{enable, configs=[{name,subvolume}]}` | snapper + snap-pac + timeline/cleanup timers |
| `hardware_acceleration` | `{enable, install_codecs=true}` | VA-API/VDPAU tools, plus per-driver extras from `drivers` |
| `microsoft_fonts` | `{install, source_iso}` | extracts the fonts from a Windows ISO |
| `cpu` | `{scaling_driver, mode, power_profiles_daemon, governor}` | scaling driver kernel param, power-profiles-daemon and/or cpupower |
| `reflector` | `{countries, protocols, latest, sort, save}` | reflector + `reflector.timer` + `/etc/xdg/reflector/reflector.conf` |
| `sudo` | `{wheel=true, nopasswd=false, rules}` | `/etc/sudoers.d/10-dasik` |
| `pacman` | `{options:{Parallel,Color,VerbosePkgLists}, multilib}` | `/etc/pacman.conf` |
| `plymouth` | `{theme}` | plymouth + `splash` + the initramfs hook |

Scalar toggles:

| Field | Type | Default | Effect |
| --- | --- | --- | --- |
| `enable_trim` | bool | `false` | enables `fstrim.timer` |
| `drivers` | list of strings | `[]` | GPU drivers: `nvidia`, `nvidia-open`, `nouveau`, `intel`, `amd` |

### `cpu` in detail

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `scaling_driver` | `auto`\|`amd_pstate`\|`intel_pstate`\|`acpi_cpufreq`\|`none` | `auto` | `auto` reads the vendor from `/proc/cpuinfo` |
| `mode` | `active`\|`guided`\|`passive`\|`disable` | `active` | `guided` is AMD-only; `intel_pstate` accepts active/passive/disable |
| `power_profiles_daemon` | bool | `true` | installs + enables `power-profiles-daemon.service` |
| `governor` | string | `null` | a cpupower governor, e.g. `performance`; plain identifier only |

Declaring both `power_profiles_daemon` and a fixed `governor` gets a
[warning](Validation.md#ppd_and_governor--ppd_and_tlp) — they fight over the same policy.
`tlp` alongside power-profiles-daemon is an **error**.

### `reflector` in detail

| Key | Type | Default |
| --- | --- | --- |
| `countries` | list of strings | `[]` |
| `protocols` | list of `https`/`http`/`rsync`/`ftp` | `["https"]` |
| `latest` | int ≥ 1 or null | `20` |
| `sort` | `rate`\|`age`\|`score`\|`delay`\|`country` | `"rate"` |
| `save` | absolute path | `/etc/pacman.d/mirrorlist` |

### `sudo` in detail

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `wheel` | bool | `true` | the `%wheel` rule — Arch ships it commented out, so without this the group grants nothing |
| `nopasswd` | bool | `false` | wheel sudo without a password prompt |
| `rules` | list of strings | `[]` | extra sudoers lines, verbatim, one line each; `@include`/`#include` are refused |

The fragment is written to `/etc/sudoers.d/10-dasik` and **validated with
`visudo` before it is installed** — a broken fragment would break sudo for
everyone on the machine.

### `snapper` in detail

```json
"snapper": { "enable": true, "configs": [{ "name": "root", "subvolume": "/" }] }
```

Defaults to one config: `root` → `/`. The action runs *before* the package
transaction, because snap-pac's pacman hooks snapshot each transaction and the
config has to exist first — otherwise the entire install happens unprotected.

---

## Validation layers

| Layer | What it catches | When |
| --- | --- | --- |
| JSON parse | syntax | `check`, `plan`, `apply`, `sync` |
| include resolution | missing fragment, cycle, absolute or `..` path | same |
| pydantic (`JsonModel`) | field shape, enums, regex, cross-field rules inside one model | same |
| [preflight](Validation.md) | coherence across blocks, on the **expanded** config | `check`, `plan`, `apply` — **not** `sync` |

Notable schema-level rules, all of which fail the config rather than the install:

- a partition with `encrypt: true` and no `luks_name`;
- more than one partition sized `rest`, or `rest` not last;
- duplicate partition labels on one disk;
- `btrfs_subvolumes` on a non-btrfs partition;
- a device path not starting with `/dev/`;
- a `package_sources` key not declared in `packages`;
- a unit both enabled and disabled;
- a plaintext `hashed_password`;
- `root` declaring a shell or groups;
- a sudoers rule spanning multiple lines.

## The full annotated example

`docs/config-reference.md` in the repository carries a single annotated JSON
containing **every** field at once, generated by introspecting the pydantic
models, and validated with `dasik check`. Use it when you want to see the whole
shape in one place; use this page when you want to look one thing up.
