#!/usr/bin/env bash
# VM test — Layer B: QEMU (full boot).
#
# Two flows:
#   run-iso   boot the Arch ISO with a fresh qcow2 disk + the dasik repo shared
#             over 9p, so you can run guest-install.sh inside the guest to do a
#             real `dasik apply` install. (Interactive: archiso has no unattended
#             hook without rebuilding it; see docs/vm-testing.md.)
#   boot      boot an already-installed image headless and verify it reaches a
#             login/boot marker within a timeout. This is the automatable check.
#
# Usage:
#   scripts/vmtest/qemu.sh run-iso [--dry-run]
#   scripts/vmtest/qemu.sh boot <disk-image> [--dry-run]
#
# Knobs (see lib.sh): DASIK_VM_RAM (MiB, default 2048, cap 8192),
#   DASIK_VM_CPUS (2), DASIK_VM_DISK (8G), DASIK_VM_ISO (path to Arch ISO,
#   required for run-iso), DASIK_VM_WORKDIR.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

REPO_ROOT="$(_vmtest_repo_root)"
BOOT_TIMEOUT="${DASIK_VM_BOOT_TIMEOUT:-180}"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# KVM if available (fast); fall back to TCG with a warning (slow but works).
_accel_args() {
    if [ -w /dev/kvm ]; then echo "-enable-kvm -cpu host"; else
        warn "/dev/kvm not available — using slow TCG emulation."; echo "-cpu max"; fi
}

# NOTE: validate_ram must be called by each command DIRECTLY, never inside a
# $(...) — a `die` inside command-substitution only exits the subshell and the
# script would sail on with the RAM cap unenforced.
_base_args() {
    echo "-m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -nographic -serial mon:stdio"
}

cmd_run_iso() {
    local dry=0; [ "${1:-}" = "--dry-run" ] && dry=1
    validate_ram
    require_cmds qemu-system-x86_64 qemu-img
    [ -n "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO is unset. Point it at an Arch ISO (https://archlinux.org/download/)."
    [ -f "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO='$DASIK_VM_ISO' does not exist."

    local work; work="${DASIK_VM_WORKDIR:-$(mktemp -d /tmp/dasik-qemu.XXXXXX)}"
    local disk="$work/vda.qcow2"
    log "Creating ${DASIK_VM_DISK} qcow2 at $disk"
    [ "$dry" -eq 1 ] || qemu-img create -f qcow2 "$disk" "$DASIK_VM_DISK" >/dev/null

    # 9p-share the repo read-only so the guest can `pip install` dasik and read
    # guest-install.sh without any network round-trip.
    local args
    args="$(_base_args) $(_accel_args)"
    args="$args -cdrom $DASIK_VM_ISO -boot d"
    args="$args -drive file=$disk,if=virtio,format=qcow2"
    args="$args -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    args="$args -netdev user,id=n0 -device virtio-net,netdev=n0"

    log "QEMU command:"; echo "  qemu-system-x86_64 $args"
    cat <<EOF

Inside the guest (after the ISO boots to a root shell):
  mkdir -p /mnt-src && mount -t 9p -o trans=virtio,ro dasik /mnt-src
  /mnt-src/scripts/vmtest/guest-install.sh /mnt-src/config/<your-config>.json
  # then: poweroff, and re-run this harness with:  qemu.sh boot $disk
EOF
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching QEMU."; return 0; }
    # shellcheck disable=SC2086
    exec qemu-system-x86_64 $args
}

cmd_boot() {
    local image="" dry=0
    for a in "$@"; do case "$a" in --dry-run) dry=1;; *) image="$a";; esac; done
    [ -n "$image" ] || die "usage: qemu.sh boot <disk-image> [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    validate_ram
    require_cmds qemu-system-x86_64

    local args
    args="$(_base_args) $(_accel_args)"
    args="$args -drive file=$image,if=virtio -boot c"

    log "QEMU command:"; echo "  qemu-system-x86_64 $args"
    if [ "$dry" -eq 1 ]; then log "(dry-run) not launching QEMU."; return 0; fi

    log "Booting headless; waiting up to ${BOOT_TIMEOUT}s for a login/boot marker…"
    local out; out="$(mktemp)"
    # -no-reboot so the guest halts instead of looping; kill after the timeout.
    # shellcheck disable=SC2086
    timeout "$BOOT_TIMEOUT" qemu-system-x86_64 $args -no-reboot > "$out" 2>&1 || true
    if grep -qiE "login:|reached target|Welcome to|systemd\[1\]" "$out"; then
        log "boot layer PASSED — guest reached a boot/login marker."
        rm -f "$out"; return 0
    fi
    warn "no boot marker seen in ${BOOT_TIMEOUT}s. Last lines:"; tail -n 20 "$out" >&2
    rm -f "$out"
    die "boot layer FAILED — guest did not reach a login/boot marker."
}

case "${1:-}" in
    run-iso) shift; cmd_run_iso "$@" ;;
    boot)    shift; cmd_boot "$@" ;;
    -h|--help|"") usage 0 ;;
    *) die "unknown subcommand '$1' (try --help)" ;;
esac
