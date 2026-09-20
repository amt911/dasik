#!/bin/bash
# Isolates ONE question the full pass left open: when the shared $include is
# removed from the config, does `plan` REMOVE the two udev rules a previous
# generation applied — and does `sync` change that answer?
#
# The full pass (guest-udev-discord.sh) only ever asked it AFTER a sync, and got
# "no". So here the same drop is planned TWICE: before any sync, and after one.
# A difference between the two is a sync that dispossesses the domain; the same
# answer twice means the ownership question has nothing to do with sync.
# Ends with UDROP-DONE, then powers off.
set -x
cd /root/repo || { echo "UDROP-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-udev-discord/main.json
echo "UDROP: BEGIN"

rm -rf /root/cfg
cp -r config/vm-udev-discord /root/cfg

echo "UDROP-A: the machine has both rules, and the config is converged"
ls -l /etc/udev/rules.d/
$D plan "$C" --target / $L; echo "UDROP-PLAN-RC=$?"

echo "UDROP-B: what the manifest says it owns"
$D generations --target / $L
cat /var/lib/dasik/manifest.json 2>/dev/null | head -c 2000 || echo "UDROP-MANIFEST: not at /var/lib/dasik"
find / -xdev -name 'manifest*.json' -path '*dasik*' 2>/dev/null | head

echo "UDROP-C: drop the shared include BEFORE any sync — expect DELETE of both rules"
python /root/repo/scripts/vmtest/udev_discord_report.py drop
$D plan /root/cfg/dropped.json --target / $L; echo "UDROP-PRESYNC-RC=$?"

echo "UDROP-D: now sync, and ask the very same question again"
$D sync /root/cfg/main.json --target / $L; echo "UDROP-SYNC-RC=$?"
python /root/repo/scripts/vmtest/udev_discord_report.py drop
$D plan /root/cfg/dropped.json --target / $L; echo "UDROP-POSTSYNC-RC=$?"

echo "UDROP-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
