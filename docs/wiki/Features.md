# Feature blocks

A feature block is a small declaration that **expands** into packages, units,
files and modprobe snippets before anything is planned. `"cups": {"install": true}`
is five packages and a socket; you write the intent, dasik writes the
consequences.

Expansion happens between parsing and preflight, so:

- everything a block contributes counts as *declared* for validation and for the
  plan;
- the block appears in the plan **through the domains it touches**, not as a
  `[cups]` line of its own;
- `sync` subtracts what your own toggles already derive, so a captured config
  keeps the block instead of its expansion.

Source: `dasik/lib/expand/toggles.py`.

---

## The catalogue

### bluetooth

```json
"bluetooth": { "enable": true, "package": "bluez", "in_initramfs": false }
```

| Contributes | |
| --- | --- |
| packages | `bluez` (or `package`), `bluez-utils` |
| units | `bluetooth.service` |

`in_initramfs: true` additionally puts the bluetooth stack **inside the
initramfs** (dracut), so a paired Bluetooth keyboard can type the LUKS
passphrase or touch a FIDO2 prompt. It is not part of this expansion — the
initramfs backend reads it directly ([Boot](Boot.md#dracut)).

### cups

```json
"cups": { "install": true }
```

| Contributes | |
| --- | --- |
| packages | `cups`, `cups-pdf`, `system-config-printer`, `sane`, `sane-airscan` |
| sockets | `cups.socket` |

Socket-activated, so no `cups.service` in the list. `sane` also creates the
`scanner` group, which is why a user in `scanner` passes preflight once this is
on.

### kvm

```json
"kvm": { "install": true }
```

| Contributes | |
| --- | --- |
| packages | `qemu-full`, `qemu-block-gluster`, `qemu-block-iscsi`, `samba`, `qemu-guest-agent`, `qemu-user-static`, `edk2-ovmf`, `swtpm`, `virt-firmware`, `libvirt`, `virt-manager`, `dnsmasq`, `openbsd-netcat`, `dmidecode` |
| units | `libvirtd.service`, `virtlogd.service` |
| modprobe | `dasik-nested-virt.conf` — `options kvm_intel nested=1`, `options kvm_amd nested=1` |
| user groups | `libvirt` for every declared user |

**`iptables-nft` is deliberately absent.** It conflicts with the `iptables` that
base/systemd already pull in, and `pacman -S iptables-nft` cannot swap it
non-interactively (the conflict prompt defaults to *No* under `--noconfirm`), so
declaring it left the install silently failing and every day-2 plan retrying
forever. libvirt's dependency is satisfied by the present iptables/nftables and
the NAT network works either way.

### firewall

```json
"firewall": {
  "enable": true,
  "allowed_services": ["syncthing", "samba"],
  "remove_services": ["ssh"],
  "rich_rules": ["rule family=\"ipv4\" source address=\"192.168.1.0/24\" accept"]
}
```

| Contributes | |
| --- | --- |
| packages | `firewalld` |
| units | `firewalld.service` |

The rules themselves are a domain of their own. dasik writes the complete
`/etc/firewalld/zones/public.xml`:

```text
desired zone = (firewalld's default services − remove_services)
             + allowed_services + rich_rules
```

Writing the file rather than driving `firewall-cmd` avoids firewalld's
default-service quirk — `--remove-service` does not strip a built-in default and
`--list-services` reports defaults, so a `remove_services` entry re-fired on
every single apply.

### wireguard

```json
"wireguard": { "enable": true, "interface_name": "wg0",
               "config_content": "[Interface]\nPrivateKey = …" }
```

| Contributes | |
| --- | --- |
| packages | `wireguard-tools` |
| units | `wg-quick@<interface_name>.service` |
| files | `/etc/wireguard/<interface_name>.conf` |

The content holds a private key. Keep it out of the committed config with
`{"$include_text": "secrets/wg0.conf"}` and give the file mode `0600` —
`wg-quick` ignores a world-readable config. See
[Config splitting](Config-splitting.md#secrets).

### snapper

```json
"snapper": { "enable": true,
             "configs": [{ "name": "root", "subvolume": "/" }] }
```

| Contributes | |
| --- | --- |
| packages | `snapper`, `snap-pac` |
| units | `snapper-timeline.timer`, `snapper-cleanup.timer` |

The configs themselves are created by the snapper action, with
`snapper -c <name> create-config <subvolume>` — planned only for a config that
does not already exist.

**It runs before the package transaction**, and installs `snapper`/`snap-pac`
itself if needed. snap-pac's pacman hooks snapshot every transaction, so the
config has to exist first; otherwise the whole install happens unprotected, as
it did on 2026-07-19.

Needs a btrfs root. Give `/.snapshots` its own subvolume
([Disks](Disks.md#btrfs-subvolumes)).

### hardware_acceleration

```json
"hardware_acceleration": { "enable": true, "install_codecs": true }
```

| Contributes | |
| --- | --- |
| packages (always) | `libva-utils`, `vdpauinfo` |
| + per declared driver | `nvidia`: `libva-nvidia-driver`, `nvtop` · `intel`: `intel-media-driver`, `intel-gpu-tools`, `libvdpau-va-gl` · `amd`: `mesa` (it provides the VA-API driver) |

The extras come from your [`drivers`](#gpu-drivers) list.

### GPU drivers

```json
"drivers": ["amd"]
```

Not a block — a root-level list. Recognised values and what they pull in:

| Driver | Base | Added when `pacman.multilib` is on |
| --- | --- | --- |
| `nvidia` | `nvidia-open`, `nvidia-utils`, `nvidia-settings` | `lib32-nvidia-utils` |
| `nvidia-open` | `nvidia-open`, `nvidia-utils`, `nvidia-settings` | `lib32-nvidia-utils` |
| `nouveau` | `mesa`, `vulkan-nouveau` | `lib32-mesa`, `lib32-vulkan-nouveau` |
| `intel` | `mesa`, `vulkan-intel`, `intel-media-driver` | `lib32-mesa`, `lib32-vulkan-intel` |
| `amd` | `mesa`, `vulkan-radeon` | `lib32-mesa`, `lib32-vulkan-radeon` |

An unrecognised name (`nvidia_old`, a typo) contributes **nothing** and does not
error — a wrong package is worse than a documented no-op. List the package by
hand in `packages` instead.

### cpu

```json
"cpu": { "scaling_driver": "auto", "mode": "active",
         "power_profiles_daemon": true, "governor": null }
```

| Contributes | |
| --- | --- |
| packages | `power-profiles-daemon` when enabled; `cpupower` when a `governor` is set |
| units | `power-profiles-daemon.service`; `cpupower.service` |
| files | `/etc/default/cpupower` with `governor="…"` |
| kernel cmdline | `amd_pstate=<mode>` / `intel_pstate=<mode>` ([Boot](Boot.md#kernel-cmdline)) |

`auto` reads the vendor from `/proc/cpuinfo`. Declaring both
power-profiles-daemon and a fixed governor is a warning (they fight);
power-profiles-daemon alongside `tlp` is an error.

### reflector

```json
"reflector": { "countries": ["ES"], "protocols": ["https"],
               "latest": 20, "sort": "rate",
               "save": "/etc/pacman.d/mirrorlist" }
```

| Contributes | |
| --- | --- |
| packages | `reflector` |
| units | `reflector.timer` |
| files | `/etc/xdg/reflector/reflector.conf` |

The file is rendered as reflector's own argument syntax:

```text
# Managed by dasik
--country ES
--protocol https
--latest 20
--sort rate
--save /etc/pacman.d/mirrorlist
```

### plymouth

```json
"plymouth": { "theme": "bgrt" }
```

| Contributes | |
| --- | --- |
| packages | `plymouth` |
| files | `/etc/plymouth/plymouthd.conf` when a theme is declared |
| kernel cmdline | `splash` |
| initramfs | the hook/module, positioned before the crypt hook |

An **absent** block means no splash. `{}` means splash with plymouth's default
theme. See [Boot](Boot.md#plymouth).

### zram

```json
"zram": { "zram0": { "zram-size": "ram / 2", "compression-algorithm": "zstd" } }
```

| Contributes | |
| --- | --- |
| packages | `zram-generator` |
| files | `/etc/systemd/zram-generator.conf` (its own domain, compared semantically) |

### oomd / systemd.conf

```json
"oomd": { "DefaultMemoryPressureDurationSec": "20s" }
```

| Contributes | |
| --- | --- |
| units | `systemd-oomd.service` (only for `oomd`) |
| files | a `10-dasik.conf` drop-in beside the packaged file |

`systemd_system_conf` and `systemd_user_conf` configure the managers themselves
and enable nothing.

### initramfs = dracut

```json
"initramfs": "dracut"
```

| Contributes | |
| --- | --- |
| packages | `dracut` |
| pacman hooks | the mkinitcpio neutralizers, written in phase 1 — [Boot](Boot.md#the-mkinitcpio-neutralizer-why-pacman_hooks-runs-first) |

### bootloader = sd-boot

| Contributes | |
| --- | --- |
| units | `systemd-boot-update.service` |

systemd ships that unit itself: it runs `bootctl update` when the ESP's loader is
older than the installed systemd. The old imperative installer built an AUR
pacman hook for the same job; the native unit needs no package.

Because it is *derived*, a synced config will not list it — the toggle
re-derives it. That is expected, and why sync assertions check reproducibility
rather than literal presence.

### enable_trim

```json
"enable_trim": true
```

| Contributes | |
| --- | --- |
| units | `fstrim.timer` |

### microsoft_fonts

```json
"microsoft_fonts": { "install": true, "source_iso": "/data/Win11.iso" }
```

Extracts the fonts from a Windows ISO you provide (`7z` + `arch-chroot`) and
installs them into the target. Planned only when the fonts are not already
there. No package contribution — it is a file operation.

### sudo

```json
"sudo": { "wheel": true, "nopasswd": false, "rules": [] }
```

Writes `/etc/sudoers.d/10-dasik`, validated with `visudo` **before** it is
installed. Contributes no package — declare `sudo` (or `base-devel`) in
`packages`, or preflight errors: a fragment without sudo cannot even be
validated.

---

## `containers` — the runtime, not the containers

```json
"containers": { "runtime": "podman", "rootless": true, "docker_compat": true }
```

One engine, installed and configured. dasik does not manage containers.

| | podman | docker |
| --- | --- | --- |
| package | `podman` | `docker` |
| unit | none (rootless podman runs no daemon); `podman.socket` with `api_socket` | `docker.service`, or `docker.socket` with `api_socket` |
| group | none — that is the point | `docker`, for every declared user |
| id maps | `subuid`/`subgid` per user | n/a |
| daemon config | n/a | `daemon_json` → `/etc/docker/daemon.json` |
| compose | `podman-compose` | `docker-compose` |

A field belonging to the other engine is **refused, not ignored**: `daemon_json`
under podman would describe a storage driver nobody applies, and every `plan`
would still say "no changes".

**The `docker` group is root-equivalent** — a member can bind-mount `/` into a
container. That is not a reason to avoid it (there is no other way to use docker
without root), it is a reason to know it.

**subuid/subgid** is the one piece with no other owner. Rootless podman maps
container uids into a range reserved for the user; `useradd` writes one for
users it creates (shadow ≥ 4.11.1-3), so a fresh install converges to nothing,
but an older account or one restored from a capture has none — and without it
every rootless container fails to start. dasik allocates the next free range
above whatever `/etc/subuid` already reserves.

## `config_saver` — backups of `$HOME`, and restoring them

```json
"config_saver": {
  "source": { "url": "https://github.com/amt911/config-saver-aur.git", "ref": "e853c51f978b80fff9c993bcfdfe3a25c1efb201" },
  "configs": { "dotfiles": { "directories": [{ "source": "$HOME", "files": [".zshrc"] }] } },
  "timer_users": ["andres"],
  "restore": [{ "user": "andres", "archive": "/run/media/usb/dotfiles.tar.gz" }]
}
```

[config-saver](https://github.com/amt911/config-saver) carries what a config
file cannot: themes, browser profiles, keyboard layouts, whole directories. The
block declares the policy — which backup documents exist, whose timer runs — and
the restore unpacks, on a fresh machine, the archive the old one produced.

The package is **not in the AUR**; `source` is the Git PKGBUILD that builds it,
which becomes a [`package_sources`](Packages.md#packages-from-a-git-pkgbuild)
entry. Without it the name resolves nowhere and `warn-and-skip` drops it.

Restores are **once per archive content**: the marker under
`~/.local/state/dasik/config-saver/<sha256>` says what was unpacked, so
re-applying does nothing and a newer capture at the same path is restored again.
Un-declaring one removes nothing — unpacking cannot be undone.

### It is not only `$HOME` — `/etc/ssh` and friends

A backup document takes absolute paths, so system configuration goes in the same
way:

```json
"config_saver": {
  "source": { "url": "https://github.com/amt911/config-saver-aur.git", "ref": "e853c51f978b80fff9c993bcfdfe3a25c1efb201" },
  "configs": {
    "etc-ssh": {
      "normalize_content": true,
      "directories": [
        { "source": "/etc/ssh",               "files": ["sshd_config", "ssh_config"] },
        { "source": "/etc/ssh/sshd_config.d", "files": ["10-hardening.conf"] }
      ]
    }
  },
  "timer_users": ["root"]
}
```

Three things decide whether this works:

- **The timer is per user.** A document that reads `/etc` needs `root` in
  `timer_users`; a timer running as `andres` cannot read
  `/etc/ssh/sshd_config.d`, and the backup silently comes out short.
- **Name the files, not the directory.** `/etc/ssh` also holds the host's
  private keys (`ssh_host_*_key`). Listing `files` explicitly is what keeps them
  out of a configuration archive you might push somewhere.
- **`ref` is the full 40-character sha.** A short one is rejected by the model
  (`config_saver.source.ref`).

### `config_saver` saves; `files` applies

They are complementary, and mixing them up is the easy mistake:

| you want | use |
| --- | --- |
| every machine to *have* this `sshd_config`, and drift repaired | [`files`](Configuration.md) — dasik writes it, plans the drift, fixes it |
| to *keep* what this machine grew, and put it back on the next one | `config_saver` |

`files` is desired state: declare it and every install gets it. `config_saver`
is a backup policy: it captures what a config file cannot express and restores
it on a fresh machine. For `/etc/ssh` most people want both — the hardening
snippet declared in `files`, the accumulated local bits saved by config-saver.

---

## Seeing a feature in the plan

A block never appears under its own name. `sysrq` shows up as
`+ [kernel_cmdline] install sysrq_always_enabled=1`; `cpu` as a cmdline
parameter plus a package plus a unit plus `/etc/default/cpupower`; `reflector`
as a `[files]` entry plus `reflector.timer`.

That is fine. What is *not* fine is a block that appears nowhere at all — you
could not tell "already applied" from "dasik ignores this". Every feature
therefore ships assertions for **missing ⇒ planned, present ⇒ silent,
owned-but-undeclared ⇒ removed**, in
`tests/lib/test_feature_detectability.py`, and capture assertions in
`tests/lib/test_feature_sync_capture.py`.
