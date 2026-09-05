#!/usr/bin/env bash
# Runs INSIDE the Arch ISO guest (booted by qemu.sh run-iso) to perform a real
# dasik install onto the guest's virtual disk (/dev/vda). Not for the host.
#
#   mount -t 9p -o trans=virtio,ro dasik /mnt-src
#   /mnt-src/scripts/vmtest/guest-install.sh /mnt-src/config/<config>.json
#
# The config's `disks` section must target the guest disk (/dev/vda) with the
# format flags on. This is the only place the harness runs the FULL, destructive
# `dasik apply` (base install + config + bootloader) — safe because it is the
# guest's throwaway qcow2, never the host.
set -euo pipefail

CONFIG="${1:?usage: guest-install.sh <config.json>}"
[ -f "$CONFIG" ] || { echo "config '$CONFIG' not found" >&2; exit 1; }

# Sanity: refuse to run outside a VM guest, so a stray invocation on the host
# can't reach `dasik apply` against real disks.
if ! grep -qiE "qemu|kvm|virtio" /sys/class/dmi/id/sys_vendor 2>/dev/null \
   && [ ! -e /dev/vda ]; then
    echo "refusing: this does not look like a QEMU guest (no /dev/vda). Run it inside qemu.sh run-iso." >&2
    exit 2
fi

SRC="$(cd "$(dirname "$0")/../.." && pwd)"

echo ">> Copying dasik out of the read-only share and installing it"
# Only what the installer needs. `resources/` is a bind-mount of the Arch Wiki
# plus the old imperative installer (>200 MB across >12k files) and `.venv` /
# `.git` / `mutants` are the developer's, not the guest's. The ISO's root is
# RAM-backed with a fixed cowspace, so copying them is both slow and a way to
# run out of space before pacstrap has started.
rm -rf /root/dasik && mkdir -p /root/dasik
tar -C "$SRC" -cf - \
    --exclude=./resources --exclude=./.venv --exclude=./.git \
    --exclude=./mutants --exclude=./graphify-out --exclude=./.mypy_cache \
    . | tar -C /root/dasik -xf -
cd /root/dasik
python -m venv /root/venv
/root/venv/bin/pip install -e .

echo ">> Plan (read-only) against /mnt"
/root/venv/bin/dasik plan "$CONFIG" --target /mnt

echo ">> Apply (DESTRUCTIVE, guest disk only) against /mnt"
/root/venv/bin/dasik apply "$CONFIG" --target /mnt --yes

# genfstab if base install didn't already write one (best-effort).
if [ -d /mnt/etc ] && ! grep -q "UUID" /mnt/etc/fstab 2>/dev/null; then
    command -v genfstab >/dev/null && genfstab -U /mnt >> /mnt/etc/fstab || true
fi

echo ">> Install complete. Now: poweroff — then on the host run:"
echo "     scripts/vmtest/qemu.sh boot <the qcow2 qemu.sh created>"
