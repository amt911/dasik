#!/usr/bin/env bash
# VM test — Layer B: QEMU (full boot).
#
# Four flows:
#   install         UNATTENDED via archiso's script= param, which fetches
#                   guest-install-auto.sh (served over HTTP) and runs a real
#                   `dasik apply` onto the guest /dev/vda, then poweroffs. NOTE:
#                   the script= autologin hook does NOT fire on ttyS0 with recent
#                   ISOs (2025.12+) — use install-driven there.
#   install-driven  Same install, but drives the guest over an interactive serial
#                   socket (serial_driver.py logs in as root and runs the
#                   installer). Works regardless of the archiso autologin hook.
#   run-iso         boot the Arch ISO + repo over 9p for a MANUAL in-guest install.
#   boot            boot an already-installed image headless and verify it reaches
#                   a login/boot marker within a timeout.
#   boot-unlock     boot an already-installed ENCRYPTED image and type the LUKS
#                   passphrase over serial, verifying the root unlocks and boots.
#   day2            boot an already-installed image and re-apply configs against the
#                   LIVE host (target /) to prove day-2 idempotency.
#   lifecycle       boot an already-installed image and exercise generations /
#                   rollback / sync against the LIVE host (target /).
#   sync-luks       boot an ENCRYPTED image, unlock it, and verify `dasik sync`
#                   captures the real LUKS layout (luks_uuid, format:false).
#   drive           boot an installed image and run an arbitrary in-guest script
#                   over serial (unlocks LUKS if DASIK_VM_LUKS_PASSWORD is set).
#   hibernate       boot an installed image, assert the resume preconditions,
#                   `systemctl hibernate`, then boot AGAIN and prove the kernel
#                   RESTORED the image (same boot_id) instead of booting cold.
#
# Usage:
#   scripts/vmtest/qemu.sh install [config.json] [--dry-run]
#   scripts/vmtest/qemu.sh install-driven [config.json] [--dry-run]
#   scripts/vmtest/qemu.sh run-iso [--dry-run]
#   scripts/vmtest/qemu.sh boot <disk-image> [--dry-run]
#   scripts/vmtest/qemu.sh boot-unlock <encrypted-image> [passphrase] [--dry-run]
#   scripts/vmtest/qemu.sh day2 <installed-image> [--dry-run]
#   scripts/vmtest/qemu.sh hibernate <installed-image> [passphrase] [--dry-run]
#
# Knobs (see lib.sh): DASIK_VM_RAM (MiB, default 2048, cap 8192, also refused if
#   it would exceed host MemAvailable), DASIK_VM_CPUS (2), DASIK_VM_DISK (8G),
#   DASIK_VM_ISO (path to Arch ISO), DASIK_VM_TPM (1=swtpm), DASIK_VM_WORKDIR,
#   DASIK_VM_KEYDEV (raw image attached as a SECOND disk = the "pendrive" a LUKS
#   keyfile unlock reads its key from; build it with make-keydev.sh).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
. ./lib.sh

REPO_ROOT="$(_vmtest_repo_root)"
BOOT_TIMEOUT="${DASIK_VM_BOOT_TIMEOUT:-180}"

# $0 is relative to the ORIGINAL cwd and we have already cd'd into this script's
# directory, so read the header back through BASH_SOURCE instead.
usage() { sed -n '2,28p' "$(basename "${BASH_SOURCE[0]}")" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# KVM if available (fast); fall back to TCG with a warning (slow but works).
_accel_args() {
    if [ -w /dev/kvm ]; then echo "-enable-kvm -cpu host"; else
        warn "/dev/kvm not available — using slow TCG emulation."; echo "-cpu max"; fi
}

# OVMF (UEFI) firmware pflash args, with a FRESH writable VARS copy in $1 (work
# dir). Verified: `-kernel` + this pflash gives the archiso guest a real EFI env
# (/sys/firmware/efi/efivars present), so dasik's bootloader step actually
# installs systemd-boot. Echoes nothing when OVMF isn't installed.
_ovmf_args() {
    local work="$1" code="" vars=""
    for c in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/OVMF/OVMF_CODE.fd \
             /usr/share/ovmf/x64/OVMF_CODE.fd /usr/share/edk2-ovmf/x64/OVMF_CODE.fd; do
        [ -f "$c" ] && { code="$c"; break; }
    done
    for v in /usr/share/edk2/x64/OVMF_VARS.4m.fd /usr/share/OVMF/OVMF_VARS.fd \
             /usr/share/ovmf/x64/OVMF_VARS.fd /usr/share/edk2-ovmf/x64/OVMF_VARS.fd; do
        [ -f "$v" ] && { vars="$v"; break; }
    done
    if [ -n "$code" ] && [ -n "$vars" ]; then
        cp -f "$vars" "$work/OVMF_VARS.fd"
        echo "-drive if=pflash,unit=0,format=raw,readonly=on,file=$code -drive if=pflash,unit=1,format=raw,file=$work/OVMF_VARS.fd"
    fi
}

# Second virtio disk — the "virtual pendrive" a LUKS keyfile unlock reads its key
# from. Attached to every flow that boots something (install, boot, unlock,
# drive), because the key device has to be present at INSTALL time (dasik creates
# and enrolls the keyfile on it) and again at BOOT time (the initramfs mounts it).
# Leave DASIK_VM_KEYDEV unset and nothing is attached — every other flow is
# unchanged. Create the image with make-keydev.sh.
#
# NOTE: raw, and second in the -drive order, so the install target stays /dev/vda.
_keydev_args() {
    [ -n "${DASIK_VM_KEYDEV:-}" ] || return 0
    [ -f "$DASIK_VM_KEYDEV" ] || die "DASIK_VM_KEYDEV='$DASIK_VM_KEYDEV' does not exist (run make-keydev.sh)."
    echo "-drive file=$DASIK_VM_KEYDEV,if=virtio,format=raw"
}

# Software TPM 2.0 (swtpm) for TPM2 LUKS auto-unlock tests. Sets the globals
# TPM_QARGS (qemu device args) and SWTPM_PID; NOT a $(...) helper because it must
# start the daemon in the caller's shell. Enable with DASIK_VM_TPM=1. The TPM
# STATE lives under <work>/tpm and persists across the install + boot runs, so a
# key enrolled at install unlocks at boot.
TPM_QARGS=""; SWTPM_PID=""
_start_tpm() {
    TPM_QARGS=""; SWTPM_PID=""
    [ "${DASIK_VM_TPM:-0}" = "1" ] || return 0
    command -v swtpm >/dev/null 2>&1 || { warn "DASIK_VM_TPM=1 but swtpm not installed — skipping TPM."; return 0; }
    local dir="$1/tpm"; mkdir -p "$dir"
    swtpm socket --tpmstate "dir=$dir" --ctrl "type=unixio,path=$dir/sock" \
        --tpm2 --flags not-need-init >/dev/null 2>&1 &
    SWTPM_PID=$!
    sleep 1
    TPM_QARGS="-chardev socket,id=chrtpm,path=$dir/sock -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0"
    log "TPM2 (swtpm) enabled: state $dir"
}
_stop_tpm() { [ -n "$SWTPM_PID" ] && kill "$SWTPM_PID" 2>/dev/null; SWTPM_PID=""; }

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

    # UEFI images need OVMF to boot (fresh VARS copy kept beside the image).
    local ovmf; ovmf="$(_ovmf_args "$(dirname "$image")")"
    [ -z "$ovmf" ] && warn "No OVMF — a UEFI-installed image will not boot without it."
    local args
    _start_tpm "$(dirname "$image")"
    args="$(_base_args) $(_accel_args) $ovmf $TPM_QARGS"
    args="$args -drive file=$image,if=virtio -boot c $(_keydev_args)"

    log "QEMU command:"; echo "  qemu-system-x86_64 $args"
    if [ "$dry" -eq 1 ]; then log "(dry-run) not launching QEMU."; return 0; fi

    log "Booting headless; waiting up to ${BOOT_TIMEOUT}s for a login/boot marker…"
    local out; out="$(mktemp)"
    # -no-reboot so the guest halts instead of looping; kill after the timeout.
    # shellcheck disable=SC2086
    timeout "$BOOT_TIMEOUT" qemu-system-x86_64 $args -no-reboot > "$out" 2>&1 || true
    _stop_tpm
    if grep -qiE "login:|reached target|Welcome to|systemd\[1\]" "$out"; then
        log "boot layer PASSED — guest reached a boot/login marker."
        rm -f "$out"; return 0
    fi
    warn "no boot marker seen in ${BOOT_TIMEOUT}s. Last lines:"; tail -n 20 "$out" >&2
    rm -f "$out"
    die "boot layer FAILED — guest did not reach a login/boot marker."
}

# Fully UNATTENDED install: boot the ISO's kernel with archiso's `script=` param
# pointing at guest-install-auto.sh (served over HTTP), which runs `dasik apply`
# against the guest's /dev/vda, then poweroffs. The host waits for the
# "DASIK-VM-DONE" marker on the serial console.
cmd_install() {
    local config="config/vm-minimal.json" dry=0
    for a in "$@"; do case "$a" in --dry-run) dry=1;; *.json) config="$a";; esac; done
    validate_ram
    require_cmds qemu-system-x86_64 qemu-img bsdtar python3
    [ -n "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO unset (path to an Arch ISO)."
    [ -f "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO '$DASIK_VM_ISO' not found."
    [ -f "$REPO_ROOT/$config" ] || [ -f "$config" ] || die "config '$config' not found."

    # Work dir on a REAL disk, never /tmp (often tmpfs = RAM-backed).
    local work="${DASIK_VM_WORKDIR:-$HOME/.cache/dasik-vmtest}"
    mkdir -p "$work/http"
    local label; label="$(blkid -o value -s LABEL "$DASIK_VM_ISO" 2>/dev/null)"
    [ -n "$label" ] || die "could not read the ISO volume label."

    log "Extracting kernel + initramfs from the ISO (bsdtar, no root)"
    bsdtar -xf "$DASIK_VM_ISO" -C "$work" \
        arch/boot/x86_64/vmlinuz-linux arch/boot/x86_64/initramfs-linux.img
    local kernel="$work/arch/boot/x86_64/vmlinuz-linux"
    local initrd="$work/arch/boot/x86_64/initramfs-linux.img"

    local disk="$work/vda.qcow2"
    log "Creating ${DASIK_VM_DISK} qcow2 at $disk"
    qemu-img create -f qcow2 "$disk" "$DASIK_VM_DISK" >/dev/null

    # cwd is this script's dir (set at the top), so the guest installer is local.
    cp guest-install-auto.sh "$work/http/install.sh"
    local port="${DASIK_VM_HTTP_PORT:-8712}"

    # UEFI: `-kernel` + OVMF pflash gives the guest a real EFI env (efivars), so
    # dasik's bootloader step (bootctl) installs for real and a re-apply is a
    # no-op. Without OVMF the guest boots BIOS-style and the bootloader step
    # can't complete (partition/pacstrap/config/idempotency still verified).
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -n "$ovmf" ]; then
        log "UEFI firmware: OVMF (efivars enabled) — bootloader step will run."
    else
        warn "No OVMF — guest boots without UEFI; dasik's bootloader step can't complete. Install edk2-ovmf for a bootable+idempotent bootloader result."
    fi

    # Pass the chosen config to the guest via the kernel cmdline so it installs
    # THIS config, not the hard-coded default.
    local append="archisobasedir=arch archisolabel=$label cow_spacesize=2G copytoram=n console=ttyS0,115200 dasik_config=$config script=http://10.0.2.2:$port/install.sh"
    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -nographic -display none"
    qargs="$qargs $ovmf -kernel $kernel -initrd $initrd -append \"$append\""
    # ISO on virtio (OVMF does not enumerate the IDE -cdrom); qcow2 as vda.
    qargs="$qargs -drive file=$disk,if=virtio,format=qcow2 $(_keydev_args)"
    qargs="$qargs -drive file=$DASIK_VM_ISO,if=virtio,media=cdrom,format=raw"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    _start_tpm "$work"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0 $TPM_QARGS"
    qargs="$qargs -serial file:$work/serial.log -no-reboot"

    log "QEMU install command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    : > "$work/serial.log"
    log "Serving installer on 127.0.0.1:$port and booting guest (this takes minutes)…"
    ( cd "$work/http" && python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 ) &
    local http_pid=$!
    local timeout_s="${DASIK_VM_INSTALL_TIMEOUT:-900}"
    eval "timeout $timeout_s qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!
    while kill -0 "$qpid" 2>/dev/null; do
        grep -qa "DASIK-VM-DONE" "$work/serial.log" 2>/dev/null && break
        sleep 5
    done
    # `|| true`: the guest powers itself off, so by now qemu/http are usually
    # already gone and a failing kill would abort the run under `set -e` —
    # turning a SUCCESSFUL install into rc=1 before the verdict is printed.
    kill "$qpid" 2>/dev/null || true; kill "$http_pid" 2>/dev/null || true
    _stop_tpm; wait 2>/dev/null || true

    echo; log "Install serial highlights:"
    grep -a "DASIK-VM" "$work/serial.log" 2>/dev/null | tail -25
    if grep -qa "DASIK-VM-DONE rc=0" "$work/serial.log"; then
        log "install layer: dasik apply completed (rc=0). qcow2: $disk"
        return 0
    fi
    warn "install did not report rc=0 — see $work/serial.log (may be a dasik gap or the OVMF/UEFI prerequisite)."
    return 1
}

# Like `install`, but drives the guest over an interactive serial socket instead
# of relying on archiso's `script=` autologin hook — which does NOT fire on ttyS0
# with recent ISOs (2025.12+), leaving the guest stuck at `archiso login:`. Boots
# with the serial on a unix socket and hands it to serial_driver.py, which logs in
# as root and runs the same guest installer. Use this on modern ISOs.
cmd_install_driven() {
    local config="config/vm-minimal.json" dry=0
    for a in "$@"; do case "$a" in --dry-run) dry=1;; *.json) config="$a";; esac; done
    validate_ram
    require_cmds qemu-system-x86_64 qemu-img bsdtar python3
    [ -n "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO unset (path to an Arch ISO)."
    [ -f "$DASIK_VM_ISO" ] || die "DASIK_VM_ISO '$DASIK_VM_ISO' not found."
    [ -f "$REPO_ROOT/$config" ] || [ -f "$config" ] || die "config '$config' not found."

    local work="${DASIK_VM_WORKDIR:-$HOME/.cache/dasik-vmtest}"
    mkdir -p "$work/http"
    local label; label="$(blkid -o value -s LABEL "$DASIK_VM_ISO" 2>/dev/null)"
    [ -n "$label" ] || die "could not read the ISO volume label."

    log "Extracting kernel + initramfs from the ISO"
    bsdtar -xf "$DASIK_VM_ISO" -C "$work" \
        arch/boot/x86_64/vmlinuz-linux arch/boot/x86_64/initramfs-linux.img
    local kernel="$work/arch/boot/x86_64/vmlinuz-linux"
    local initrd="$work/arch/boot/x86_64/initramfs-linux.img"

    local disk="$work/vda.qcow2"
    log "Creating ${DASIK_VM_DISK} qcow2 at $disk"
    qemu-img create -f qcow2 "$disk" "$DASIK_VM_DISK" >/dev/null

    cp guest-install-auto.sh "$work/http/install.sh"
    local port="${DASIK_VM_HTTP_PORT:-8712}"
    local sock="$work/serial.sock"; rm -f "$sock"

    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -n "$ovmf" ]; then
        log "UEFI firmware: OVMF (efivars enabled)."
    else
        warn "No OVMF — bootloader step can't complete."
    fi

    # NOTE: no `script=`; the driver runs the installer after logging in.
    local append="archisobasedir=arch archisolabel=$label cow_spacesize=2G copytoram=n console=ttyS0,115200 dasik_config=$config"
    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -kernel $kernel -initrd $initrd -append \"$append\""
    qargs="$qargs -drive file=$disk,if=virtio,format=qcow2 $(_keydev_args)"
    qargs="$qargs -drive file=$DASIK_VM_ISO,if=virtio,media=cdrom,format=raw"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    _start_tpm "$work"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0 $TPM_QARGS"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU install command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; _stop_tpm; return 0; }

    ( cd "$work/http" && python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 ) &
    local http_pid=$!
    local timeout_s="${DASIK_VM_INSTALL_TIMEOUT:-1500}"
    log "Booting guest and driving the install over serial (this takes minutes)…"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    python3 serial_driver.py "$sock" "$port" "$timeout_s" | tee "$work/serial.log"
    local rc=${PIPESTATUS[0]}
    set -e

    # `|| true`: the guest powers itself off, so by now qemu/http are usually
    # already gone and a failing kill would abort the run under `set -e` —
    # turning a SUCCESSFUL install into rc=1 before the verdict is printed.
    kill "$qpid" 2>/dev/null || true; kill "$http_pid" 2>/dev/null || true
    _stop_tpm; wait 2>/dev/null || true

    echo; log "Install serial highlights:"
    grep -a "DASIK-VM" "$work/serial.log" 2>/dev/null | tail -25
    if [ "$rc" -eq 0 ] && grep -qa "DASIK-VM-DONE rc=0" "$work/serial.log"; then
        log "install layer: dasik apply completed (rc=0). qcow2: $disk"
        return 0
    fi
    warn "install did not report rc=0 — see $work/serial.log."
    return 1
}

# Day-2 convergence check: boot an ALREADY-INSTALLED image (installed from
# vm-day2.json, which autologins root on ttyS0 and ships python-pydantic/colorama)
# with the repo on 9p, and run guest-day2.sh — which re-applies configs against the
# LIVE host (target /). Proves re-apply is a no-op and a modified config changes
# only the delta. Usage: qemu.sh day2 <installed-image.qcow2> [--dry-run]
cmd_day2() {
    local image="" dry=0
    for a in "$@"; do case "$a" in --dry-run) dry=1;; *) image="$a";; esac; done
    [ -n "$image" ] || die "usage: qemu.sh day2 <installed-image> [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    validate_ram
    require_cmds qemu-system-x86_64 python3

    local work; work="$(dirname "$image")"
    local sock="$work/day2.sock"; rm -f "$sock"
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -z "$ovmf" ]; then die "day2 needs OVMF to boot the UEFI image."; fi

    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -drive file=$image,if=virtio,format=qcow2 -boot c"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU day2 command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local timeout_s="${DASIK_VM_DAY2_TIMEOUT:-600}"
    log "Booting installed image and driving day-2 apply against target / …"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    python3 day2_driver.py "$sock" "$timeout_s" | tee "$work/day2.log"
    local rc=${PIPESTATUS[0]}
    set -e
    kill "$qpid" 2>/dev/null || true; wait 2>/dev/null || true

    echo; log "Day-2 highlights:"
    grep -aE "DAY2|No changes|\[files\]|\[packages\]|\[network\]" "$work/day2.log" 2>/dev/null | tail -30
    if [ "$rc" -eq 0 ] && grep -qa "DAY2-DONE rc=0" "$work/day2.log"; then
        log "day2 layer PASSED — re-apply/converge on the live host completed."
        return 0
    fi
    warn "day2 did not complete cleanly — see $work/day2.log."
    return 1
}

# Generation lifecycle check: boot an already-installed image (from vm-day2.json)
# and exercise dasik's NixOS-like generation management against the LIVE host
# (target /): apply → records a generation, `generations` lists them, `rollback`
# re-converges to a prior generation (removing an owned file), and `sync` captures
# reality into a config. Usage: qemu.sh lifecycle <installed-image.qcow2> [--dry-run]
cmd_lifecycle() {
    local image="" dry=0
    for a in "$@"; do case "$a" in --dry-run) dry=1;; *) image="$a";; esac; done
    [ -n "$image" ] || die "usage: qemu.sh lifecycle <installed-image> [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    validate_ram
    require_cmds qemu-system-x86_64 python3

    local work; work="$(dirname "$image")"
    local sock="$work/lifecycle.sock"; rm -f "$sock"
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -z "$ovmf" ]; then die "lifecycle needs OVMF to boot the UEFI image."; fi

    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -drive file=$image,if=virtio,format=qcow2 -boot c"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU lifecycle command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local timeout_s="${DASIK_VM_LIFECYCLE_TIMEOUT:-600}"
    log "Booting installed image and driving generation lifecycle against target / …"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    python3 day2_driver.py "$sock" "$timeout_s" guest-lifecycle.sh LIFE-DONE | tee "$work/lifecycle.log"
    local rc=${PIPESTATUS[0]}
    set -e
    kill "$qpid" 2>/dev/null || true; wait 2>/dev/null || true

    echo; log "Lifecycle highlights:"
    grep -aE "LIFE-|Generation|No changes|Synced|Rolled back" "$work/lifecycle.log" 2>/dev/null | tail -40
    if [ "$rc" -eq 0 ] && grep -qa "LIFE-DONE rc=0" "$work/lifecycle.log"; then
        log "lifecycle layer PASSED — generations/rollback/sync work on the live host."
        return 0
    fi
    warn "lifecycle did not complete cleanly — see $work/lifecycle.log."
    return 1
}

# Encrypted-sync check: boot an ENCRYPTED installed image (from vm-day2-luks.json:
# LUKS + autologin root + python), unlock it over serial, and run guest-sync-luks.sh
# — which runs `dasik sync` against the live host and asserts the disk/LUKS layout
# is captured (real luks_uuid, format:false, password dropped). The passphrase is
# handed to the driver via DASIK_VM_LUKS_PASSWORD.
# Usage: qemu.sh sync-luks <encrypted-installed-image.qcow2> [passphrase] [--dry-run]
cmd_sync_luks() {
    local image="" pass="" dry=0
    for a in "$@"; do case "$a" in
        --dry-run) dry=1;;
        *.qcow2|*.img|*.raw) image="$a";;
        *) if [ -z "$image" ] && [ -f "$a" ]; then image="$a"; else pass="$a"; fi;;
    esac; done
    [ -n "$image" ] || die "usage: qemu.sh sync-luks <encrypted-image> [passphrase] [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    pass="${pass:-${DASIK_VM_LUKS_PASSWORD:-dasik-test-pass}}"
    validate_ram
    require_cmds qemu-system-x86_64 python3

    local work; work="$(dirname "$image")"
    local sock="$work/syncluks.sock"; rm -f "$sock"
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -z "$ovmf" ]; then die "sync-luks needs OVMF to boot the UEFI image."; fi

    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -drive file=$image,if=virtio,format=qcow2 -boot c"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU sync-luks command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local timeout_s="${DASIK_VM_SYNCLUKS_TIMEOUT:-600}"
    log "Booting encrypted image, unlocking, and driving 'dasik sync' against target / …"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    DASIK_VM_LUKS_PASSWORD="$pass" \
        python3 day2_driver.py "$sock" "$timeout_s" guest-sync-luks.sh SYNCLUKS-DONE \
        | tee "$work/syncluks.log"
    local rc=${PIPESTATUS[0]}
    set -e
    kill "$qpid" 2>/dev/null || true; wait 2>/dev/null || true

    echo; log "Encrypted-sync highlights:"
    grep -aE "SYNCLUKS-|luks_uuid" "$work/syncluks.log" 2>/dev/null | tail -20
    if [ "$rc" -eq 0 ] && grep -qa "SYNCLUKS-DONE rc=0" "$work/syncluks.log"; then
        log "sync-luks layer PASSED — sync captured the real LUKS layout on the live host."
        return 0
    fi
    warn "sync-luks did not complete cleanly — see $work/syncluks.log."
    return 1
}

# LUKS boot-unlock check: boot an ENCRYPTED installed image (from vm-luks.json,
# which sets console=ttyS0 so the initramfs passphrase prompt lands on serial) and
# type the LUKS passphrase over serial. Proves the encrypted root unlocks with the
# declared passphrase and the system boots to login — the piece a headless VM can't
# do with the plain `boot` subcommand (which only reads, never types).
# Usage: qemu.sh boot-unlock <encrypted-image.qcow2> [passphrase] [--dry-run]
cmd_boot_unlock() {
    local image="" pass="" dry=0
    for a in "$@"; do case "$a" in
        --dry-run) dry=1;;
        *.qcow2|*.img|*.raw) image="$a";;
        *) if [ -z "$image" ] && [ -f "$a" ]; then image="$a"; else pass="$a"; fi;;
    esac; done
    [ -n "$image" ] || die "usage: qemu.sh boot-unlock <encrypted-image> [passphrase] [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    pass="${pass:-${DASIK_VM_LUKS_PASSWORD:-dasik-test-pass}}"
    validate_ram
    require_cmds qemu-system-x86_64 python3

    local work; work="$(dirname "$image")"
    local sock="$work/unlock.sock"; rm -f "$sock"
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -z "$ovmf" ]; then die "boot-unlock needs OVMF to boot the UEFI image."; fi

    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -drive file=$image,if=virtio,format=qcow2 -boot c $(_keydev_args)"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU boot-unlock command:"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local timeout_s="${DASIK_VM_UNLOCK_TIMEOUT:-240}"
    log "Booting encrypted image and typing the LUKS passphrase over serial …"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    python3 boot_unlock_driver.py "$sock" "$pass" "$timeout_s" | tee "$work/unlock.log"
    local rc=${PIPESTATUS[0]}
    set -e
    kill "$qpid" 2>/dev/null || true; wait 2>/dev/null || true

    echo; log "Boot-unlock highlights:"
    grep -aiE "passphrase|unlock|reached target|login:|PASS|FAIL" "$work/unlock.log" 2>/dev/null | tail -20
    if [ "$rc" -eq 0 ]; then
        log "boot-unlock layer PASSED — encrypted root unlocked with the passphrase and the system booted."
        return 0
    fi
    warn "boot-unlock did not complete cleanly — see $work/unlock.log."
    return 1
}

# Generic driver: boot an already-installed image (9p repo + serial socket, OVMF,
# NAT) and run an arbitrary in-guest script over serial via day2_driver.py. Unlocks
# LUKS first if DASIK_VM_LUKS_PASSWORD is set. This is the reusable building block
# behind day2/lifecycle/sync-luks for one-off in-guest checks.
# Usage: qemu.sh drive <installed-image> <guest-script.sh> <DONE-MARKER> [--dry-run]
cmd_drive() {
    local image="" guest="" marker="" dry=0
    for a in "$@"; do case "$a" in
        --dry-run) dry=1;;
        *.sh) guest="$a";;
        *.qcow2|*.img|*.raw) image="$a";;
        *) if [ -z "$image" ] && [ -f "$a" ]; then image="$a"; else marker="$a"; fi;;
    esac; done
    [ -n "$image" ] && [ -n "$guest" ] && [ -n "$marker" ] || \
        die "usage: qemu.sh drive <image> <guest-script.sh> <DONE-MARKER> [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    # day2_driver.py runs scripts/vmtest/<guest>; accept a path or a bare name.
    guest="$(basename "$guest")"
    [ -f "$REPO_ROOT/scripts/vmtest/$guest" ] || die "guest script '$guest' not found in scripts/vmtest/."
    validate_ram
    require_cmds qemu-system-x86_64 python3

    local work; work="$(dirname "$image")"
    local sock="$work/drive.sock"; rm -f "$sock"
    local ovmf; ovmf="$(_ovmf_args "$work")"
    if [ -z "$ovmf" ]; then die "drive needs OVMF to boot the UEFI image."; fi

    local qargs="-enable-kvm -cpu host -m $DASIK_VM_RAM -smp $DASIK_VM_CPUS -display none -monitor none"
    qargs="$qargs $ovmf -drive file=$image,if=virtio,format=qcow2 -boot c $(_keydev_args)"
    qargs="$qargs -virtfs local,path=$REPO_ROOT,mount_tag=dasik,security_model=none,readonly=on"
    qargs="$qargs -netdev user,id=n0 -device virtio-net,netdev=n0"
    qargs="$qargs -serial unix:$sock,server,nowait -no-reboot"

    log "QEMU drive command ($guest -> $marker):"; echo "  qemu-system-x86_64 $qargs"
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local timeout_s="${DASIK_VM_DRIVE_TIMEOUT:-900}"
    log "Booting installed image and driving $guest against target / …"
    # `exec`: without it $! is the SUBSHELL bash forks for `eval`, and the kill
    # below leaves the real qemu running — holding the qcow2 lock, so the NEXT
    # run starts a qemu that cannot open the image and prints nothing at all.
    eval "exec qemu-system-x86_64 $qargs" >/dev/null 2>&1 &
    local qpid=$!

    set +e
    python3 day2_driver.py "$sock" "$timeout_s" "$guest" "$marker" | tee "$work/drive.log"
    local rc=${PIPESTATUS[0]}
    set -e
    kill "$qpid" 2>/dev/null || true; wait 2>/dev/null || true

    echo; log "Drive highlights:"
    grep -aE "OK:|BAD:|$marker|No changes|Rolled back|Synced" "$work/drive.log" 2>/dev/null | tail -40
    if [ "$rc" -eq 0 ] && grep -qa "$marker rc=0" "$work/drive.log"; then
        log "drive layer PASSED — $guest completed cleanly."
        return 0
    fi
    warn "drive did not complete cleanly — see $work/drive.log."
    return 1
}

# Hibernate/resume check: boot an installed image, assert the preconditions the
# initramfs must satisfy (resume= on the cmdline, the swap device active,
# /sys/power/resume set, logind's CanHibernate), hibernate, then boot AGAIN and
# compare boot_id — preserved by a resume, regenerated by a cold boot. This is
# what caught dracut silently dropping its `resume` module from a chroot build.
# Usage: qemu.sh hibernate <installed-image> [passphrase] [--dry-run]
cmd_hibernate() {
    local image="" passphrase="" dry=0
    for a in "$@"; do case "$a" in
        --dry-run) dry=1;;
        *.qcow2|*.img|*.raw) image="$a";;
        *) if [ -z "$image" ] && [ -f "$a" ]; then image="$a"; else passphrase="$a"; fi;;
    esac; done
    [ -n "$image" ] || die "usage: qemu.sh hibernate <installed-image> [passphrase] [--dry-run]"
    [ -f "$image" ] || die "image '$image' does not exist."
    passphrase="${passphrase:-${DASIK_VM_LUKS_PASSWORD:-}}"
    [ -n "$passphrase" ] || die "hibernate needs the LUKS passphrase (argument or DASIK_VM_LUKS_PASSWORD)."
    validate_ram
    require_cmds qemu-system-x86_64 python3

    log "Hibernate/resume check on $image (two boots, ${DASIK_VM_RAM} MiB)."
    [ "$dry" -eq 1 ] && { log "(dry-run) not launching."; return 0; }

    local work; work="$(dirname "$image")"
    set +e
    python3 hibernate_driver.py "$image" "$passphrase" "$REPO_ROOT" \
        "$DASIK_VM_RAM" "$DASIK_VM_CPUS" | tee "$work/hibernate.log"
    local rc=${PIPESTATUS[0]}
    set -e

    if [ "$rc" -eq 0 ] && grep -qa "RESUMED FROM HIBERNATION: True" "$work/hibernate.log"; then
        log "hibernate layer PASSED — the second boot restored the image."
        return 0
    fi
    warn "hibernate did NOT resume — see $work/hibernate.log."
    return 1
}

case "${1:-}" in
    run-iso)        shift; cmd_run_iso "$@" ;;
    drive)          shift; cmd_drive "$@" ;;
    install)        shift; cmd_install "$@" ;;
    install-driven) shift; cmd_install_driven "$@" ;;
    day2)           shift; cmd_day2 "$@" ;;
    boot)           shift; cmd_boot "$@" ;;
    boot-unlock)    shift; cmd_boot_unlock "$@" ;;
    lifecycle)      shift; cmd_lifecycle "$@" ;;
    sync-luks)      shift; cmd_sync_luks "$@" ;;
    hibernate)      shift; cmd_hibernate "$@" ;;
    -h|--help|"") usage 0 ;;
    *) die "unknown subcommand '$1' (try --help)" ;;
esac
