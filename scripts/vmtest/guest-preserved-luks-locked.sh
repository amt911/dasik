#!/bin/bash
# The vm-preserved-luks machine booted when its kept disk CANNOT be opened
# silently: no FIDO2 key answers (none exists in a VM) and, after
# guest-preserved-luks-rekey.sh, the root's cached passphrase is not the kept
# disk's. `nofail,timeout=1s` on its crypttab line must make that cost
# nothing but the disk: no prompt left waiting on a console nobody watches
# (that is what locked the real key's PIN on torre-amd), the boot reaches this
# autologin shell, and Games is simply not mounted.
#
# Not a unit test: only a booted systemd shows whether a crypttab entry leaves
# an ask-password request pending. Ends with LOCKED-DONE rc=<n>.
set -x
rc=0
echo "LOCKED-A: the boot reached a usable state"
state=$(timeout 180 systemctl is-system-running --wait 2>/dev/null)
echo "LOCKED-STATE=$state"
case "$state" in running|degraded) : ;; *) echo "LOCKED-A FAILED: state $state"; rc=1 ;; esac

echo "LOCKED-B: nothing is waiting for a password or a PIN"
ls -la /run/systemd/ask-password/
pending=$(ls /run/systemd/ask-password/ 2>/dev/null | grep -c '^ask\.')
echo "LOCKED-PENDING=$pending"
[ "$pending" -eq 0 ] || { echo "LOCKED-B FAILED: an ask-password request is pending"; rc=1; }
journalctl -b -o short-monotonic --no-pager -u systemd-cryptsetup@games.service
# B is only evidence if the unlock really RAN and gave up: a unit nobody
# started would leave no request pending either.
unit=$(systemctl show -p ActiveState,Result --value systemd-cryptsetup@games.service | paste -sd,)
echo "LOCKED-UNIT=$unit"
[ "$unit" = "failed,exit-code" ] || \
    { echo "LOCKED-B FAILED: the games unlock did not run and give up (got $unit)"; rc=1; }

saw=$(cat /run/dm-saw-games 2>/dev/null)
echo "LOCKED-DM-SAW=$saw"
[ "$saw" = failed ] || { echo "LOCKED-B FAILED: the greeter started while games was '$saw'"; rc=1; }

echo "LOCKED-C: the kept disk stayed closed and unmounted"
cryptsetup status games >/dev/null 2>&1 && { echo "LOCKED-C FAILED: games is open"; rc=1; }
findmnt /home/tester/Games && { echo "LOCKED-C FAILED: Games is mounted"; rc=1; }

echo "LOCKED-D: the root came up exactly as always"
findmnt -no SOURCE / | grep -q '^/dev/mapper/cryptroot' || { echo "LOCKED-D FAILED: root"; rc=1; }

echo "LOCKED-DONE rc=$rc"
sync; sleep 2; poweroff -f
