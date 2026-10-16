#!/bin/bash
# Second `drive` run for the day-2 subvolume test: a FRESH BOOT of the image
# guest-subvol-day2.sh left behind. Proves the fstab it wrote still mounts
# cleanly after a reboot and that the base config is still converged.
#   qemu.sh drive <vda.qcow2> guest-subvol-day2-reboot.sh SUBVOL-REBOOT-DONE
cd /root/repo || { echo "SUBVOL-REBOOT-DONE rc=91"; poweroff -f; }

D="python -m dasik"
FAIL=0
step() { echo "SUBVOL-$1=$2"; [ "$2" -eq 0 ] || FAIL=1; }

findmnt --verify > /tmp/verify.log 2>&1; RC=$?; cat /tmp/verify.log
ok=$RC
# The state the first script ended on: no @data anywhere, its data in @.
findmnt /srv/data > /dev/null 2>&1 && ok=1
[ -f /srv/data2/big.bin ] || ok=1
step REBOOT-FSTAB $ok

$D plan config/vm-subvol-day2.json --target / > /tmp/plan.log 2>&1; cat /tmp/plan.log
grep -q '\[btrfs_subvolumes\]' /tmp/plan.log && ok=1 || ok=0
step REBOOT $ok

echo "SUBVOL-REBOOT-DONE rc=$FAIL"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
