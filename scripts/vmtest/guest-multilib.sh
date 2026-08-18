#!/bin/bash
# multilib enabled-and-synced regression, run INSIDE the booted guest via
# `qemu.sh drive <image> guest-multilib.sh MULTILIB-DONE` after
# `qemu.sh install-driven config/vm-multilib.json`.
#
# The install already proved apply through the whole chain (conf write, -Sy,
# lib32-glibc from the repo). This asserts the machine LOOKS like that — conf
# active, sync DB present, package installed from multilib not AUR — and that
# a re-apply is silent, i.e. the new multilib_synced key is converged, not a
# perpetual plan. The enabled-but-unsynced edge (DB deleted) is asserted at
# unit level: on --target / the key is deliberately absent, so a live-target
# drive cannot exercise it honestly.
#
# QEMU-only. Never run on a real host.
set -u

cd /root || { echo "MULTILIB-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

CONFIG=/root/repo/config/vm-multilib.json
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

echo "MULTILIB: evidence — sync dir contents:"
ls -la /var/lib/pacman/sync/

echo "MULTILIB: pacman.conf must carry an active [multilib] with Include"
grep -A1 '^\[multilib\]' /etc/pacman.conf | grep -q '^Include' \
    || fail "[multilib] not active in pacman.conf"

echo "MULTILIB: the sync DB the -Sy fetched must exist"
[ -e /var/lib/pacman/sync/multilib.db ] || fail "multilib.db missing"

# lib32-acl: in the multilib repo TODAY (checked against archlinux.org on
# 2026-08-18 — lib32-glibc moved to core, several lib32 moved to the AUR; if
# this assert starts failing, re-verify which repo carries the package now).
echo "MULTILIB: lib32-acl must be installed FROM multilib"
pacman -Q lib32-acl >/dev/null 2>&1 || fail "lib32-acl not installed"
pacman -Si lib32-acl 2>/dev/null | grep -Eq '^Repository[[:space:]]*:[[:space:]]*multilib' \
    || fail "lib32-acl not attributed to the multilib repo: $(pacman -Si lib32-acl 2>&1 | head -1)"
pacman -Qm | grep -q '^lib32-acl ' && fail "lib32-acl is foreign (AUR path?)"

echo "MULTILIB: re-apply must be a no-op (multilib_synced converged)"
second_output="$(python -m dasik apply "$CONFIG" --target / --yes --no-log 2>&1)"
second_rc=$?
printf '%s\n' "$second_output"
[ "$second_rc" -eq 0 ] || fail "re-apply exited $second_rc"
printf '%s\n' "$second_output" | grep -F "No changes" >/dev/null \
    || fail "re-apply was not a no-op"

echo "MULTILIB: plan must be silent too"
plan_output="$(python -m dasik plan "$CONFIG" --target / --no-log 2>&1)"
plan_rc=$?
printf '%s\n' "$plan_output"
[ "$plan_rc" -eq 0 ] || fail "plan exited $plan_rc"
printf '%s\n' "$plan_output" | grep -F "No changes" >/dev/null \
    || fail "plan was not silent"

echo "MULTILIB-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
