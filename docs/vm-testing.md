# Functional testing in a VM / on a loop device

dasik partitions disks and runs `pacman` — the only way to know an *install*
actually works (not just that the decision logic is right) is to run it against a
**disposable block device**. This harness gives you two layers, both of which
only ever write to a file-backed device or a QEMU guest disk, never real
hardware.

| Layer | Script | What it proves | Needs | Speed |
| --- | --- | --- | --- | --- |
| A — loopback | `scripts/vmtest/loopback.sh` | disk partitioning/format lands correctly on a real block device | root, `losetup` | seconds |
| B — QEMU | `scripts/vmtest/qemu.sh` | a full `dasik apply` install boots | `qemu`, KVM, an Arch ISO | minutes |

Unit + property + mutation tests remain the first line (see
`docs/testing-without-a-vm.md`, `docs/mutation-testing.md`); this harness is the
end-to-end complement.

## Safety model (read this)

Every layer routes through the guards in `scripts/vmtest/lib.sh` and
`scripts/vmtest/apply_disks_only.py`:

- `require_disposable_device` accepts **only** `/dev/loop*` / `/dev/nbd*` and
  hard-aborts on anything resembling a real disk (`/dev/sd*`, `/dev/nvme*`,
  `/dev/vd*`, `/dev/mmcblk*`, `/dev/hd*`).
- `apply_disks_only.py` re-checks every `disks[].device` against the same
  allowlist and **exits before importing dasik** if a config points anywhere
  else (exit code 3). This guard is unit-tested in
  `tests/integration/test_vmtest_disk_driver.py`.

## Configurable knobs (env)

| Var | Default | Meaning |
| --- | --- | --- |
| `DASIK_VM_RAM` | `2048` | guest RAM in MiB — **hard-capped at 8192** (`DASIK_VM_RAM_CAP`) |
| `DASIK_VM_CPUS` | `2` | vCPUs |
| `DASIK_VM_DISK` | `8G` | disk image size (`truncate`/`qemu-img` syntax) |
| `DASIK_VM_ISO` | — | path to an Arch ISO (required by `qemu.sh run-iso`) |
| `DASIK_VM_BOOT_TIMEOUT` | `180` | seconds `qemu.sh boot` waits for a boot marker |
| `DASIK_PYTHON` | `python3` | interpreter with dasik importable (loopback layer) |

Over-cap RAM is refused: `DASIK_VM_RAM=9000 …` → *"exceeds the 8192 MiB cap"*,
exit 1, nothing launched. Raise the ceiling only deliberately via
`DASIK_VM_RAM_CAP`.

## Layer A — loopback (disk ops)

```bash
pip install -e .                      # dasik must be importable
sudo scripts/vmtest/loopback.sh       # DASIK_VM_DISK=16G sudo -E ... to resize
```

It creates a sparse image, attaches it with `losetup -P`, applies a minimal
GPT + ESP(fat32) + root(ext4) layout via the **real** `DiskPartitionAction`, then
asserts with `blkid` that the two filesystems and labels landed. Everything is
torn down on exit (unmount, `losetup -d`, remove image).

Prereqs: `losetup parted mkfs.ext4 mkfs.fat blkid` (all in `util-linux` /
`dosfstools` / `e2fsprogs`). `wipe_disk:true` configs additionally need `sgdisk`
(`gptfdisk`) — the bundled config uses `wipe_disk:false` so a fresh image needs
neither.

> This layer is what makes the loop-device flow in `docs/testing-without-a-vm.md`
> actually work: partition-node naming now handles `/dev/loop0` → `/dev/loop0p1`
> (previously it produced `/dev/loop01` and `mkfs` failed).

## Layer B — QEMU (full boot)

### Unattended (`install`)

```bash
DASIK_VM_ISO=~/Downloads/archlinux.iso DASIK_VM_RAM=3072 \
  scripts/vmtest/qemu.sh install config/vm-minimal.json
```

This boots the ISO's kernel directly (`-kernel`/`-initrd`) with archiso's
`script=` parameter pointing at `guest-install-auto.sh` — served over a
throwaway HTTP server on `127.0.0.1` — which archiso fetches and runs as root.
That script 9p-mounts the repo, installs dasik into a venv, runs `dasik apply`
against the guest's `/dev/vda`, re-applies to check idempotency, and poweroffs.
The host follows progress on the serial console (every line is prefixed
`DASIK-VM:`) and waits for the `DASIK-VM-DONE rc=<n>` marker. The qcow2 and
extracted kernel live under `~/.cache/dasik-vmtest/` (a real disk — **not**
`/tmp`, which is often tmpfs/RAM). RAM is `DASIK_VM_RAM` (default 2048, capped
8192); `DASIK_VM_INSTALL_TIMEOUT` bounds the whole run.

> **UEFI.** When OVMF (`edk2-ovmf`) is installed, `install` boots the ISO kernel
> with an OVMF pflash pair (CODE + a fresh writable VARS copy). Verified: this
> gives the guest a real EFI environment (`/sys/firmware/efi/efivars` present),
> so dasik's bootloader step (`bootctl` / `grub --target=x86_64-efi`) installs
> for real and its re-apply is a no-op — full idempotency including the
> bootloader. The ISO is attached on **virtio** (`media=cdrom`) because OVMF does
> not enumerate the IDE `-cdrom`. Without OVMF the guest boots BIOS-style and the
> bootloader step can't complete (everything else is still verified);
> `qemu.sh boot` (also OVMF-aware) then boots the installed image.

### Out of space in the live ISO (airootfs / cowspace)

The Arch ISO root is a **read-only squashfs** plus a writable overlay called the
**cowspace** — a **tmpfs in RAM**, sized by the kernel `cow_spacesize` parameter.
The harness pins it at `cow_spacesize=2G` (in `qemu.sh`'s `-append`), so a run
that writes a lot to the *live* root (AUR builds under `makepkg`, the pacman
download cache, `/tmp`) can hit **"No space left on device"** even though the
guest disk is fine. (A normal `dasik apply` writes into `/mnt` on the guest disk,
**not** the cowspace — so this bites AUR/interactive work, not the pacstrap.)

**Unblock right now, without rebooting** — the cowspace is already a tmpfs, so
just grow it in place (needs no disk space):

```bash
mount -o remount,size=75% /run/archiso/cowspace   # up to 75% of RAM
df -h /run/archiso/cowspace     # Size grew?
free -h                         # RAM actually free?
```

If `free -h` shows free RAM, that remount unblocks you immediately. If RAM itself
is exhausted (the tmpfs can't grow past RAM), free some instead:

```bash
rm -rf /var/cache/pacman/pkg/*   # downloaded package cache
rm -rf /tmp/*
```

…or move the heavy writes onto the **guest disk** (not RAM) — e.g. point the
pacman cache / AUR build dir at a directory under `/mnt`.

**Raise it for the whole run** — bump both `cow_spacesize` occurrences and give
the guest RAM to hold it (the cow lives in RAM, so `cow_spacesize` < `DASIK_VM_RAM`):

```bash
sed -i 's/cow_spacesize=2G/cow_spacesize=6G/' scripts/vmtest/qemu.sh
DASIK_VM_RAM=8192 scripts/vmtest/qemu.sh install config/vm-minimal.json
```

Outside this harness (booting a real ISO): add `cow_spacesize=6G` on the kernel
line from the boot menu (`e`/`Tab`), or `mount -o remount,size=6G /run/archiso/cowspace`
once booted.

### Manual (`run-iso` + `boot`)

```bash
# 1. boot the ISO with a fresh disk + the repo shared over 9p
DASIK_VM_ISO=~/Downloads/archlinux.iso scripts/vmtest/qemu.sh run-iso

# 2. inside the guest (config's `disks` must target /dev/vda, format on):
mount -t 9p -o trans=virtio,ro dasik /mnt-src
/mnt-src/scripts/vmtest/guest-install.sh /mnt-src/config/install-megamix.json
poweroff

# 3. back on the host, verify the installed image boots:
scripts/vmtest/qemu.sh boot ~/.cache/dasik-vmtest/vda.qcow2
```

`guest-install.sh` installs dasik into a venv from the share, runs `dasik plan`
then `dasik apply --target /mnt --yes` (the only place the full destructive apply
runs — against the guest's throwaway qcow2), and refuses to run if it does not
detect a QEMU guest. `qemu.sh boot` boots headless and passes when the guest
reaches a login/boot marker within `DASIK_VM_BOOT_TIMEOUT`, else fails.

Add `--dry-run` to any `qemu.sh` subcommand to print the exact
`qemu-system-x86_64` command without launching.

## CI

`scripts/vmtest/loopback.sh` and `qemu.sh run-iso` need root / nested KVM, which
GitHub hosted runners lack — they are **local** harnesses. The `vm-harness-lint`
CI job (opt-in, `workflow_dispatch`) runs what *is* CI-safe: `bash -n` on every
script and the `apply_disks_only.py` guard tests. Full boot runs live on a
KVM-capable host.

## Status (what has been executed)

- **Layer A (loopback)** — run for real against `/dev/loop0`: GPT + ESP(vfat) +
  ROOT(ext4) landed, and a **second apply of the same config was a no-op**
  ("already matches") — real-hardware idempotency, `/mnt` untouched.
- **Layer B (`install`, unattended UEFI)** — run for real (Arch ISO 2026.06,
  KVM, OVMF, 3 GB): archiso's `script=` fetched the installer, and `dasik apply`
  produced a **complete, bootable UEFI system on the guest `/dev/vda`**:
  - GPT + **ESP populated** (`EFI/systemd/systemd-bootx64.efi`, `EFI/BOOT/BOOTX64.EFI`,
    `loader/loader.conf`, `loader/entries/arch.conf`), ROOT ext4, **154 packages**
    pacstrapped, kernel installed, `hostname`, `/etc/localtime → Etc/UTC`, locale,
    initramfs. `/etc/fstab` records **both** `/` and the ESP `/boot`. `dasik apply`
    exit 0.
  - **Full idempotency (the NixOS goal):** the second `apply` reported
    **"No changes - system matches config"** — a complete no-op across **every**
    step, bootloader included.
  - The installed image **boots** (`qemu.sh boot`, OVMF).
- The unattended run **found and this branch FIXES two real dasik bugs** that
  unit tests missed:
  1. `NetworkAction` raised "Network type not recognized." on a hostname-only
     config (empty `network.type`), aborting before initramfs/bootloader.
  2. **Mount-order shadowing** — `_mount_partitions` sorted by
     `mountpoint.count('/')`, so `/` and `/boot` tied; the ESP was mounted at
     `/mnt/boot` then **shadowed** by root at `/mnt`, leaving the ESP empty
     (kernel + bootloader on the root fs) → a **non-bootable install** whose
     bootloader never converged. Fixed by ordering on path-component depth so
     root mounts first. (This also resolved the earlier "bootloader re-fires"
     observation — it was a symptom of the empty ESP, not the no-UEFI env.)
- **Btrfs + subvolumes** — verified for real: `config/vm-btrfs.json` (root btrfs
  with `@`/`@home`/`@snapshots`) installs with correct `subvol=` fstab entries and
  `rootflags=…,subvol=@` in the boot entry, and the second apply is a no-op.
- Unit-level guards (driver device allowlist, partition-node naming,
  `_has_partition_table`, mount order, network type, RAM cap, `--dry-run`
  assembly) are **unit-tested**. The safety guards make a mis-pointed run fail
  safe.
