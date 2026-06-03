# Installing with dasik from an Arch live ISO (or a VM)

How to run dasik from a booted Arch live ISO to install onto a target disk.

> ⚠️ **Destructive.** `dasik apply` partitions/formats the disk in your config
> (`wipe_disk: true` wipes it entirely). Use a disposable disk / VM.

## Why a RAM scratch dir

The live ISO's writable root (airootfs) is a small RAM overlay (often capped,
e.g. 256 MiB). Cloning dasik, `pip install`, and pacman's download cache all
fight for it and hit "no space on device" — even with lots of RAM and a huge
target disk. So we carve out our own bigger tmpfs and work there. (dasik also
mounts a RAM tmpfs for pacman's cache during pacstrap; see
`base_install_action._cache_to_ram`.)

## From scratch

```bash
# 1. scratch in RAM (4G of your RAM; tmpfs only uses what you write)
mkdir -p /scratch && mount -t tmpfs -o size=4G tmpfs /scratch
cd /scratch

# 2. git + clone dasik
pacman -Sy --noconfirm git
git clone <YOUR-REPO-URL> dasik && cd dasik
# (whatever branch you're testing)
git checkout add-vm-complex-config

# 3. pip install -e .  — redirect build/cache to the scratch, not the capped overlay
export TMPDIR=/scratch/tmp PIP_CACHE_DIR=/scratch/pipcache
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR"
pip install -e . --break-system-packages

# 4. install
dasik plan  config/install-vm-complex.json          # read-only preview
dasik apply config/install-vm-complex.json --yes     # DESTRUCTIVE
```

### No-pip alternative (deps via pacman)

If `pip` runs out of overlay space (ENOSPC) installing into site-packages:

```bash
pacman -Sy --noconfirm python-pydantic python-colorama
PYTHONPATH=/scratch/dasik python -m dasik apply config/install-vm-complex.json --yes
```

## Requirements

- **VM firmware: UEFI (OVMF)**, not BIOS — the sample config uses an ESP + GRUB EFI.
- **Disk ≥ 40 GiB** for `config/install-vm-complex.json` (root 30 GiB + swap 4 GiB
  + ESP 1 GiB + home rest). dasik aborts up front if the layout exceeds the disk.
- Device name: `lsblk` — usually `/dev/vda` (virtio). If it shows `/dev/nvme0n1`,
  set that as `device` in the config (dasik handles the `p1`/`p2` suffix).

## Resuming after a failure

dasik is idempotent — re-running continues from where it stopped (already-done
steps are skipped, the disk is **not** re-wiped once its partitions exist).

- **Same live session, `/mnt` still mounted** → just re-run `dasik apply`.
- **After a reboot / `/mnt` unmounted** → dasik re-mounts the existing partitions
  automatically on a converged re-run. (If you ever need to do it by hand:
  `mount /dev/vdaN /mnt`, the ESP at `/mnt/boot`, home at `/mnt/home`,
  `swapon /dev/vdaM`.)

## Cleanup

```bash
cd / && umount -R /mnt 2>/dev/null; swapoff -a 2>/dev/null
umount /scratch
```

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `no space on device` cloning / `pip` / `scp` | airootfs RAM overlay is tiny — use the `/scratch` tmpfs above (don't work in `/root`). |
| `ModuleNotFoundError: colorama` | deps not in the Python you run. Use `pip install -e . --break-system-packages` in the same Python, or the pacman + `PYTHONPATH` variant. |
| `error: failed to prepare transaction (could not find database)` (multilib) | fixed: `PacmanAction` runs `pacman -Sy` after enabling `[multilib]`. Pull latest. |
| `error: target not found: <pkg>` | a package no longer exists in Arch repos (rolling). Verify against `resources/arch-wiki/` and remove/replace it in the config or in `dasik/lib/expand/toggles.py`. |
| `declared partitions need ~X MiB but <dev> is only ~Y MiB` | the layout doesn't fit — shrink sizes or use a bigger disk. |
| garbled `b'NAME SIZE…\n'` line | fixed: lsblk output is decoded now. Pull latest. |
