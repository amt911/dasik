#!/bin/bash
# Second `drive` run for the day-2 subvolume test: a FRESH BOOT of the image
# guest-subvol-day2.sh left behind. Proves the fstab it wrote still mounts
# cleanly after a reboot and that the base config is still converged.
#   qemu.sh drive <vda.qcow2> guest-subvol-day2-reboot.sh SUBVOL-REBOOT-DONE
cd /root/repo || { echo "SUBVOL-REBOOT-DONE rc=91"; poweroff -f; }

D="python -m dasik --no-log"   # repo is a read-only 9p mount: no run log in the cwd
FAIL=0
step() { echo "SUBVOL-$1=$2"; [ "$2" -eq 0 ] || FAIL=1; }

findmnt --verify > /tmp/verify.log 2>&1; RC=$?; cat /tmp/verify.log
ok=$RC
# The state the first script ended on: no @data anywhere, its data in @.
findmnt /srv/data > /dev/null 2>&1 && ok=1
[ -f /srv/data2/big.bin ] || ok=1
step REBOOT-FSTAB $ok

$D plan config/vm-subvol-day2.json --target / > /tmp/plan.log 2>&1; PRC=$?; cat /tmp/plan.log
# The plan must have RUN (rc 0, no traceback, Plan.render output) and be silent
# in this domain; a crash or an empty log is a failure, not silence.
ok=1
if [ "$PRC" -eq 0 ] && [ -s /tmp/plan.log ] && ! grep -q Traceback /tmp/plan.log \
   && grep -Eq '^No changes|^  [-+~!] \[[a-z_0-9]+\]' /tmp/plan.log \
   && ! grep -q '\[btrfs_subvolumes\]' /tmp/plan.log; then ok=0; fi
step REBOOT $ok

echo "SUBVOL-REBOOT-DONE rc=$FAIL"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
