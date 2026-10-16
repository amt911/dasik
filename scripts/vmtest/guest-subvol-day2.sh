#!/bin/bash
# Day-2 btrfs subvolume add / move / remove, run INSIDE the booted (installed)
# guest. Repo is 9p-mounted at /root/repo; dasik runs from source against the
# LIVE host (target /). Needs config/vm-subvol-day2.json installed first:
#   qemu.sh install-driven config/vm-subvol-day2.json
#   qemu.sh drive <vda.qcow2> guest-subvol-day2.sh SUBVOL-DONE
# then, for the reboot check, a SECOND drive run (fresh boot of the same image):
#   qemu.sh drive <vda.qcow2> guest-subvol-day2-reboot.sh SUBVOL-REBOOT-DONE
#
# Every step computes its rc from its assertions (0 = pass) and echoes
# SUBVOL-<STEP>=<rc>. No `set -e`: a failing step must still print its marker
# and let the rest run. Start from a FRESH qcow2: the image persists between
# drives, and this script mutates it.
cd /root/repo || { echo "SUBVOL-DONE rc=91"; poweroff -f; }

D="python -m dasik"
BASE=config/vm-subvol-day2.json
ADD=config/vm-subvol-day2-add.json
MOVE=config/vm-subvol-day2-move.json
LOG=/tmp/subvol.log
TOP=/mnt/top
FAIL=0

# step NAME RC  -> echo the marker, remember any failure
step() { echo "SUBVOL-$1=$2"; [ "$2" -eq 0 ] || FAIL=1; }
# plan_ok FILE RC: the plan REALLY ran. rc 0, non-empty, no traceback, and it
# printed what Plan.render prints ("No changes - system matches config." or one
# or more "  <+|~|-> [domain] ..." lines). A crash/empty log is NOT "silent".
plan_ok() {
    [ "$2" -eq 0 ] && [ -s "$1" ] && ! grep -q 'Traceback' "$1" \
        && grep -Eq '^No changes|^  [-+~!] \[[a-z_0-9]+\]' "$1"
}
# silent FILE RC: the plan ran AND has no [btrfs_subvolumes] line.
silent() { plan_ok "$1" "$2" && ! grep -q '\[btrfs_subvolumes\]' "$1"; }
root_dev() { findmnt -no SOURCE / | sed 's/\[.*//'; }
top_mount() { mkdir -p $TOP; mountpoint -q $TOP || mount -o subvolid=5 "$(root_dev)" $TOP; }
top_umount() { mountpoint -q $TOP && umount $TOP; }

echo "SUBVOL: BEGIN (target / = the live booted host)"
# A previous run on this image would test a machine it already broke.
if [ -e /srv/data/big.bin ] || [ -e /srv/data2/big.bin ]; then
    echo "SUBVOL: stale state from an earlier run; start from a fresh qcow2"
    step STALE 1
fi

# 1 - the base config is converged
$D plan $BASE --target / > $LOG 2>&1; PRC=$?; cat $LOG
silent $LOG $PRC; step BASE $?

# 2 - seed data at the future mountpoint
mkdir -p /srv/data
head -c 50M /dev/urandom > /srv/data/big.bin
SUM=$(sha256sum /srv/data/big.bin | cut -d' ' -f1)
[ -n "$SUM" ]; step SEED $?

# 3 - the add is planned
$D plan $ADD --target / > $LOG 2>&1; PRC=$?; cat $LOG
plan_ok $LOG $PRC && grep -q '+ \[btrfs_subvolumes\] install ROOT:@data' $LOG; step PLAN-ADD $?

# 4 - the add is applied
$D apply $ADD --target / --yes; RC=$?
ok=$RC
findmnt -no OPTIONS /srv/data | grep -q 'subvol=/@data' || ok=1
[ "$(sha256sum /srv/data/big.bin | cut -d' ' -f1)" = "$SUM" ] || ok=1
top_mount
if [ -d $TOP/@/srv/data ] && [ -z "$(ls -A $TOP/@/srv/data)" ]; then :; else ok=1; echo "SUBVOL: @/srv/data not empty"; fi
top_umount
grep -q '/srv/data btrfs' /etc/fstab || ok=1
findmnt --verify > /tmp/verify.log 2>&1 || { ok=1; cat /tmp/verify.log; }
step APPLY-ADD $ok

# 5 - converged after the add
$D plan $ADD --target / > $LOG 2>&1; PRC=$?; cat $LOG
silent $LOG $PRC; step REPLAN-ADD $?

# 6 - sync reads the day-2 subvolume back, and the capture re-plans to nothing
# From an EMPTY seed, so the disks block is discovered off the machine itself
# (declared disks would just echo the declaration back). sync rewrites the file
# in place (positional config, .bak beside it).
rm -f /tmp/s.json /tmp/s.json.bak
echo '{}' > /tmp/s.json
$D sync /tmp/s.json --target / --no-log; RC=$?
ok=$RC
$D check /tmp/s.json || ok=1
grep -q '@data' /tmp/s.json || { ok=1; echo "SUBVOL: sync did not capture @data"; }
$D plan /tmp/s.json --target / > $LOG 2>&1; PRC=$?; cat $LOG
silent $LOG $PRC || ok=1
step SYNC $ok

# 7 - move: /srv/data -> /srv/data2, data stays in the subvolume
$D apply $MOVE --target / --yes; RC=$?
ok=$RC
findmnt -no OPTIONS /srv/data2 | grep -q 'subvol=/@data' || ok=1
findmnt /srv/data > /dev/null 2>&1 && ok=1
grep -q '/srv/data2 btrfs' /etc/fstab || ok=1
grep -q ' /srv/data btrfs' /etc/fstab && ok=1
[ "$(sha256sum /srv/data2/big.bin | cut -d' ' -f1)" = "$SUM" ] || ok=1
findmnt --verify > /tmp/verify.log 2>&1 || { ok=1; cat /tmp/verify.log; }
step MOVE $ok

# 8 - remove: back to the base config; data comes back into @
$D apply $BASE --target / --yes; RC=$?
ok=$RC
[ "$(btrfs subvolume list / | grep -c '@data$')" = "0" ] || ok=1
[ -f /srv/data2/big.bin ] || ok=1
findmnt /srv/data2 > /dev/null 2>&1 && ok=1
[ "$(sha256sum /srv/data2/big.bin | cut -d' ' -f1)" = "$SUM" ] || ok=1
grep -q '@data' /etc/fstab && ok=1
top_mount
[ -f $TOP/@/srv/data2/big.bin ] || ok=1
top_umount
findmnt --verify > /tmp/verify.log 2>&1 || { ok=1; cat /tmp/verify.log; }
$D plan $BASE --target / > $LOG 2>&1; PRC=$?; cat $LOG
silent $LOG $PRC || ok=1
step REMOVE $ok

echo "SUBVOL-DONE rc=$FAIL"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
