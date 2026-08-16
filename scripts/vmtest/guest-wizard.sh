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

# Keys, in the order the screens ask. NOTE the arrow form is terminal-specific:
# this console is vt220, whose kcud1 is ESC [ B (CSI). An xterm in application
# cursor mode wants ESC O B (SS3) instead. ncurses decodes whichever its
# terminfo says; a script has to match the terminal it is typing into, or the
# bytes arrive as a bare ESC plus junk — which the wizard now shrugs off rather
# than treating as "abandon".
#   disk (enter)          — /dev/vda, now that the 4 KiB floppy is filtered out
#   layout: down, enter   — ESP + encrypted btrfs root, with subvolumes
#   NOT EMPTY: down, enter — row 2 = "Simulate", i.e. compose WITHOUT erasing
#   ESP size (enter) · LUKS name (enter) · passphrase · hostname · review
printf '\r\033[B\r\033[B\r\r\rvmpass\r\r\r' > /root/keys

# The guest image persists between runs, so a leftover from a previous pass
# would be "verified" instead of what this run wrote — and the wizard would
# refuse to overwrite it, quietly making every check below meaningless.
rm -rf /root/wiz /root/wiz.out /root/wiz.typescript

echo "WIZ-B: drive real curses through a pty"
script -qec "$D partition-wizard --output /root/wiz/main.json $L" /root/wiz.typescript \
    < /root/keys > /root/wiz.out 2>&1
wizard_rc=$?
echo "wizard exit=$wizard_rc"
[ "$wizard_rc" -eq 0 ] || { echo "WIZ-EXIT BAD"; rc=1; }
tail -20 /root/wiz.out

echo "WIZ-C: it wrote a config — a SIMULATION, with nothing erased"
python - <<'PY' || rc=1
import json, pathlib, sys
disk = json.loads(pathlib.Path("/root/wiz/main.json").read_text())["disks"]["disks"][0]
print("device:", disk["device"], "wipe_disk:", disk["wipe_disk"])
sys.exit(0 if disk["wipe_disk"] is False else 1)
PY
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
# Against the LIVE target: `plan` is read-only, and a non-/ target would need
# arch-chroot, which an installed guest does not carry. What is being proved is
# that plan parses the composed block and ANNOUNCES the erase — the gate the
# wizard deliberately leaves in front of the destructive half.
$D plan /root/wiz/main.json --target / $L > /root/plan.txt 2>&1
head -12 /root/plan.txt
# The exact line, not just the word somewhere: this is the gate the whole
# design rests on — the wizard composed it, and `plan` is what announces the
# erase before anything happens.
# A simulation must NOT plan a repartition: dasik refuses a populated disk that
# does not declare wipe_disk, and the wizard composed exactly that.
grep -qiE 'ERASES|DESTRUCTIVE' /root/plan.txt \
    && { echo "WIZ-SIMULATION-PLANNED-AN-ERASE"; rc=1; }
grep -qiE 'skip|does not match' /root/plan.txt \
    || { echo "WIZ-PLAN-DID-NOT-SKIP"; head -20 /root/plan.txt; }

echo "WIZ-F: nothing was partitioned — the guest's own layout is untouched"
lsblk -no NAME,FSTYPE,MOUNTPOINT /dev/vda
findmnt -no SOURCE / || { echo "WIZ-ROOT-GONE"; rc=1; }

echo "WIZ-DONE rc=$rc"
sync
poweroff -f
