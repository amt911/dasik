# dasik config reference — every option

The full set of fields dasik accepts in a config JSON, generated from the pydantic
models in [`dasik/lib/models/`](../dasik/lib/models/) (the source of truth — if
this doc and the models ever disagree, the models win).

**Everything is optional.** A config is a set of independent sections; include only
what you want dasik to manage. `dasik plan <config>` shows what a section would
change, `dasik apply` converges it, `dasik sync` captures many of them back from a
running system. Unknown top-level keys are ignored; unknown keys *inside* a modeled
section are rejected by validation.

Legend: **type** as in the model, **default** (`—` = required when the section is
present), and whether `sync` captures it.

---

## Splitting a config across files

A config that manages a real machine is mostly two things: a long package list and
a handful of verbatim file bodies escaped into single JSON lines. Both read badly
inline, so any value may be replaced by one of three directives, resolved **before**
validation:

| Directive | Becomes |
| --- | --- |
| `{"$include": "path.json"}` | the parsed JSON of that file (object, list, string…) |
| `{"$include_text": "path.conf"}` | that file's contents, as a string (verbatim, trailing newline included) |
| `{"$include_line": "secrets/hash"}` | that file's first line, stripped — for secrets |
| `{"$concat": [ ... ]}` | the lists inside it, flattened into one |

```json
{
  "hostname": "archlinux-p14s",
  "packages": {"$concat": [
    {"$include": "packages-base.json"},
    {"$include": "packages-desktop.json"}
  ]},
  "disks": {"$include": "disks.json"},
  "files": [
    {"path": "/etc/pam.d/sudo", "content": {"$include_text": "parts/pam-sudo"}}
  ]
}
```

A working example lives in [`config/split-example/`](../config/split-example/) —
`dasik check config/split-example/main.json` assembles and validates it.

Rules, each of them there so a config cannot quietly load something its reader
does not see:

* Paths are **relative to the file that names them**, so a directory of fragments
  moves as a unit. Absolute paths and `..` are refused.
* A directive must be the **only key** in its object.
* Include cycles are reported, but including the same fragment from two places is
  fine.
* `$include_text` never parses: the file arrives verbatim, newlines and all.

**Secrets.** Use `$include_line`, not `$include_text`: the latter is verbatim by
design (a PAM file needs its final newline), and `usermod -p '$y$…\n'` sets a hash
nobody can log in with while nothing complains.

```json
"hashed_password": {"$include_line": "secrets/hashed-password"},
"luks_password":   {"$include_line": "secrets/luks-passphrase"}
```

Referencing one secret file from two places is the point: both encrypted
partitions read the same passphrase, so root and swap cannot drift apart and
systemd's initrd password cache asks once. `.gitignore` keeps
`config/*/secrets/*` out of the repo and tracks only the `.example` next to it.

Two ready-made splits live in the repo:
[`config/test-config-split/`](../config/test-config-split/) (the tracked sample,
427 lines → a 97-line `main.json` plus 18 fragments) and
[`config/laptop-p14s-split/`](../config/laptop-p14s-split/) (the ThinkPad config,
with its hash and passphrase in `secrets/`). `tests/lib/test_split_configs.py`
asserts each one assembles to exactly its single-file counterpart, so the two
forms cannot drift.

**`sync` refuses a config assembled this way** — it rewrites the file it is given
and would flatten the split into one document. Sync a scratch copy and fold the
result back by hand.

---

## Top-level fields at a glance

| Field | Type | What it manages |
| --- | --- | --- |
| `disks` | object | Partitioning, filesystems, LUKS, btrfs subvolumes |
| `timezone` | object | `/etc/localtime` |
| `locales` | object | `/etc/locale.gen`, `locale.conf`, `vconsole` keymap |
| `network` | object | NetworkManager / systemd-networkd + `/etc/hosts` |
| `hostname` | string | `/etc/hostname` |
| `users` | list | User accounts + groups + shell |
| `packages` | list | pacman + AUR + Git-source packages (real names) |
| `package_policy` | object | What to do with an unknown package (`warn-and-skip` \| `error`) |
| `package_sources` | object | Git PKGBUILD source per package (outside repo/AUR) |
| `drivers` | list | GPU driver selection |
| `bootloader` | string | GRUB or systemd-boot |
| `initramfs` | string | mkinitcpio or dracut |
| `kernel_cmdline` | list | Extra kernel parameters |
| `systemd` | object | Enable/disable units + sockets |
| `pacman` | object | `/etc/pacman.conf` options + multilib |
| `udev_rules`, `modprobe_conf`, `modules_load`, `sysctl_d`, `tmpfiles_d`, `sddm_conf_d`, `profile_d` | list | Local `/etc/*.d` snippet files |
| `etc_environment` | list | `/etc/environment` lines |
| `files` | list | Arbitrary `/etc/...` files (verbatim) |
| `zram` | object | `/etc/systemd/zram-generator.conf` |
| `sudo` | object | `/etc/sudoers.d/10-dasik` — wheel access + extra rules |
| `cpu` | object | CPU scaling driver, power-profiles-daemon, cpupower governor |
| `reflector` | object | `/etc/xdg/reflector/reflector.conf` + `reflector.timer` |
| `bluetooth`, `hardware_acceleration`, `kvm`, `cups`, `microsoft_fonts`, `firewall`, `wireguard`, `snapper` | object | Feature toggles |
| `enable_trim`, `enable_microcode`, `remove_home_on_delete`, `sysrq` | bool | Simple toggles |
| `metadata`, `notes` | object / string | Free-form; not applied |

---

## `disks`

`{"disks": {"disks": [ <DiskLayout>, ... ]}}` — a list of disks. **Destructive** when
`wipe_disk`/`format` are on. See [copy-your-config-and-test.md](copy-your-config-and-test.md)
for making a captured layout portable.

### DiskLayout

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `device` | string | — | Target disk, e.g. `/dev/sda`, `/dev/nvme0n1`, `/dev/vda`. No auto-detect. |
| `partition_table` | `gpt` \| `msdos` | `gpt` | |
| `wipe_disk` | bool | `false` | **DESTRUCTIVE** — wipe the whole disk before partitioning. |
| `partitions` | list | — | ≥1 `Partition` (below). |

### Partition

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | string | — | `[A-Za-z0-9_.-]{1,36}`, unique per disk. dasik keys off this, not the partition number. |
| `size` | string | — | `512MiB` / `1GiB` / `100MB` / `50%` / `rest` (fills remainder; only one, must be last). |
| `filesystem` | `ext4` \| `btrfs` \| `fat32` \| `swap` \| `xfs` | — | |
| `partition_type` | `esp` \| `linux` \| `linux-swap` \| `lvm` | `linux` | GPT type. |
| `mountpoint` | string | `null` | e.g. `/`, `/boot`. For a btrfs-with-subvolumes root, leave unset and put mounts on the subvolumes. |
| `format` | bool | `true` | `sync` sets `false` (never reformat on re-apply). A freshly-created partition is formatted regardless. |
| `encrypt` | bool | `false` | LUKS2. Requires `luks_name`. |
| `luks_name` | string | `null` | dm-mapper name (`/dev/mapper/<name>`); `[A-Za-z0-9_-]+`. Use a generic name like `cryptroot`. |
| `luks_password` | string | `null` | Passphrase, **plaintext** in config. Omit → cryptsetup prompts at install. |
| `luks_keyfile` | string | `null` | Path to a key file (instead of a passphrase). |
| `luks_uuid` | string | `null` | Explicit LUKS header UUID. Unset → deterministic UUID (header ↔ cmdline agree). `sync` bakes the real one. |
| `unlock_keyfile` | string | `null` | Key file added as an extra LUKS key for auto boot-unlock (`rd.luks.key`). |
| `unlock_keydev` | string | `null` | FS UUID of the device holding `unlock_keyfile` (e.g. a USB pendrive). |
| `unlock_tpm2` | bool | `false` | Enroll a TPM2 keyslot (passwordless). |
| `unlock_fido2` | bool | `false` | Enroll a FIDO2 token (needs the physical key at enroll **and** boot). |
| `luks_options` | list[str] | `[]` | Extra verbatim `rd.luks.options` tokens (e.g. `token-timeout=10s`). |
| `mount_options` | list[str] | `[]` | Extra mount options for the partition. |
| `btrfs_subvolumes` | list | `[]` | Only for `btrfs` (below). |

### BtrfsSubvolume

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `name` | string | — | e.g. `@`, `@home`. |
| `mountpoint` | string | — | e.g. `/`, `/home`. |
| `mount_options` | list[str] | `["compress-force=zstd"]` | |

---

## System basics

### `timezone`  *(sync ✓)*

| Field | Type | Default |
| --- | --- | --- |
| `region` | string | — (e.g. `Europe`) |
| `city` | string | — (e.g. `Madrid`) |

### `locales`  *(sync ✓)*

| Field | Type | Default |
| --- | --- | --- |
| `selected_locales` | list[str] | — (e.g. `["en_US.UTF-8 UTF-8"]`) |
| `desired_locale` | string | — (e.g. `en_US.UTF-8`) |
| `desired_tty_layout` | string | — (vconsole keymap, e.g. `us`) |

### `network` + `hostname`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `network.type` | `NetworkManager` \| `systemd-networkd` | — | |
| `network.add_default_hosts` | bool | `false` | Write the standard `/etc/hosts` entries. |
| `hostname` | string | `""` | `/etc/hostname`. |

### `users`  *(sync ✓)*

List of accounts:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `username` | string | — | |
| `hashed_password` | string | — | Crypt hash (`$6$…` / `$y$…`); use `dasik hash-password` or `openssl passwd -6`. |
| `shell` | string | `/bin/bash` | |
| `groups` | list[str] | `[]` | Supplementary groups. |

---

## Packages, drivers, boot

### `packages`  *(sync ✓)*

A list; each item is either a **string** (explicitly installed) or an object
`{"name": "...", "reason": "explicit" | "dep", "optional": true | false}`. Use the
**real package name only**
— dasik resolves each name's origin automatically at apply time (configured repo →
pacman group → `package_sources` → AUR), so `firefox`, `yay` and
`claude-desktop-bin` all just work; the same name keeps working if a package later
moves from the AUR into a repo. Names never encode the origin — no `aur-` prefix,
no URLs.

A name found in **no** repo, group, `package_sources` or the AUR is, by default,
**skipped with a visible warning** (yellow console + `[WARNING]` in the log); the
rest install and `apply` exits 0. The skipped name is not recorded as managed and
is retried on the next apply. Set `package_policy.unknown = "error"` to restore a
hard abort instead (useful in CI). A source that could not be *reached* (the AUR
was unreachable) is **always** a blocking error, whatever the policy — dasik does
not know whether the package exists, so it refuses rather than skip.

> The deprecated `aur-<name>` prefix is still accepted (with a warning) for
> configs produced by older syncs; `sync` rewrites it back to the plain name.

#### `optional: true` — a failure that must not stop the install

A package marked `{"name": "sunshine", "optional": true}` may **fail to install
without aborting the apply**. Optional packages are installed in their own batch
*after* the required ones (repo packages one at a time, AUR packages in a separate
build batch), so one broken upstream cannot take the others — or the rest of the
convergence — down with it.

It is not a licence to lie about state: a failed optional package is reported in
red, is **excluded from the manifest** (dasik never claims it is installed), and
the next `plan` lists it again. An unknown optional name is skipped even under
`package_policy.unknown = "error"`. `sync` preserves the flag, since it is intent.

Use it for peripheral software whose source can break independently of your
system: large AUR applications, vendor printer drivers, fonts. Do **not** use it
for anything the machine needs to boot or log in.

```json
"packages": [
  "base", "linux", "plasma-meta",
  {"name": "sunshine", "optional": true},
  {"name": "epsonscan2", "optional": true}
]
```

Why it exists: on 2026-07-19 three peripheral AUR packages out of 311 failed
(one upstream `pkg_resources` transition, two vendor URLs returning HTTP 403).
`yay` installed everything else and exited 1, and that single exit code stopped
the reconciler before users, systemd units, firewall, snapper, the initramfs and
the bootloader — on an already-partitioned disk.

### `package_policy`

`{"unknown": "warn-and-skip" | "error"}` — how to treat a declared package that
resolves to no known source. `warn-and-skip` (default) skips it with a warning and
continues; `error` aborts the whole apply before installing anything.

### `package_sources`  *(sync ✓ — preserved)*

A map of **package name → Git PKGBUILD source**, for packages that live in no
pacman repo/group and no AUR (e.g. your own public GitHub repo). Only needed for
such packages; everything else resolves automatically.

```json
"packages": ["firefox", "config-saver"],
"package_sources": {
  "config-saver": {
    "type": "pkgbuild-git",
    "url": "https://github.com/amt911/config-saver-aur.git",
    "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
  }
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `type` | `"pkgbuild-git"` | Only value for now. |
| `url` | string | HTTPS `github.com` URL ending in `.git` (first version limits host). |
| `ref` | string | **Full 40-char commit SHA** — pins the build for reproducibility. Change it deliberately to update. |
| `subdir` | string | Optional; PKGBUILD subdirectory (default `.`). Must stay inside the clone (no `..`). |

Each key must also appear in `packages`. dasik clones the URL, checks out the
exact `ref` (refusing any other commit), builds the PKGBUILD as an **unprivileged**
user, and verifies the built package's `pkgname` matches the declared name
**before** installing. The applied SHA is tracked, so an unchanged `ref` is a
no-op on re-apply and a **changed `ref` triggers a rebuild** even when the name is
already installed. `sync` keeps `packages` as real names and preserves this map
untouched — it never infers a Git source from a package's metadata. A pinned SHA
protects reproducibility, but a PKGBUILD is still third-party code you must trust.

### `drivers`

`list[str]` — GPU driver selection (e.g. NVIDIA). Expanded into packages + config.

### `bootloader`

`string` — `grub` (default) or `sd-boot` (a.k.a. `systemd-boot`).

On `sd-boot` dasik writes **two** entries: `arch.conf` (the `default`) and
`arch-fallback.conf`, a rescue entry loading `initramfs-linux-fallback.img` when
mkinitcpio built one and the same image as the main entry otherwise (dracut
builds no fallback). Every `kernel_cmdline` parameter is written to both. It also
enables systemd's own `systemd-boot-update.service`, which keeps the loader on
the ESP up to date.

### `initramfs`  *(sync ✓)*

`string` — `mkinitcpio` (default) or `dracut`. Switching to dracut neutralizes
mkinitcpio's pacman hooks automatically.

### `kernel_cmdline`  *(sync ✓)*

`list[str]` — extra kernel parameters appended to the boot entry (e.g.
`intel_iommu=on`). The LUKS `rd.luks.*` params are derived from `disks` — one set
per **encrypted partition**, not just the root one — so they do not belong here.

Two rules worth knowing:

* `rd.luks.name`, `rd.luks.key` and `rd.luks.options` are **repeatable** (one per
  device): an entry you write here is *added* next to the derived ones. Every
  other parameter is single-valued, so yours replaces the derived one
  (`root=`, `resume=`).
* `sync` captures the boot entry's own parameters minus everything dasik derives
  from `disks` (machine-specific UUIDs never enter the config) and minus the
  parameters a block owns — `amd_pstate=`, `intel_pstate=`, `sysrq_always_enabled=1`
  come back as the `cpu` block and the `sysrq` flag, not as raw parameters. What
  is left is what you really set by hand: `resume=`, `quiet`, `intel_iommu=on`.

---

## Services — `systemd`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable_units` | list[str] | `[]` | Services/timers to enable (`foo.service`, `bar.timer`, `getty@.service`). |
| `enable_sockets` | list[str] | `[]` | Sockets to enable. |
| `disable_units` | list[str] | `[]` | Units to ensure disabled. |

---

## `pacman`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `options.Parallel` | bool | `true` | `ParallelDownloads`. |
| `options.Color` | bool | `true` | |
| `options.VerbosePkgLists` | bool | `false` | |
| `multilib` | bool | `false` | Enable the `[multilib]` repo. |

---

## Local `/etc` files  *(sync ✓ — discovers your non-package files)*

Each of these is a `list` of `{"name": "<file>", "content": "<verbatim>"}`, written
into the matching directory. `sync` discovers the files **you** created there
(skipping symlinks and pacman-owned files):

| Field | Directory |
| --- | --- |
| `udev_rules` | `/etc/udev/rules.d` |
| `modprobe_conf` | `/etc/modprobe.d` |
| `modules_load` | `/etc/modules-load.d` |
| `sysctl_d` | `/etc/sysctl.d` |
| `tmpfiles_d` | `/etc/tmpfiles.d` |
| `sddm_conf_d` | `/etc/sddm.conf.d` |
| `profile_d` | `/etc/profile.d` |

Plus:

- **`etc_environment`** *(sync ✓)* — `list[str]`, lines written to `/etc/environment`.
- **`files`** *(sync ✓ for known ones)* — `list` of `{"path": "/etc/...",
  "content": "...", "mode": "0600"?}`, arbitrary absolute-path files written
  verbatim (e.g. `/etc/crypttab`, `/etc/wireguard/wg0.conf`, NetworkManager
  `*.nmconnection`). Optional `mode` is an octal string applied via `chmod` after
  writing — needed for secret keyfiles (wireguard / NetworkManager refuse a
  world-readable one); sync sets `"0600"` on the files it discovers there.

---

## `zram`  *(sync ✓)*

Mirrors `/etc/systemd/zram-generator.conf` as `{device: {option: value}}`:

```json
"zram": { "zram0": { "zram-size": "min(ram / 2, 8192)", "swap-priority": 100 } }
```

Pulls in `zram-generator`.

---

## `sudo`  *(sync ✓)*

Writes `/etc/sudoers.d/10-dasik` (mode `0440`), validated with `visudo -cf`
through a temporary whose name sudo's `#includedir` skips — a fragment that
fails validation never reaches the directory.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `wheel` | bool | `true` | Writes `%wheel ALL=(ALL:ALL) ALL`. |
| `nopasswd` | bool | `false` | Makes the wheel rule `NOPASSWD: ALL`. |
| `rules` | list | `[]` | Extra sudoers lines, verbatim and in order. Single-line only; `@include`/`#include` are rejected. |

```json
"sudo": { "wheel": true, "nopasswd": false, "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"] }
```

**Implicit default:** with no `sudo` block at all, a user declared in `wheel`
still gets the password-protected wheel rule — stock Arch ships `%wheel`
commented out, so the group alone grants nothing. Opt out with
`"sudo": { "wheel": false }`. Declare a package providing sudo (`sudo` or
`base-devel`): preflight errors on an explicit block without one, and warns for
the implicit default.

---

## `cpu`  *(sync ✓)*

CPU frequency scaling — the old installer's `install_cpu_scaler`.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `scaling_driver` | string | `auto` | `auto` \| `amd_pstate` \| `intel_pstate` \| `acpi_cpufreq` \| `none`. `auto` reads the CPU vendor from `/proc/cpuinfo`. |
| `mode` | string | `active` | `active` \| `guided` (AMD only) \| `passive` \| `disable`. |
| `power_profiles_daemon` | bool | `true` | Installs and enables `power-profiles-daemon.service`. |
| `governor` | string | `null` | Pins a cpupower governor (`performance`, …): pulls `cpupower`, writes `/etc/default/cpupower`, enables `cpupower.service`. |

```json
"cpu": { "scaling_driver": "auto", "mode": "active", "power_profiles_daemon": true }
```

The kernel parameter (`amd_pstate=active` / `intel_pstate=active`) is **derived**:
it lands on every loader entry and an explicit `kernel_cmdline` entry for the same
key still wins. `sync` subtracts it from `kernel_cmdline` and rebuilds this block
instead — driver and mode from the live entry, `governor` from
`/etc/default/cpupower`, `power_profiles_daemon` from the unit. A machine with no
pstate parameter and no governor captures no `cpu` block at all (ppd on its own is
already covered by `packages` + `systemd`), and `<driver>=disable` comes back as
`scaling_driver: "acpi_cpufreq"` — the only reason dasik emits it. Preflight warns when `power_profiles_daemon`
and `governor` are both set (ppd owns the energy-performance preference) and
errors on ppd + `tlp`.

---

## `reflector`  *(sync ✓)*

Periodic pacman mirrorlist refresh: installs `reflector`, enables
`reflector.timer`, and writes `/etc/xdg/reflector/reflector.conf`.

| Field | Type | Default |
| --- | --- | --- |
| `countries` | list | `[]` |
| `protocols` | list | `["https"]` (`https` \| `http` \| `rsync` \| `ftp`) |
| `latest` | int | `20` |
| `sort` | string | `rate` (`rate` \| `age` \| `score` \| `delay` \| `country`) |
| `save` | string | `/etc/pacman.d/mirrorlist` |

```json
"reflector": { "countries": ["ES"], "protocols": ["https"], "latest": 20, "sort": "rate" }
```

`sync` reads the conf back (repeated *and* comma-separated `--country` lines, both
`--flag value` and `--flag=value`). A conf with no `--latest` captures
`"latest": null` — defaulting it to 20 would add a filter the machine never had.

---

## Feature toggles

### `bluetooth`  *(sync ✓ for `in_initramfs`)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Installs `bluez`/`bluez-utils` + `bluetooth.service`. |
| `package` | string | `bluez` | |
| `in_initramfs` | bool | `false` | Pull the BT stack into the initramfs (dracut) so a paired BT keyboard works at the early LUKS/FIDO2 prompt. |

### `hardware_acceleration`

| Field | Type | Default |
| --- | --- | --- |
| `enable` | bool | `false` |
| `install_codecs` | bool | `true` |

### `kvm`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | libvirt/QEMU stack + user groups. |

### `cups`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | CUPS + scanning stack + `cups.socket`. |

### `microsoft_fonts`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `install` | bool | `false` | |
| `source_iso` | string | `null` | Path to a Windows ISO to extract fonts from. |

### `firewall`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | firewalld; owns the `public` zone. |
| `allowed_services` | list[str] | `[]` | Services to allow (e.g. `syncthing`). |
| `remove_services` | list[str] | `[]` | Default services to remove (e.g. `ssh`). |
| `rich_rules` | list[str] | `[]` | `firewall-cmd` rich-rule strings. |

Rich rules round-trip **losslessly**: family, source/destination address, service,
port+protocol, protocol value, the action (`accept`/`reject`/`drop`) and its rate
`limit`. A rule dasik cannot represent exactly (`log`, `audit`, `masquerade`,
`NOT` …) is **rejected** with `ConfigValidationError` rather than approximated —
dropping a clause of an access rule (e.g. `accept limit value="2/m"`) would
silently widen it.

### `wireguard`  *(sync ✓ via `files`)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Installs `wireguard-tools` + `wg-quick@<iface>`. |
| `interface_name` | string | `wg0` | |
| `config_content` | string | `null` | Full `/etc/wireguard/<iface>.conf` (holds the private key — keep the config private). |

### `snapper`  *(sync ✓)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Package + timers via the toggle; this creates the configs. |
| `configs` | list | `[{name: "root", subvolume: "/"}]` | Each `{"name": "...", "subvolume": "/abs/path"}`. |

The action runs **before** the package transaction (snap-pac's pacman hooks
snapshot each transaction, so the config must already exist) and installs
`snapper`/`snap-pac` itself if they are not on the target yet. `sync` captures
every config found under `/etc/snapper/configs`.

### Simple bool toggles

| Field | Default | Effect |
| --- | --- | --- |
| `enable_trim` | `false` | `fstrim.timer` for SSDs. |
| `enable_microcode` | `false` | CPU microcode (`amd-ucode`/`intel-ucode`) in the boot entry. |
| `remove_home_on_delete` | `false` | Remove a user's home when the account is removed. |
| `sysrq` | `false` | REISUB: derives `sysrq_always_enabled=1` on the kernel cmdline. `sync` captures it back as this flag (never as a raw `kernel_cmdline` entry), and clears it when the live entry does not carry the parameter. |

---

## Free-form

- **`metadata`** — arbitrary object; stored, not applied.
- **`notes`** — free-text string; not applied.

---

## Complete example (every field)

One config exercising every section — validate a copy with `dasik check`
(this one passes). Sections are independent; delete the ones you don't want.

```json
{
  "timezone": { "region": "Europe", "city": "Madrid" },
  "locales": {
    "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
    "desired_locale": "en_US.UTF-8",
    "desired_tty_layout": "us"
  },
  "network": { "type": "NetworkManager", "add_default_hosts": true },
  "hostname": "archbox",
  "users": [
    { "username": "andres", "hashed_password": "$6$salt$hash",
      "shell": "/bin/zsh", "groups": ["wheel", "docker"] }
  ],
  "packages": [
    "base-devel",
    "docker",
    "openssh",
    "firefox",
    { "name": "linux-headers", "reason": "dep" },
    "yay",
    "config-saver"
  ],
  "package_policy": { "unknown": "warn-and-skip" },
  "package_sources": {
    "config-saver": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/config-saver-aur.git",
      "ref": "a520605367e13ec25db4c3c7e1c4bf46175ba8cd"
    }
  },
  "drivers": ["nvidia"],
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "kernel_cmdline": ["intel_iommu=on", "quiet"],
  "enable_microcode": true,
  "enable_trim": true,
  "sysrq": true,
  "remove_home_on_delete": false,
  "sudo": { "wheel": true, "nopasswd": false,
            "rules": ["andres ALL=(ALL) NOPASSWD: /usr/bin/pacman"] },
  "cpu": { "scaling_driver": "auto", "mode": "active",
           "power_profiles_daemon": true, "governor": null },
  "reflector": { "countries": ["ES"], "protocols": ["https"],
                 "latest": 20, "sort": "rate", "save": "/etc/pacman.d/mirrorlist" },
  "systemd": {
    "enable_units": ["sshd.service", "fstrim.timer"],
    "enable_sockets": ["cups.socket"],
    "disable_units": ["systemd-networkd.service"]
  },
  "pacman": {
    "options": { "Parallel": true, "Color": true, "VerbosePkgLists": false },
    "multilib": true
  },
  "disks": { "disks": [
    { "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": true,
      "partitions": [
        { "label": "esp", "size": "1GiB", "filesystem": "fat32",
          "partition_type": "esp", "mountpoint": "/boot", "format": true },
        { "label": "cryptswap", "size": "4GiB", "filesystem": "swap",
          "partition_type": "linux-swap", "format": true },
        { "label": "root", "size": "rest", "filesystem": "btrfs",
          "partition_type": "linux", "mountpoint": "/", "format": true,
          "encrypt": true, "luks_name": "cryptroot", "luks_password": "CHANGE_ME",
          "unlock_tpm2": false, "unlock_fido2": false,
          "luks_options": ["token-timeout=10s"],
          "mount_options": ["compress-force=zstd:3"],
          "btrfs_subvolumes": [
            { "name": "@",     "mountpoint": "/" },
            { "name": "@home", "mountpoint": "/home" }
          ] }
      ] }
  ] },
  "udev_rules":    [{ "name": "99-mydev.rules",    "content": "SUBSYSTEM==\"usb\", MODE=\"0660\"\n" }],
  "modprobe_conf": [{ "name": "nvidia.conf",       "content": "options nvidia_drm modeset=1\n" }],
  "modules_load":  [{ "name": "virtio.conf",       "content": "virtio\nvirtio_pci\n" }],
  "sysctl_d":      [{ "name": "99-swappiness.conf","content": "vm.swappiness=10\n" }],
  "tmpfiles_d":    [{ "name": "mytmp.conf",        "content": "d /run/mytmp 0755 root root\n" }],
  "sddm_conf_d":   [{ "name": "autologin.conf",    "content": "[Autologin]\nUser=andres\nSession=plasma\n" }],
  "profile_d":     [{ "name": "editor.sh",         "content": "export EDITOR=nvim\n" }],
  "etc_environment": ["EDITOR=nvim", "MOZ_ENABLE_WAYLAND=1"],
  "files": [{ "path": "/etc/crypttab", "content": "swap LABEL=cryptswap /dev/urandom swap,cipher=aes-xts-plain64\n" }],
  "zram": { "zram0": { "zram-size": "min(ram / 2, 8192)", "swap-priority": 100 } },
  "bluetooth": { "enable": true, "package": "bluez", "in_initramfs": true },
  "hardware_acceleration": { "enable": true, "install_codecs": true },
  "kvm": { "install": true },
  "cups": { "install": true },
  "microsoft_fonts": { "install": false, "source_iso": null },
  "firewall": {
    "enable": true,
    "allowed_services": ["syncthing", "samba"],
    "remove_services": ["ssh"],
    "rich_rules": ["rule family=\"ipv4\" source address=\"192.168.1.0/24\" accept"]
  },
  "wireguard": { "enable": true, "interface_name": "wg0",
    "config_content": "[Interface]\nPrivateKey = ...\nAddress = 10.0.0.2/32\n[Peer]\nPublicKey = ...\nEndpoint = vpn.example:51820\n" },
  "snapper": { "enable": true, "configs": [{ "name": "root", "subvolume": "/" }] },
  "metadata": { "author": "andres", "created": "2026-07" },
  "notes": "Free-form notes; not applied."
}
```

---

## Validation: `check`, and what runs before a mutation

`dasik check <config>` validates without touching anything, and **the same
validation now runs inside `plan`, `apply` and `sync`** — a config never reaches a
destructive action unvalidated. Two layers:

1. **Schema** (pydantic `JsonModel`): field types, required keys, package-name and
   path grammar.
2. **Preflight** (cross-field coherence, on the *expanded* config). Errors abort
   before the first mutation; warnings are printed and do not block.

| Code | Level | Meaning |
| --- | --- | --- |
| `group_without_provider` | error | A user needs a supplementary group no declared package creates (e.g. `docker` with only `podman-docker`). `useradd -G` would fail — after the disk was wiped. |
| `unknown_group` | warning | Group is neither a base group nor one dasik knows a provider for. |
| `unit_without_provider` (display manager) | error | `sddm.service` enabled but no `sddm` declared, etc. |
| `unit_without_provider` (other units) | warning | Provider not declared, but it is often present as a dependency. |
| `multiple_display_managers` | error | Two DM units enabled; `display-manager.service` can be only one. |
| `display_manager_config_mismatch` | warning | e.g. `sddm_conf_d` declared while Plasma Login Manager is the enabled DM (it reads `/etc/plasmalogin.conf.d`). |
| `crypttab_bad_option` | error | Not `crypttab(5)` syntax (`size512` instead of `size=512`). |
| `crypttab_undeclared_device` | error / warning | Entry names a device no declared partition provides. **Error** when the entry carries `swap`, which reformats that device on every boot. |
| `sudo_without_provider` | error | A `sudo` block is declared but nothing provides sudo (`sudo` / `base-devel`). |
| `wheel_without_sudo` | warning | A user is in `wheel` with no package providing sudo, so the group grants nothing. |
| `ppd_and_governor` | warning | `cpu.power_profiles_daemon` and `cpu.governor` both set; ppd owns the energy-performance preference. |
| `ppd_and_tlp` | error | power-profiles-daemon and `tlp` both manage power policy and conflict. |

## Generations and a failed apply

`apply` records a generation when it converges. When it fails **part-way** — the
disk is already partitioned, some packages are installed — the progress is still
recorded, as a **partial** generation:

```
$ dasik generations
Generation 4
Generation 5 (current, partial — apply failed part-way)
```

A partial generation:

* claims only the domains of actions that actually completed; domains of failed
  or never-reached actions keep the **previous** manifest's ownership (dasik does
  not know they changed, and forgetting ownership would make a later plan stop
  seeing what it owns);
* is **not** a convergence — the next `plan` still lists what is missing;
* **cannot be rolled back to** (`rollback N` refuses it, and a bare `rollback`
  skips it and picks the last complete generation).

Re-running `apply` after fixing the cause does not redo completed work: every
action derives its plan from the live system (`pacman -Qq`, `lsblk`, files under
`/etc`), so installed packages and converged disks are skipped.

## See also

- [copy-your-config-and-test.md](copy-your-config-and-test.md) — capturing a running
  system with `sync`, making the `disks` block generic, and testing in a VM.
- Sample configs under [`config/`](../config/).
