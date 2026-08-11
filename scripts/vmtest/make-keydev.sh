#!/usr/bin/env bash
# Create the "virtual pendrive" the LUKS keyfile unlock is tested against.
#
# A keyfile unlock names its key device by FILESYSTEM UUID (`unlock_keydev`), so
# the config has to know that UUID before the device exists. FAT lets us pin it:
# `mkfs.vfat -i` sets the volume id, which blkid reports as `XXXX-XXXX`. The
# config/vm-luks-keyfile.json pendrive is therefore always 1234-ABCD, on every
# machine and every run — no editing a captured UUID into the config by hand.
#
# The image is a plain raw file with the filesystem on the WHOLE device (no
# partition table): that is what a mkfs'd USB stick looks like, and it keeps the
# by-uuid symlink pointing at /dev/vdb instead of /dev/vdb1.
#
# Usage: make-keydev.sh [image-path] [volume-id] [size]
# Defaults: $HOME/.cache/dasik-vmtest/keydev.raw  1234-ABCD  64M
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

out="${1:-$HOME/.cache/dasik-vmtest/keydev.raw}"
volid="${2:-1234-ABCD}"
size="${3:-64M}"

require_cmds mkfs.vfat truncate

case "$volid" in
    [0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]-[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]) : ;;
    *) die "volume id must look like 1234-ABCD (8 hex digits, dashed); got '$volid'." ;;
esac

# Guard: only ever write to a regular file. A block device here would be a real
# disk, and mkfs.vfat would happily erase it.
[ -b "$out" ] && die "REFUSING to mkfs '$out' — that is a block device, not an image file."

mkdir -p "$(dirname "$out")"
rm -f "$out"
truncate -s "$size" "$out"
# -F 32 keeps the geometry predictable; -i takes the id WITHOUT the dash.
mkfs.vfat -F 32 -i "${volid//-/}" -n DASIKKEY "$out" >/dev/null

log "key device image: $out (vfat, UUID=$volid, $size)"
if command -v blkid >/dev/null 2>&1; then
    log "blkid says: $(blkid -o value -s UUID "$out" 2>/dev/null || echo '(needs root to probe a file)')"
fi
echo "$out"
