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

> **UEFI caveat.** `-kernel` direct boot bypasses OVMF, so the guest has no EFI
> environment and dasik's bootloader step (`bootctl` / `grub --target=x86_64-efi`,
> both need efivars) cannot run in this path. The `install` layer therefore
> verifies **partition + pacstrap + config + idempotency**, not the bootloader.
> For a boot-verified UEFI image, use the manual `run-iso` path below on an
> OVMF-capable host, then `qemu.sh boot`.

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
- **Layer B (`install`, unattended)** — run for real (Arch ISO 2026.06, KVM,
  3 GB): archiso's `script=` fetched the installer, and `dasik apply` produced a
  **complete base system on the guest `/dev/vda`** — GPT/ESP/ROOT, **154
  packages** pacstrapped, kernel installed, `hostname=dasik-vm`,
  `/etc/localtime → Etc/UTC`, locale + initramfs configured. `dasik apply`
  exited 0.
  - **Idempotency (the NixOS goal):** a second `apply` was a no-op for
    **disks, base, packages, timezone, locale, network, initramfs**.
  - **Bootloader caveat:** the `bootloader` step re-fires on the second apply
    and the ESP is empty — because the `-kernel` unattended guest has **no UEFI
    environment**, so `bootctl install` writes no EFI files and the
    `_installed()` marker never lands. This is the harness's UEFI limitation,
    not a wrong marker path. Verify bootloader idempotency with a UEFI (OVMF)
    run via `run-iso`. (Secondary observation: `BootloaderAction` trusts
    `bootctl`'s exit code without confirming the files landed — a robustness
    nicety, out of scope here.)
- The `install` run also **found a real dasik bug**: `NetworkAction` raised
  "Network type not recognized." on a hostname-only config, aborting the install
  before initramfs/bootloader. Fixed (an absent `network.type` no longer raises;
  regression test in `tests/lib/actions/test_network_action.py`).
- Unit-level guards (driver device allowlist, partition-node naming,
  `_has_partition_table`, RAM cap, `--dry-run` assembly) are **unit-tested /
  verified**. The safety guards make a mis-pointed run fail safe.
