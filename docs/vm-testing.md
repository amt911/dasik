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

archiso has no built-in unattended hook, so the install is driven by a script you
run **inside** the guest; booting the result is fully automatic.

```bash
# 1. boot the ISO with a fresh disk + the repo shared over 9p
DASIK_VM_ISO=~/Downloads/archlinux.iso scripts/vmtest/qemu.sh run-iso

# 2. inside the guest (config's `disks` must target /dev/vda, format on):
mount -t 9p -o trans=virtio,ro dasik /mnt-src
/mnt-src/scripts/vmtest/guest-install.sh /mnt-src/config/install-megamix.json
poweroff

# 3. back on the host, verify the installed image boots:
scripts/vmtest/qemu.sh boot /tmp/dasik-qemu.XXXX/vda.qcow2
```

`guest-install.sh` installs dasik into a venv from the share, runs `dasik plan`
then `dasik apply --target /mnt --yes` (the only place the full destructive apply
runs — against the guest's throwaway qcow2), and refuses to run if it does not
detect a QEMU guest. `qemu.sh boot` boots headless and passes when the guest
reaches a login/boot marker within `DASIK_VM_BOOT_TIMEOUT`, else fails.

Add `--dry-run` to either `qemu.sh` subcommand to print the exact
`qemu-system-x86_64` command without launching.

## CI

`scripts/vmtest/loopback.sh` and `qemu.sh run-iso` need root / nested KVM, which
GitHub hosted runners lack — they are **local** harnesses. The `vm-harness-lint`
CI job (opt-in, `workflow_dispatch`) runs what *is* CI-safe: `bash -n` on every
script and the `apply_disks_only.py` guard tests. Full boot runs live on a
KVM-capable host.

## Status (what has been executed)

- Layer A driver guard + partition-node naming: **unit-tested and passing**
  (`tests/integration/test_vmtest_disk_driver.py`,
  `tests/lib/actions/test_partition_device_naming.py`).
- Script syntax, safety guards, RAM cap, and `--dry-run` command assembly:
  **verified**.
- A full end-to-end boot install was **not** run in the authoring session (no
  Arch ISO / no root there); run Layer A and B locally per the steps above. The
  guards make a mis-pointed run fail safe rather than touch real hardware.
