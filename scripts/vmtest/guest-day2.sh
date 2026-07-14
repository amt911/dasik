#!/bin/bash
# Day-2 convergence check, run INSIDE the booted (already-installed) guest.
#
# The repo is 9p-mounted at /root/repo and dasik's deps (pydantic/colorama) come
# from pacman, so dasik runs straight from source against the LIVE host (target /,
# not /mnt). Proves: re-applying the same config is a no-op, applying a modified
# config changes ONLY the delta, and re-applying the modified config is a no-op.
# Emits DAY2-* markers the host driver greps; ends with DAY2-DONE then powers off.
set -x
cd /root/repo || { echo "DAY2-DONE rc=91"; poweroff -f; }

D="python -m dasik"
echo "DAY2: BEGIN (target / = the live booted host)"

echo "DAY2-A: re-apply the SAME config (expect: No changes)"
$D apply config/vm-day2.json --target / --yes
echo "DAY2-A-RC=$?"

echo "DAY2-B: plan the MODIFIED config (expect: only the one marker file)"
$D plan config/vm-day2-mod.json --target /

echo "DAY2-C: apply the MODIFIED config (expect: only the delta applied)"
$D apply config/vm-day2-mod.json --target / --yes
echo "DAY2-C-RC=$?"
if [ -f /etc/dasik-day2-marker.conf ]; then echo "DAY2-MARKER: present"; else echo "DAY2-MARKER: MISSING"; fi

echo "DAY2-D: re-apply the MODIFIED config (expect: No changes again)"
$D apply config/vm-day2-mod.json --target / --yes
echo "DAY2-D-RC=$?"

echo "DAY2-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
