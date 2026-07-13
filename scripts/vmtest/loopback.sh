#!/usr/bin/env bash
# VM test — Layer A: loopback disk image (no kernel boot, no network).
#
# Exercises dasik's most destructive code path — DiskPartitionAction — against a
# real block device that is just a file. Fast; needs root (losetup/mount/mkfs).
# It does NOT run base install (pacstrap); that is Layer B (qemu.sh).
#
#   sudo scripts/vmtest/loopback.sh
#
# Knobs (see lib.sh): DASIK_VM_DISK (image size, default 8G), DASIK_PYTHON
# (interpreter with dasik importable; default python3).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

PYTHON="${DASIK_PYTHON:-python3}"

require_root "loopback.sh"
require_cmds losetup parted truncate lsblk blkid mkfs.ext4 mkfs.fat "$PYTHON"
"$PYTHON" -c "import dasik" 2>/dev/null \
    || die "dasik is not importable by '$PYTHON'. Install it: pip install -e . (see docs/vm-testing.md)."

WORK="${DASIK_VM_WORKDIR:-$(mktemp -d /tmp/dasik-loopback.XXXXXX)}"
IMG="$WORK/disk.img"
DEV=""
CONFIG="$WORK/config.json"

cleanup() {
    set +e
    # Deepest mounts first (the disk action mounts /mnt then /mnt/boot).
    umount /mnt/boot 2>/dev/null
    umount /mnt 2>/dev/null
    [ -n "$DEV" ] && losetup -d "$DEV" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

log "Creating ${DASIK_VM_DISK} sparse image at $IMG"
truncate -s "$DASIK_VM_DISK" "$IMG"

log "Attaching as a loop device (-P scans the partition table)"
DEV="$(losetup -f -P --show "$IMG")"
require_disposable_device "$DEV"
log "Loop device: $DEV"

# Minimal GPT layout: 512 MiB ESP (fat32) + ext4 root. Dict-nested "disks" shape
# is the one the v3 path consumes. wipe_disk:false (fresh image → no sgdisk).
cat > "$CONFIG" <<JSON
{
  "disks": {
    "disks": [
      {
        "device": "$DEV",
        "partition_table": "gpt",
        "wipe_disk": false,
        "partitions": [
          {"label": "ESP",  "size": "512MiB", "filesystem": "fat32",
           "partition_type": "esp",   "mountpoint": "/boot", "format": true},
          {"label": "ROOT", "size": "rest",   "filesystem": "ext4",
           "partition_type": "linux", "mountpoint": "/",     "format": true}
        ]
      }
    ]
  }
}
JSON

log "Applying disk layout via the real DiskPartitionAction (guarded driver)"
"$PYTHON" apply_disks_only.py "$CONFIG"

log "Verifying result with blkid"
P1="${DEV}p1"; P2="${DEV}p2"
fail=0
fs1="$(blkid -o value -s TYPE "$P1" 2>/dev/null || true)"
fs2="$(blkid -o value -s TYPE "$P2" 2>/dev/null || true)"
lb1="$(blkid -o value -s LABEL "$P1" 2>/dev/null || true)"
lb2="$(blkid -o value -s LABEL "$P2" 2>/dev/null || true)"
[ "$fs1" = "vfat" ] || { warn "expected $P1 to be vfat, got '${fs1:-none}'"; fail=1; }
[ "$fs2" = "ext4" ] || { warn "expected $P2 to be ext4, got '${fs2:-none}'"; fail=1; }
[ "$lb1" = "ESP" ]  || { warn "expected $P1 label ESP, got '${lb1:-none}'"; fail=1; }
[ "$lb2" = "ROOT" ] || { warn "expected $P2 label ROOT, got '${lb2:-none}'"; fail=1; }

lsblk -f "$DEV"

if [ "$fail" -ne 0 ]; then
    die "loopback layer FAILED — the partition/format result did not match the declared layout."
fi
log "loopback layer PASSED — GPT + ESP(vfat) + ROOT(ext4) landed on $DEV."
