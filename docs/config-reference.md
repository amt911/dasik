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

## Top-level fields at a glance

| Field | Type | What it manages |
| --- | --- | --- |
| `disks` | object | Partitioning, filesystems, LUKS, btrfs subvolumes |
| `timezone` | object | `/etc/localtime` |
| `locales` | object | `/etc/locale.gen`, `locale.conf`, `vconsole` keymap |
| `network` | object | NetworkManager / systemd-networkd + `/etc/hosts` |
| `hostname` | string | `/etc/hostname` |
| `users` | list | User accounts + groups + shell |
| `packages` | list | pacman + AUR packages |
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
| `bluetooth`, `hardware_acceleration`, `kvm`, `cups`, `microsoft_fonts`, `firewall`, `wireguard`, `snapper` | object | Feature toggles |
| `enable_trim`, `enable_microcode`, `remove_home_on_delete` | bool | Simple toggles |
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
`{"name": "...", "reason": "explicit" | "dep"}`. Prefix a name with `aur-` to build
it from the AUR.

### `drivers`

`list[str]` — GPU driver selection (e.g. NVIDIA). Expanded into packages + config.

### `bootloader`

`string` — `grub` (default) or `sd-boot` (a.k.a. `systemd-boot`).

### `initramfs`  *(sync ✓)*

`string` — `mkinitcpio` (default) or `dracut`. Switching to dracut neutralizes
mkinitcpio's pacman hooks automatically.

### `kernel_cmdline`  *(sync ✓)*

`list[str]` — extra kernel parameters appended to the boot entry (e.g.
`intel_iommu=on`). LUKS `rd.luks.*` params are derived from `disks`, not listed here.

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
- **`files`** *(sync ✓ for known ones)* — `list` of `{"path": "/etc/...", "content": "..."}`,
  arbitrary absolute-path files written verbatim (e.g. `/etc/crypttab`,
  `/etc/wireguard/wg0.conf`).

---

## `zram`  *(sync ✓)*

Mirrors `/etc/systemd/zram-generator.conf` as `{device: {option: value}}`:

```json
"zram": { "zram0": { "zram-size": "min(ram / 2, 8192)", "swap-priority": 100 } }
```

Pulls in `zram-generator`.

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

### `wireguard`  *(sync ✓ via `files`)*

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Installs `wireguard-tools` + `wg-quick@<iface>`. |
| `interface_name` | string | `wg0` | |
| `config_content` | string | `null` | Full `/etc/wireguard/<iface>.conf` (holds the private key — keep the config private). |

### `snapper`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `enable` | bool | `false` | Package + timers via the toggle; this creates the configs. |
| `configs` | list | `[{name: "root", subvolume: "/"}]` | Each `{"name": "...", "subvolume": "/abs/path"}`. |

### Simple bool toggles

| Field | Default | Effect |
| --- | --- | --- |
| `enable_trim` | `false` | `fstrim.timer` for SSDs. |
| `enable_microcode` | `false` | CPU microcode (`amd-ucode`/`intel-ucode`) in the boot entry. |
| `remove_home_on_delete` | `false` | Remove a user's home when the account is removed. |

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
    "firefox",
    { "name": "linux-headers", "reason": "dep" },
    "aur-yay"
  ],
  "drivers": ["nvidia"],
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "kernel_cmdline": ["intel_iommu=on", "quiet"],
  "enable_microcode": true,
  "enable_trim": true,
  "remove_home_on_delete": false,
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
        { "label": "swap", "size": "4GiB", "filesystem": "swap",
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

## See also

- [copy-your-config-and-test.md](copy-your-config-and-test.md) — capturing a running
  system with `sync`, making the `disks` block generic, and testing in a VM.
- Sample configs under [`config/`](../config/).
