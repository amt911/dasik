# Copying your running system into a dasik config (and testing it in a VM)

This guide covers the exact workflow — and the papercuts — for using dasik to
**capture your existing Arch system into a declarative config**, complete it for
a dracut + LUKS + FIDO2 + bluetooth setup, and test it in a KVM before trusting it.

> **Mental model.** dasik has two directions:
> - **`sync`** — *system → config* (read-only to the system; writes the JSON). This
>   is "copy my config".
> - **`plan` / `apply`** — *config → system* (`apply` is **destructive**: it
>   partitions, formats, pacstraps). Use these to install/converge, **not** to
>   capture.

---

## 0. Running the `dasik` command

`dasik` was installed with `pip install -e .` into the repo venv, so the binary is:

```
~/repos/dasik/.venv/bin/dasik
```

Your interactive shell finds it (the venv is on your `PATH`), but **`sudo dasik`
does not** — `sudo` uses root's `PATH`, which doesn't include the venv. That's why
you saw `sudo: dasik: command not found`. Always give sudo the full path:

```bash
sudo ~/repos/dasik/.venv/bin/dasik <verb> ...
# or, if the venv is active in your shell:
sudo "$(command -v dasik)" <verb> ...
```

A handy alias for this repo:

```bash
alias sdasik='sudo ~/repos/dasik/.venv/bin/dasik'
```

### `Error: Binary not found: arch-chroot`

Some actions shell out to `arch-chroot` (from `arch-install-scripts`) when they
probe/converge a target. `sync` and `plan` against your live system (`--target /`)
generally don't need it, but **`apply` does**, and a few probes may. If you hit it:

```bash
sudo pacman -S arch-install-scripts
```

---

## 1. Capture your system → a config (`sync`)

`sync` starts from an **existing JSON file** and splices your system's reality into
it. It does **not** create the file for you, and the file must be **valid JSON**
(an empty file gives `Expecting value: line 1 column 1`).

```bash
cd ~/repos/dasik

# 1. Seed file — a minimal valid JSON is enough (sync bootstraps the rest):
echo '{}' > config/mysystem.json

# 2. Sync needs root: it writes /var/lib/dasik/state.json and reads system state.
#    (Without root you get: Permission denied: '/var/lib/dasik/state.json'.)
sudo ~/repos/dasik/.venv/bin/dasik sync config/mysystem.json --target /
```

Result: `config/mysystem.json` now holds the captured reality, and a `.bak` of the
seed is written next to it.

### What `sync` captures automatically

- `packages` (explicitly-installed), `locales`, `timezone`, `network` + `hostname`,
  `users`, arbitrary `/etc` `files`, `kernel_cmdline`.
- **`disks`** — the partition layout is captured **non-destructively**. If the seed
  already **declares** disks, those are reflected back; if it declares **none**,
  `sync` now **discovers the live layout from scratch** (via `lsblk`/`findmnt`/
  `cryptsetup`) — all disks, best-effort. Every partition comes back with
  `format: false` and `wipe_disk: false`, an encrypted partition gets its real
  `luks_uuid` + enrolled `unlock_fido2`/`unlock_tpm2` baked in (the plaintext
  `luks_password` is *dropped* — a secret is never written back), and a btrfs root
  gets its mounted `btrfs_subvolumes` (`@`, `@home`, …). So re-applying a synced
  config can never reformat.
  - **Inventory, not a lossy guess.** Partitions whose filesystem dasik cannot
    represent (`ntfs`, unformatted, a *locked* LUKS) are **skipped**; a disk with no
    representable partitions is **omitted**; a btrfs spanning several devices shows
    each encrypted member disk (dasik's model has no multi-device btrfs). Every
    emitted disk is validated through the model, so the captured stanza always
    round-trips.
- **`zram`** — `/etc/systemd/zram-generator.conf` is captured verbatim as a
  `zram` mapping (`{device: {option: value}}`), so a zram-swap host round-trips.
  Applying it re-writes the same conf and pulls in `zram-generator`.
- **local `/etc` snippets** — `sync` discovers the files you (not a package) put
  under `/etc/modprobe.d` (`modprobe_conf`), `/etc/modules-load.d`
  (`modules_load`), `/etc/sysctl.d` (`sysctl_d`), `/etc/tmpfiles.d` (`tmpfiles_d`),
  `/etc/sddm.conf.d` (`sddm_conf_d`), `/etc/udev/rules.d` (`udev_rules`) and
  `/etc/profile.d` (`profile_d`). It **skips package-owned files** (`pacman -Qo`)
  and symlinks — those are distro defaults that come back with their package — so
  you only capture your own modprobe options/blacklists, module-load lists,
  sysctl tunables, udev rules and display-manager settings.
- **`/etc/crypttab`** — captured verbatim into `files` when it has any real
  (non-comment) entry, e.g. an encrypted random-key swap the `disks` layout does
  not otherwise describe. An empty/comment-only crypttab is left out.
- **firewall** — the live firewalld **permanent** `public` zone is captured into
  a `firewall` block: `allowed_services` / `remove_services` (the diff against
  firewalld's upstream defaults) and `rich_rules` verbatim (they come back in the
  same `firewall-cmd` syntax dasik consumes). Read via `firewall-offline-cmd`
  (reads `/etc/firewalld` directly — no running daemon needed, and it works
  against a `/mnt` install target), which needs root; `sync` runs as root.
- **WireGuard** — every `/etc/wireguard/*.conf` (wg-quick interfaces) is captured
  into `files`, so a VPN round-trips. ⚠️ a wg conf contains the interface
  **PrivateKey**; sync writes it verbatim into the JSON (as the `wireguard`
  config block already does) — **keep synced configs private**. If your VPN is a
  *NetworkManager* connection instead (`/etc/NetworkManager/system-connections/`),
  it is not captured here — that is a separate path.
- **the initramfs generator** — `"initramfs": "dracut"` (or `"mkinitcpio"`),
  detected from which one is installed.
- **`unlock_fido2` / `unlock_tpm2`** — read from the LUKS header's enrolled tokens
  (`cryptsetup luksDump`), so a FIDO2/TPM2-unlocked root round-trips.
- **`bluetooth.in_initramfs`** — detected from the `bluetooth` module in
  `/etc/dracut.conf.d/*.conf` (a BT keyboard at the early prompt).
- **`luks_options`** — extra `rd.luks.options` tokens (e.g. `token-timeout=10s`)
  read from the live kernel cmdline, minus the auto-derived fido2/tpm2 ones.

A dracut + LUKS + FIDO2 + bluetooth host now round-trips through `sync` with no
manual edits. From the captured config, dasik regenerates the equivalent
`/etc/dracut.conf.d/dasik.conf`, neutralizes mkinitcpio's pacman hooks, and derives
the `rd.luks.*` cmdline for you.
[`config/example-dracut-fido2.json`](../config/example-dracut-fido2.json) is an
annotated reference of all these fields.

---

## 1b. Make the captured `disks` generic before installing elsewhere

A synced config is an **inventory** — its `disks` block mirrors *your* machine
exactly (every disk you have, exact sizes, device-specific names, `format:false`).
That is right for a day-2 re-apply on the same host, but to install it onto a
**fresh disk** (a VM, a new SSD) you must generalize the `disks` block or it fails
the way it did before (`Device /dev/sdX does not exist`, a partition too big for a
smaller disk, or an unformatted `/boot`). The rest of the config (packages,
services, `files`, `firewall`, …) is already portable — only `disks` needs this.

| In the sync output | Make it generic |
| --- | --- |
| every disk, incl. data disks (`sdb`, `sdd`, …) | keep **only the system disk**; delete the others (they won't exist on the target) |
| `"device": "/dev/nvme0n1"` etc. | set to the **target** disk — a KVM guest is almost always `/dev/vda`. dasik needs this; there is no auto-detect |
| `"wipe_disk": false`, `"format": false` | **`true`** for a fresh install (a brand-new partition has no filesystem to preserve) |
| exact `"size": "3610645MiB"` | modest fixed sizes for ESP/boot (`1GiB`), and **`"rest"`** on the last partition so it fills any disk |
| device-name labels (`vda5`) / `luks_name: "vda7"` | role labels (`esp`, `boot`, `root`) and a generic `luks_name` like **`cryptroot`** (sync already produces role labels since the role-label change) |
| baked `luks_uuid`, `unlock_fido2: true` | **drop `luks_uuid`** (dasik derives a deterministic one) and, where the FIDO2 key isn't present at install/boot (any VM), drop `unlock_fido2` and set **`luks_password`** instead |
| two ESPs (one unmounted) | keep **one ESP** mounted at `/boot` |

Minimal, portable single-disk template (encrypted btrfs + subvolumes):

```json
"disks": {
  "disks": [
    {
      "device": "/dev/vda",
      "partition_table": "gpt",
      "wipe_disk": true,
      "partitions": [
        { "label": "esp", "size": "1GiB", "filesystem": "fat32",
          "partition_type": "esp", "mountpoint": "/boot", "format": true },
        { "label": "root", "size": "rest", "filesystem": "btrfs",
          "partition_type": "linux", "format": true,
          "encrypt": true, "luks_name": "cryptroot", "luks_password": "CHANGE_ME",
          "btrfs_subvolumes": [
            { "name": "@",     "mountpoint": "/",     "mount_options": ["compress-force=zstd:3"] },
            { "name": "@home", "mountpoint": "/home", "mount_options": ["compress-force=zstd:3"] }
          ]}
      ]
    }
  ]
}
```

> **Rule of thumb:** one disk, `device` = the target, `wipe_disk:true` +
> `format:true`, last partition `"rest"`, generic `luks_name`, no `luks_uuid`, no
> data disks. That installs on any disk without failing. dasik keys everything
> off the partition **labels**, never the partition number, so nothing breaks
> when `/dev/vda2` on the target held a different role on the source.

## 2. Sanity-check the config

```bash
~/repos/dasik/.venv/bin/dasik plan config/mysystem.json --target /
```

`plan` is read-only. For a captured config on the same machine it should show few
or no changes. For the dracut bits you should see, e.g.:

```
~ [initramfs] modify   hostonly="yes"
                       force_add_dracutmodules+=" systemd fido2 "
                       add_dracutmodules+=" bluetooth "
+ [kernel_cmdline] install rd.luks.options=<uuid>=fido2-device=auto,token-timeout=10s
+ [files] create /etc/pacman.d/hooks/90-mkinitcpio-install.hook   (neutralize mkinitcpio)
```

---

## 3. Test it in a KVM before trusting it

Testing in a VM proves the config installs and boots without risking your machine.
Requirements on the host: KVM (`/dev/kvm`), OVMF
(`/usr/share/edk2/x64/OVMF_CODE.4m.fd`), an Arch ISO, and (for TPM2) `swtpm`.

> ⚠️ **Always `pkill -9 qemu-system-x86_64` between runs.** A failed encrypted boot
> drops to an emergency shell and **leaves QEMU alive** (it never powers off),
> holding the disk-image lock — subsequent runs then read stale state and mislead you.

### 3a. Smoke test with a passphrase (no FIDO2 key needed)

FIDO2 needs the physical key; start with the passphrase path. The tracked
[`config/vm-dracut.json`](../config/vm-dracut.json) is exactly this (encrypted btrfs
+ `initramfs: dracut` + `bluetooth.in_initramfs`, autologin + serial):

```bash
cd ~/repos/dasik
pkill -9 qemu-system-x86_64
export DASIK_VM_ISO=~/ISO/archlinux-2025.12.01-x86_64.iso
export DASIK_VM_WORKDIR=~/.cache/dasik-dracut-test DASIK_VM_HTTP_PORT=8730 DASIK_VM_RAM=4096

# install into the VM (dracut installs, writes conf + crypttab, neutralizes mkinitcpio)
bash scripts/vmtest/qemu.sh install-driven config/vm-dracut.json

# boot it and type the passphrase over serial automatically
pkill -9 qemu-system-x86_64
DASIK_VM_LUKS_PASSWORD=dracutpass \
  bash scripts/vmtest/qemu.sh boot-unlock ~/.cache/dasik-dracut-test/vda.qcow2 dracutpass
```

Expected: passphrase prompt → unlock → login. Emergency mode ⇒ the encrypted
dracut boot still has an issue (this is the part not yet verified upstream).

### 3b. Interactive boot (see it yourself / debug)

```bash
pkill -9 qemu-system-x86_64
IMG=~/.cache/dasik-dracut-test/vda.qcow2
cp /usr/share/edk2/x64/OVMF_VARS.4m.fd /tmp/ovmf-vars.fd
qemu-system-x86_64 -enable-kvm -cpu host -m 4096 -smp 2 -nographic \
  -drive if=pflash,unit=0,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,unit=1,format=raw,file=/tmp/ovmf-vars.fd \
  -drive file=$IMG,if=virtio,format=qcow2 \
  -serial mon:stdio
# exit with Ctrl-A X
```

### 3c. FIDO2 inside the VM (USB passthrough)

FIDO2 needs the key present at **install** (enroll) and **boot**. Pass your key
through to QEMU (the `install-driven`/`boot-unlock` harness does **not** do this —
use the manual command from 3b, adding these flags; find the id with `lsusb`):

```bash
lsusb        # e.g. Bus ... Device ...: ID 1050:0407 Yubico ...
  # add to the qemu command:
  -device qemu-xhci -device usb-host,vendorid=0x1050,productid=0x0407
```

With `unlock_fido2: true` and the key passed through, dasik runs
`systemd-cryptenroll --fido2-device=auto` and the boot auto-unlocks with the key
(passphrase remains a fallback). Without the key you can only test 3a.

---

## 4. Applying for real

- **Fresh install** (from the Arch live ISO, destructive — it partitions/formats):
  ```bash
  dasik apply config/mysystem.json --target /mnt --yes
  ```
- **Day-2 on the running system** (converge non-destructively; keep `wipe_disk:false`
  and `format:false` — which is exactly what `sync` produced):
  ```bash
  sudo ~/repos/dasik/.venv/bin/dasik apply config/mysystem.json --target / --yes
  ```
  Re-running the same config is a no-op; `dasik generations` lists snapshots and
  `dasik rollback` restores a previous one.
