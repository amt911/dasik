# Recipes

Working configurations to copy. Every sample referenced here is tracked in the
repository under `config/` and validated with `dasik check`.

| Recipe | Tracked sample |
| --- | --- |
| [Minimal bootable](#minimal-bootable) | `config/vm-minimal.json` |
| [Simple desktop, ext4 + swap](#simple-desktop-ext4--swap) | `config/install-simple.json` |
| [Encrypted btrfs with subvolumes and snapshots](#encrypted-btrfs-with-subvolumes-and-snapshots) | `config/vm-dracut-luks-subvol.json`, `config/vm-btrfs-snapper.json` |
| [Laptop: hibernation, fingerprint, splash, pendrive unlock](#laptop) | `config/laptop-p14s.json` |
| [Workstation with everything on](#workstation) | `config/install-megamix.json` |
| [Day-2: manage the machine you run](#day-2-management) | — |
| [Split across files](#split-across-files) | `config/split-example/`, `config/laptop-p14s-split/` |

---

## Minimal bootable

The smallest thing that boots. GPT, ESP, ext4 root, systemd-boot.

```json
{
  "disks": { "disks": [{
    "device": "/dev/vda", "partition_table": "gpt", "wipe_disk": true,
    "partitions": [
      {"label": "esp",  "size": "512MiB", "filesystem": "fat32",
       "partition_type": "esp",   "mountpoint": "/boot"},
      {"label": "root", "size": "rest",   "filesystem": "ext4",
       "partition_type": "linux", "mountpoint": "/"}
    ]}]},
  "bootloader": "sd-boot",
  "hostname": "dasik-vm",
  "packages": ["base", "linux", "linux-firmware"],
  "timezone": {"region": "Etc", "city": "UTC"},
  "locales": {"selected_locales": ["en_US.UTF-8 UTF-8"],
              "desired_locale": "en_US.UTF-8", "desired_tty_layout": "us"}
}
```

No user is declared, so the machine boots to a root login you cannot use — add a
`users` block before doing this outside a VM with serial autologin.

## Simple desktop, ext4 + swap

```json
{
  "hostname": "archbox",
  "bootloader": "sd-boot",
  "enable_microcode": true,
  "timezone": { "region": "Europe", "city": "Madrid" },
  "locales": { "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
               "desired_locale": "en_US.UTF-8", "desired_tty_layout": "es" },
  "network": { "type": "NetworkManager", "add_default_hosts": true },
  "disks": { "disks": [{
    "device": "/dev/sda", "partition_table": "gpt", "wipe_disk": true,
    "partitions": [
      {"label": "boot", "size": "512MiB", "filesystem": "fat32",
       "partition_type": "esp", "mountpoint": "/boot"},
      {"label": "swap", "size": "8GiB", "filesystem": "swap",
       "partition_type": "linux-swap"},
      {"label": "root", "size": "rest", "filesystem": "ext4",
       "partition_type": "linux", "mountpoint": "/"}
    ]}]},
  "packages": ["base", "base-devel", "linux", "linux-firmware", "sudo",
               "networkmanager", "git", "vim", "htop"],
  "systemd": { "enable_units": ["NetworkManager.service"] },
  "users": [
    { "username": "root", "hashed_password": "$y$…" },
    { "username": "andres", "hashed_password": "$y$…",
      "shell": "/bin/bash", "groups": ["wheel"] }
  ],
  "sudo": { "wheel": true },
  "enable_trim": true,
  "drivers": ["amd"],
  "hardware_acceleration": { "enable": true }
}
```

Note `sudo` in `packages` **and** the `sudo` block: the package provides the
binary, the block writes the rule that makes `wheel` mean anything.

## Encrypted btrfs with subvolumes and snapshots

LUKS on everything but the ESP, btrfs subvolumes, dracut, systemd-boot, snapper
snapshots on every pacman transaction.

```json
{
  "hostname": "arch-crypt",
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "enable_microcode": true,
  "disks": { "disks": [{
    "device": "/dev/nvme0n1", "partition_table": "gpt", "wipe_disk": true,
    "partitions": [
      {"label": "esp", "size": "1GiB", "filesystem": "fat32",
       "partition_type": "esp", "mountpoint": "/boot"},
      {"label": "root", "size": "rest", "filesystem": "btrfs",
       "partition_type": "linux", "mountpoint": null,
       "encrypt": true, "luks_name": "cryptroot",
       "luks_password": { "$include_line": "secrets/luks" },
       "mount_options": ["compress-force=zstd:3", "noatime"],
       "btrfs_subvolumes": [
         {"name": "@",            "mountpoint": "/"},
         {"name": "@home",        "mountpoint": "/home"},
         {"name": "@log",         "mountpoint": "/var/log"},
         {"name": "@pkg",         "mountpoint": "/var/cache/pacman/pkg"},
         {"name": "@.snapshots",  "mountpoint": "/.snapshots"}
       ]}
    ]}]},
  "packages": ["base", "linux", "linux-firmware", "btrfs-progs", "cryptsetup", "sudo"],
  "snapper": { "enable": true, "configs": [{ "name": "root", "subvolume": "/" }] }
}
```

Things to notice:

- the partition's `mountpoint` is **null** — `/` lives on the `@` subvolume, and
  dasik derives `root=/dev/mapper/cryptroot` plus `rootflags=subvol=@,…` from
  that shape;
- `mount_options` on the partition are the base for every subvolume, so the
  compression policy is written once;
- the LUKS UUID is derived deterministically, so a single apply produces a
  bootable entry;
- `@.snapshots` is its own subvolume, which is what snapper expects.

Add TPM2 or a FIDO2 key for passwordless unlock:

```json
"unlock_tpm2": true,
"luks_options": ["token-timeout=10s"]
```

### Pendrive unlock

Enroll an extra keyslot on a USB stick; the passphrase keeps working when the
stick is not there:

```json
"unlock_keyfile": "/crypto_keyfile.bin",
"unlock_keydev": "1234-ABCD",
"unlock_keydev_fs": "vfat"
```

`unlock_keydev` is the **filesystem UUID** of the stick (`lsblk -f`). Without
`unlock_keydev` the key is baked into the initramfs on the unencrypted ESP —
[don't](Disks.md#automatic-unlock).

## Laptop

Hibernation to a swap partition, plymouth splash, fingerprint PAM, CPU scaling,
zram, oomd.

```json
{
  "hostname": "p14s",
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "enable_microcode": true,
  "plymouth": { "theme": "bgrt" },
  "kernel_cmdline": ["resume=/dev/mapper/cryptswap", "quiet"],
  "cpu": { "scaling_driver": "auto", "mode": "active",
           "power_profiles_daemon": true },
  "zram": { "zram0": { "zram-size": "ram / 2",
                       "compression-algorithm": "zstd" } },
  "oomd": { "DefaultMemoryPressureDurationSec": "20s" },
  "bluetooth": { "enable": true, "in_initramfs": true },
  "packages": ["base", "linux", "linux-firmware", "fprintd", "sudo",
               "power-profiles-daemon"],
  "files": [
    { "path": "/etc/pam.d/sudo",
      "content": { "$include_text": "parts/pam-sudo" }, "mode": "0644" }
  ],
  "sudo": { "wheel": true }
}
```

`bluetooth.in_initramfs` is the one that matters on a laptop with a Bluetooth
keyboard and an encrypted disk: without it there is nothing to type the
passphrase with.

## Workstation

`config/install-megamix.json` is the maximal tracked example — hundreds of
packages, KVM, CUPS, firewall, WireGuard, Microsoft fonts, GPU drivers,
multilib, AUR packages and a Git PKGBUILD source. Read it for the shape of a
real config; it is validated on every CI run.

```bash
dasik check config/install-megamix.json
```

## Day-2 management

A config for the machine you are running normally declares **no** `disks` block:

```json
{
  "hostname": "workstation",
  "packages": ["htop", "ripgrep", "fd", "bat"],
  "systemd": { "enable_units": ["sshd.service"] },
  "sysctl_d": [
    { "name": "99-swappiness.conf", "content": "vm.swappiness=10\n" }
  ],
  "firewall": { "enable": true, "allowed_services": ["syncthing"],
                "remove_services": ["ssh"] },
  "reflector": { "countries": ["ES"], "latest": 20, "sort": "rate" }
}
```

```bash
sudo dasik plan  day2.json --target /
sudo dasik apply day2.json --target /
sudo dasik plan  day2.json --target /   # silent
```

The usual starting point is not writing this by hand but capturing it:

```bash
echo '{}' > my-system.json
sudo dasik sync my-system.json --target /
dasik check my-system.json
```

## Split across files

```text
config/laptop/
├── main.json
├── packages-base.json
├── packages-desktop.json
├── disks.json
├── parts/pam-sudo
└── secrets/            ← gitignored
    ├── hashed-password
    └── luks
```

```json
{
  "hostname": "laptop",
  "packages": { "$concat": [ { "$include": "packages-base.json" },
                             { "$include": "packages-desktop.json" } ] },
  "disks": { "$include": "disks.json" },
  "users": [{ "username": "andres",
              "hashed_password": { "$include_line": "secrets/hashed-password" } }],
  "files": [{ "path": "/etc/pam.d/sudo",
              "content": { "$include_text": "parts/pam-sudo" } }]
}
```

```bash
dasik check config/laptop/main.json     # assembles, then validates
```

Details and rules: [Config splitting](Config-splitting.md).

---

## Keeping `/etc/ssh` (and friends) with config-saver

The question this answers: *my machine has accumulated an ssh setup — how do I
carry it to the next one?*

```json
{
  "packages": ["base", "linux", "linux-firmware", "openssh", "config-saver"],
  "package_sources": {
    "config-saver": {
      "type": "pkgbuild-git",
      "url": "https://github.com/amt911/config-saver-aur.git",
      "ref": "e853c51f978b80fff9c993bcfdfe3a25c1efb201"
    }
  },
  "files": [
    { "path": "/etc/ssh/sshd_config.d/10-hardening.conf",
      "content": "PermitRootLogin no\nPasswordAuthentication no\n" }
  ],
  "config_saver": {
    "source": { "url": "https://github.com/amt911/config-saver-aur.git",
                "ref": "e853c51f978b80fff9c993bcfdfe3a25c1efb201" },
    "configs": {
      "etc-ssh": {
        "normalize_content": true,
        "directories": [
          { "source": "/etc/ssh",               "files": ["sshd_config", "ssh_config"] },
          { "source": "/etc/ssh/sshd_config.d", "files": ["10-hardening.conf"] }
        ]
      },
      "etc-net": {
        "directories": [
          { "source": "/etc/NetworkManager/conf.d", "files": ["wifi-powersave.conf"] }
        ]
      }
    },
    "timer_users": ["root"],
    "restore": [
      { "user": "root", "archive": "/run/media/usb/etc-ssh.tar.gz" }
    ]
  }
}
```

What each half does:

- **`files`** puts `10-hardening.conf` on every machine this config installs, and
  repairs it if somebody edits it. That is the part you want *identical*
  everywhere.
- **`config_saver`** backs up what the machine grew on its own — the rest of
  `sshd_config`, the NetworkManager tweak — on a `root` timer, and `restore`
  unpacks last machine's archive onto the new one, once per archive content.

Three traps, in the order people hit them:

1. `timer_users` must include **`root`** for anything under `/etc`; a user timer
   cannot read it and the archive quietly comes out short.
2. **Never list the `/etc/ssh` directory wholesale** — it holds
   `ssh_host_*_key`, the host's private keys. Name the files.
3. `ref` is the **full 40-character sha**; a short one is rejected.

And the boundary worth remembering: dasik's own `files` section is *desired
state* (declared, applied, repaired), while config-saver is a *backup policy*
(captured, restored). Neither replaces the other.

## Making a captured `disks` block generic

`sync` captures *this* machine: real device paths, real UUIDs, `wipe_disk:
false`, `format: false`, every data disk you own. To turn that into a config that
installs a **new** machine:

| Step | Why |
| --- | --- |
| delete every disk that is not the system disk | you do not want your NAS repartitioned |
| point `device` at the target machine's disk (`/dev/vda`, `/dev/nvme0n1`) | |
| set `wipe_disk: true` | nothing is destructive without it |
| make the last partition `"size": "rest"` | absolute sizes will not fit another disk |
| keep exactly **one** ESP | two ESPs is an unbootable ambiguity |
| drop `luks_uuid` | let dasik derive it deterministically |
| replace `unlock_fido2`/`unlock_tpm2` with `luks_password` | the token is bound to *your* hardware |
| give partitions role labels (`esp`, `root`, `home`) | a captured `nvme0n1p5` label means nothing on another disk |

Then, before pointing it at hardware: `dasik check`, and a run in a VM
([Development](Development.md#testing-in-a-vm)).

The longer walkthrough, with the sudo/venv gotchas, lives in
`docs/copy-your-config-and-test.md` in the repository.
