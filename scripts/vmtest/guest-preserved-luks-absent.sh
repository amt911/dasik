#!/bin/bash
# The machine of config/vm-preserved-luks.json booted WITHOUT its kept disk
# (vdb detached). The kept volume is declared `nofail`, so a missing data disk
# must cost nothing but its mount: the boot reaches the autologin shell this
# script runs in (an emergency shell never autologins, so the drive would time
# out instead of printing ABSENT-DONE), nothing failed hard, and Games is not
# mounted.
#
# Not a unit test: only a real boot shows what systemd does with a crypttab /
# rd.luks.options / fstab entry whose device never appears.
#
# Ends with ABSENT-DONE rc=<n>, then powers off.
set -x
rc=0
echo "ABSENT-A: the kept disk is really absent"
[ -e /dev/vdb ] && { echo "ABSENT-A FAILED: /dev/vdb is attached"; rc=1; }

echo "ABSENT-B: the boot reached a usable state"
state=$(systemctl is-system-running --wait 2>/dev/null)
echo "ABSENT-STATE=$state"
case "$state" in running|degraded) : ;; *) echo "ABSENT-B FAILED: state $state"; rc=1 ;; esac
systemctl --failed --no-legend
systemctl is-active local-fs.target || { echo "ABSENT-B FAILED: local-fs.target"; rc=1; }

echo "ABSENT-C: Games is simply not mounted"
findmnt /home/tester/Games && { echo "ABSENT-C FAILED: something is mounted there"; rc=1; }

echo "ABSENT-DONE rc=$rc"
sync
sleep 2
poweroff -f
