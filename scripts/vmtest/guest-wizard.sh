#!/bin/bash
# The partition wizard, with real curses, inside the guest, against its real
# disks — and the config it writes taken all the way to `plan`.
#
# The interesting risk is the terminal: this runs on the installer's console,
# which is a serial line, and curses over one of those is exactly where a
# full-screen UI breaks. `script` gives the wizard a pty (so it is a tty, as it
# would be for a human) while letting this script feed it keystrokes, and $TERM
# is whatever the console really set.
#
# Ends with WIZ-DONE, then powers off.
set -x
cd /root/repo || { echo "WIZ-DONE rc=91"; poweroff -f; }
rc=0

D="python -m dasik"
L="--no-log"
export PYTHONPATH=/root/repo
echo "WIZ-TERM: TERM=$TERM"
tput longname 2>/dev/null || echo "(no terminfo longname)"

echo "WIZ-A: the wizard sees the guest's real disks"
lsblk -J -b -o NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,MOUNTPOINT,PTTYPE | head -30

# Keys, in the order the screens ask:
#   disk (enter)                     — /dev/vda, the only target now that the
#                                      4 KiB floppy QEMU invents is filtered out
#   layout: down, enter              — ESP + LUKS + btrfs
#   erase this disk? y               — the guest IS installed on /dev/vda
#   ESP size (enter = 512MiB)
#   LUKS mapper name (enter = cryptroot)
#   passphrase "vmpass" (enter)
#   hostname (enter = archlinux)
#   review (enter = write)
printf '\r\033[B\ry\r\rvmpass\r\r\r' > /root/keys

echo "WIZ-B: drive real curses through a pty"
script -qec "$D partition-wizard --output /root/wiz/main.json $L" /root/wiz.typescript \
    < /root/keys > /root/wiz.out 2>&1
echo "wizard exit=$?"
tail -20 /root/wiz.out

echo "WIZ-C: it wrote a config, and the passphrase is NOT in it"
if [ -f /root/wiz/main.json ]; then
    cat /root/wiz/main.json
else
    echo "WIZ-NO-CONFIG"; rc=1
fi
grep -q 'vmpass' /root/wiz/main.json 2>/dev/null && { echo "WIZ-SECRET-LEAKED"; rc=1; }
grep -q '\$include_line' /root/wiz/main.json 2>/dev/null || { echo "WIZ-NO-SECRET-REF"; rc=1; }
stat -c '%a %n' /root/wiz/secrets/luks-passphrase 2>/dev/null
[ "$(stat -c '%a' /root/wiz/secrets/luks-passphrase 2>/dev/null)" = "600" ] \
    || { echo "WIZ-SECRET-MODE BAD"; rc=1; }

echo "WIZ-D: what it wrote passes check"
$D check /root/wiz/main.json $L || { echo "WIZ-CHECK FAILED"; rc=1; }

echo "WIZ-E: and plan reads it — the composed layout is what shows up"
# --target /mnt so nothing here touches the running system; the point is that
# plan PARSES and reaches the disk domain, not that it converges.
mkdir -p /mnt
$D plan /root/wiz/main.json --target /mnt $L > /root/plan.txt 2>&1
tail -25 /root/plan.txt
grep -qE '\[disks\]|wipe_disk|ERASES' /root/plan.txt \
    || { echo "WIZ-PLAN-NO-DISKS"; rc=1; }

echo "WIZ-F: nothing was partitioned — the guest's own layout is untouched"
lsblk -no NAME,FSTYPE,MOUNTPOINT /dev/vda
findmnt -no SOURCE / || { echo "WIZ-ROOT-GONE"; rc=1; }

echo "WIZ-DONE rc=$rc"
sync
poweroff -f
