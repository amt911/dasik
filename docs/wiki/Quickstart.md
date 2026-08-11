# Quickstart

Two paths. Pick the one you are actually on.

- **[A. Install a new machine](#a-install-a-new-machine)** — from the live ISO, onto `/mnt`. Destructive.
- **[B. Manage the machine you are running](#b-manage-the-machine-you-are-running)** — day 2, onto `/`. Starts read-only.

---

## A. Install a new machine

### 0. Boot the ISO in UEFI mode

Not optional. In QEMU that means OVMF firmware; in virt-manager, *Customize
before install → Overview → Firmware = UEFI*. dasik refuses to install an EFI
bootloader on a legacy-BIOS boot, because `bootctl install` would exit 0 and the
machine would reboot into the installer forever.

Get networking up (`iwctl` for wifi), then install dasik →
[Installation](Installation.md#installing-onto-the-live-iso).

### 1. Write a config

Start from a tracked sample and edit it — `config/install-simple.json` is the
smallest realistic one, `config/vm-minimal.json` the smallest that boots.

```json
{
  "hostname": "my-arch",
  "bootloader": "sd-boot",
  "enable_microcode": true,
  "timezone": { "region": "Europe", "city": "Madrid" },
  "locales": {
    "selected_locales": ["en_US.UTF-8 UTF-8", "es_ES.UTF-8 UTF-8"],
    "desired_locale": "en_US.UTF-8",
    "desired_tty_layout": "es"
  },
  "network": { "type": "NetworkManager", "add_default_hosts": true },
  "disks": {
    "disks": [{
      "device": "/dev/nvme0n1",
      "partition_table": "gpt",
      "wipe_disk": true,
      "partitions": [
        { "label": "esp",  "size": "1GiB", "filesystem": "fat32",
          "partition_type": "esp", "mountpoint": "/boot" },
        { "label": "root", "size": "rest", "filesystem": "ext4",
          "partition_type": "linux", "mountpoint": "/" }
      ]
    }]
  },
  "packages": ["base", "base-devel", "linux", "linux-firmware", "sudo",
               "networkmanager", "vim"],
  "systemd": { "enable_units": ["NetworkManager.service"] },
  "users": [
    { "username": "root", "hashed_password": "$y$…" },
    { "username": "you",  "hashed_password": "$y$…",
      "shell": "/bin/bash", "groups": ["wheel"] }
  ],
  "sudo": { "wheel": true }
}
```

Generate the hashes — never put a plaintext password in a config, the schema
rejects it anyway:

```bash
dasik hash-password        # prompts twice, prints $y$…
```

`wipe_disk: true` is what makes this an install. It **erases the whole device**.
Check the device name three times (`lsblk`).

### 2. Validate before touching anything

```bash
dasik check my-config.json
```

This runs the JSON parse, the pydantic schema, and the cross-field
[preflight](Validation.md) — a user in a group no package creates, a display
manager no package provides, a bad `/etc/crypttab` line. Errors here would
otherwise have surfaced *after* the disk was wiped.

### 3. Dry-run

```bash
dasik plan my-config.json          # --target defaults to /mnt: correct here
```

Read every line. Destructive changes are marked:

```text
  + [disks] install /dev/nvme0n1  (wipe_disk — ERASES /dev/nvme0n1 (holds: WINDOWS, DATA))  ** DESTRUCTIVE **
  + [base] install base  (pacstrap)
  + [packages] install networkmanager
  + [users] create you
  + [bootloader] install sd-boot
```

If a disk is populated and you did **not** set `wipe_disk`, dasik refuses to
repartition it and says so — it will never silently reformat a disk that has
data on it.

### 4. Apply

```bash
dasik apply my-config.json -v      # -v streams every command; a log is written anyway
```

You get one confirmation prompt covering the destructive changes (`--yes` skips
it, for unattended runs). dasik then partitions, formats, mounts `/mnt`,
pacstraps, configures, installs the bootloader, and records **generation 1**.

If it fails part-way, what completed is recorded as a **partial generation** —
progress, never convergence. Fix the cause and re-run `apply`; completed work is
not redone. See [Workflows](Workflows.md#partial-generations).

### 5. Prove it converged

```bash
dasik plan my-config.json          # must print nothing
```

An empty plan on the second run *is* the idempotency guarantee. If a line comes
back every time, that is a bug — see
[Troubleshooting](Troubleshooting.md#a-plan-line-never-goes-away).

### 6. Reboot

```bash
umount -R /mnt
reboot
```

---

## B. Manage the machine you are running

Everything below is `--target /`. `plan`/`apply` do **not** default to it.

### 1. Capture what you have

```bash
sudo dasik sync my-system.json --target /
```

Start from `{}` in a file if you have nothing: `sync` bootstraps from reality —
disks and LUKS layout, packages (AUR ones marked), users, enabled units, locale,
timezone, hostname, firewall zone, `/etc` snippets it recognises, zram, the
boot chain. It writes a `.bak` next to the file. Full coverage table:
[Sync](Sync.md).

`sync` never mutates the system. It only rewrites the config file.

### 2. Check the capture round-trips

```bash
dasik check my-system.json                 # a capture the tool refuses is a broken capture
sudo dasik plan my-system.json --target /  # must be silent
```

That silence is the real test: *the machine and the file now say the same thing*.

### 3. Change one thing

Edit the config — add a package, enable a unit, add a sysctl file — then:

```bash
sudo dasik plan  my-system.json --target /   # exactly the change you made, nothing else
sudo dasik apply my-system.json --target /
sudo dasik plan  my-system.json --target /   # silent again
```

### 4. Undo

```bash
sudo dasik generations --target /
sudo dasik rollback --target /               # previous complete generation
sudo dasik rollback 3 --target /             # or a specific one
```

`rollback` restores that generation's config **and re-applies it**, so it is as
destructive as `apply`. It refuses to restore a partial generation.

---

## What to read next

| | |
| --- | --- |
| Every field you can put in that JSON | [Configuration](Configuration.md) |
| Encryption, btrfs subvolumes, a pendrive that unlocks the disk | [Disks](Disks.md) |
| Splitting the config once it stops fitting on a screen | [Config splitting](Config-splitting.md) |
| Working configs to copy | [Recipes](Recipes.md) |
