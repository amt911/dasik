#!/bin/bash
# The boot entry must survive a sync + rollback (issue #189).
#
# The sequence that broke a machine: `sync` records `managed <- actual`, so the
# manifest ends up owning `root=` and `rw`; then any config that cannot
# re-derive them plans their removal, `rollback` applies it, and the entry no
# longer says where the root filesystem is. The plan afterwards was silent.
#
# This drives exactly that, and leaves the guest for the host's `boot` flow to
# start again — the only real proof. Ends with ROOT-DONE, then powers off.
set -x
cd /root/repo || { echo "ROOT-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-minimal.json
echo "ROOT: BEGIN"

echo "ROOT-A: the entry as installed"
grep options /boot/loader/entries/arch.conf

echo "ROOT-B: plan (expect: No changes — root= is derived now, not missing)"
$D plan "$C" --target / $L; echo "ROOT-PLAN-RC=$?"

echo "ROOT-C: sync — this is what makes the manifest own the live entry"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "ROOT-SYNC-RC=$?"

echo "ROOT-D: rollback, the verb that used to strip the entry"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "ROOT-ROLLBACK-RC=$?"

echo "ROOT-E: what the entry says NOW"
grep options /boot/loader/entries/arch.conf
grep -q 'root=' /boot/loader/entries/arch.conf && echo "ROOT-KEPT: ok" || echo "ROOT-KEPT: GONE"
grep -qw 'rw' /boot/loader/entries/arch.conf && echo "ROOT-RW: ok" || echo "ROOT-RW: GONE"

echo "ROOT-F: and the plan after it"
$D plan "$C" --target / $L; echo "ROOT-POSTROLLBACK-RC=$?"

echo "ROOT-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
