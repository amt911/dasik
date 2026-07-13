#!/usr/bin/env bash
# Shared helpers, config knobs, and SAFETY GUARDS for the dasik VM test harness.
# Sourced by loopback.sh and qemu.sh. Not meant to run standalone.
#
# dasik partitions disks and runs pacman — every code path the harness drives is
# destructive. The single most important thing in this file is
# `require_disposable_device`: it refuses to let the harness write to anything
# that is not a loopback/nbd device or the QEMU guest's own virtual disk.

# --- configurable knobs (env, with defaults) --------------------------------
# RAM in MiB. Default modest; hard-capped so a typo can't claim the whole host.
: "${DASIK_VM_RAM:=2048}"
: "${DASIK_VM_RAM_CAP:=8192}"     # 8 GiB ceiling (user requirement)
: "${DASIK_VM_CPUS:=2}"
: "${DASIK_VM_DISK:=8G}"          # disk image size (truncate/qemu-img syntax)
: "${DASIK_VM_ISO:=}"             # path to an Arch ISO (required by qemu.sh)
: "${DASIK_VM_WORKDIR:=}"         # scratch dir; a mktemp dir is used if empty

# --- pretty output ----------------------------------------------------------
_c_red=$'\033[31m'; _c_grn=$'\033[32m'; _c_yel=$'\033[33m'; _c_rst=$'\033[0m'
log()  { printf '%s>>%s %s\n' "$_c_grn" "$_c_rst" "$*"; }
warn() { printf '%s!!%s %s\n' "$_c_yel" "$_c_rst" "$*" >&2; }
die()  { printf '%sxx%s %s\n' "$_c_red" "$_c_rst" "$*" >&2; exit 1; }

# --- validation -------------------------------------------------------------
validate_ram() {
    case "$DASIK_VM_RAM" in
        ''|*[!0-9]*) die "DASIK_VM_RAM must be an integer (MiB); got '$DASIK_VM_RAM'." ;;
    esac
    if [ "$DASIK_VM_RAM" -gt "$DASIK_VM_RAM_CAP" ]; then
        die "DASIK_VM_RAM=$DASIK_VM_RAM MiB exceeds the ${DASIK_VM_RAM_CAP} MiB cap. Raise DASIK_VM_RAM_CAP only if you mean it."
    fi
    if [ "$DASIK_VM_RAM" -lt 512 ]; then
        warn "DASIK_VM_RAM=$DASIK_VM_RAM MiB is very low; the Arch ISO wants >=1024."
    fi
}

require_cmds() {
    local missing=()
    for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
    [ ${#missing[@]} -eq 0 ] || die "missing required command(s): ${missing[*]}"
}

require_root() {
    [ "$(id -u)" -eq 0 ] || die "$1 needs root (losetup/mount/mkfs). Re-run with sudo."
}

# THE guard. Only a loopback or nbd whole-device is an acceptable target for the
# host-side (loopback) layer. Anything that looks like a real disk aborts hard.
require_disposable_device() {
    local dev="$1"
    [ -n "$dev" ] || die "require_disposable_device: empty device."
    case "$dev" in
        /dev/loop[0-9]*|/dev/nbd[0-9]*) : ;;   # ok: file-backed, disposable
        /dev/sd*|/dev/nvme*|/dev/vd*|/dev/mmcblk*|/dev/hd*|/dev/xvd*)
            die "REFUSING to touch '$dev' — that looks like a REAL disk. The loopback layer only writes to /dev/loop* or /dev/nbd*." ;;
        *)
            die "REFUSING to touch '$dev' — not a recognised loopback/nbd device. Aborting to protect real hardware." ;;
    esac
    [ -b "$dev" ] || die "'$dev' is not a block device (did losetup succeed?)."
}

# Resolve the repo root from this file's location so scripts work from anywhere.
_vmtest_repo_root() {
    ( cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd )
}
