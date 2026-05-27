# Testing dasik without a VM

dasik is destructive — it partitions disks, runs `pacman`, and configures a system
via `arch-chroot`. You do **not** need a full virtual machine plus Samba/scp to
exercise most of it. dasik runs on your Arch host against a **disposable target**,
so there is nothing to copy into a guest.

Three layers, lightest first. Use the lightest one that covers what you changed.

## TL;DR

- **Logic / decisions** → `pytest` (no root, milliseconds).
- **Disk ops** (partition, LUKS, filesystems) → **loopback image** + `losetup`.
- **Install + config** (pacstrap, packages, users, services, files) → **`systemd-nspawn`**.
- **Bootloader + real boot** → **qemu**, booting the *same image* (still no scp).

The enabler is the `Target` abstraction: dasik operates on a configurable root
(`/` for the running host, `/mnt` or any path for an install target) instead of a
hardcoded `/mnt`. See `docs/superpowers/specs/2026-05-27-declarative-convergence-and-sync-design.md` §3.1.

---

## 0. Unit tests — fastest, no root

The first thing to run on any change. Covers the decision logic (`is_needed`/`plan`,
path/chroot routing, state + generation round-trips) with mocks — no disk is touched.

```bash
# dev deps live in a venv (this machine has no global pip)
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -v                       # or: .venv/bin/pytest -v
pytest --cov=dasik              # with coverage
```

---

## 1. Loopback disk image — disk operations

The disk path runs real `sgdisk`/`cryptsetup`/`mkfs`. A loop device is a real block
device backed by a file: fully disposable, no hardware risk, no VM.

```bash
# 20 GiB sparse image
truncate -s 20G /tmp/dasik-disk.img

# attach as a loop device, -P scans the partition table into /dev/loopNpX
sudo losetup --find --show -P /tmp/dasik-disk.img      # prints e.g. /dev/loop0
```

Point the `disks` section of your config at the loop device:

```json
"disks": { "disks": [ { "device": "/dev/loop0", "partition_table": "gpt", ... } ] }
```

Run dasik's disk/partition step against it, then inspect:

```bash
lsblk -f /dev/loop0
```

**Cleanup** (in order):

```bash
sudo umount /mnt/boot /mnt 2>/dev/null      # any mounted partitions
sudo cryptsetup close cryptroot 2>/dev/null # if a LUKS mapping was opened
sudo losetup -d /dev/loop0
rm /tmp/dasik-disk.img
```

> **Safety:** always confirm you are targeting `/dev/loopN`, never a real `/dev/sdX`
> or `/dev/nvme*`. dasik keeps formatting behind an explicit `format` flag — keep it
> off unless you mean it.

---

## 2. systemd-nspawn — install + configuration

Most of dasik configures a mounted root. `systemd-nspawn` boots that root as a
lightweight container (systemd as PID 1) in seconds — no kernel boot, no VM.

**Option A — bare directory (no disk image):**

```bash
sudo mkdir -p /tmp/dasik-root
sudo pacstrap -K /tmp/dasik-root base       # minimal rootfs (or let dasik's base-install do it)

# run dasik config against that root:
sudo dasik apply config.json --target /tmp/dasik-root     # see Status below

# boot it to verify users / services / units:
sudo systemd-nspawn -D /tmp/dasik-root --boot
```

**Option B — the loopback image from step 1 (closer to real: same partitions + fs):**

```bash
sudo mount /dev/loop0p3 /mnt          # root partition
sudo mount /dev/loop0p1 /mnt/boot     # ESP / boot
sudo dasik apply config.json --target /mnt
sudo systemd-nspawn -i /tmp/dasik-disk.img --boot   # boot straight from the image
```

**nspawn covers:** pacman/AUR installs, dropped files, users, locale/timezone,
enabling systemd units, most config actions.
**nspawn does NOT cover:** bootloader install, initramfs, real kernel/firmware boot.

---

## 3. qemu — only for the bootloader and a real boot

When you need to verify the bootloader and a real kernel boot, boot the **same image**
you built above — nothing to copy:

```bash
qemu-system-x86_64 -enable-kvm -m 4G \
  -drive file=/tmp/dasik-disk.img,format=raw,if=virtio
# UEFI: add  -bios /usr/share/edk2/x64/OVMF.4m.fd   (package: edk2-ovmf)
```

This is the only step resembling a VM, and only for the last ~10% (the boot chain).
Still no scp — dasik already wrote the image on the host.

---

## No file transfer, ever

dasik is a Python package on your host. Install it editable once (`pip install -e .`)
and run it on the host, pointing `--target` at the disposable root/image. Nothing is
copied into a guest. (If you ever *do* use a real VM, mount the repo with virtiofs/9p
instead of Samba/scp.)

---

## Status (2026-05-27)

- **Unit tests:** available now (`pytest`, 26 tests).
- **`Target`** (root `/` vs `/mnt`/arbitrary path) landed in Plan 1. The `--target`
  CLI flag and the `plan`/`apply`/`sync` verbs land in **Plan 4**; until `__main__`
  is wired to them, drive actions from a short Python snippet (`setup_actions()` +
  `execute_installation()` / the executor) against the mounted root. The loopback +
  nspawn methodology above applies regardless of CLI progress.
- Once `dasik plan --target <root>` exists, the tightest loop is VM-free:
  **build loop image → `dasik apply --target /mnt` → `systemd-nspawn --boot` to
  verify → `dasik plan` to iterate.**

## Optional: mkosi

systemd's [`mkosi`](https://github.com/systemd/mkosi) builds and boots OS images
(integrating nspawn/qemu). It is the "proper" Arch way to script repeatable image
builds if you outgrow the manual loopback loop above.
