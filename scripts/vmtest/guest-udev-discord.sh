#!/bin/bash
# The two SHARED-file mechanisms dasik-personal-config now uses, checked inside
# the booted guest: udev rules delivered through an $include'd `files` fragment,
# and a Discord dotfile delivered through `home_tree`.
#
# What only a real machine answers: that both rules landed in /etc/udev/rules.d
# with the right bytes, that the dotfile is owned by the USER (a root-owned file
# in $HOME is the failure this domain exists to prevent), and that udev itself
# accepts the rules (`udevadm verify`). Then the six verbs as the two round
# trips, and the block-removed direction. Ends with UD-DONE, then powers off.
set -x
cd /root/repo || { echo "UD-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"                # the 9p repo is read-only; the run log defaults to $PWD
C=config/vm-udev-discord/main.json
echo "UD: BEGIN (target / = the live booted host)"

echo "UD-A: the two udev rules, as files"
for r in 1-qudelix.rules 50-dolphin-UB400.rules; do
    stat -c "UD-RULE: %n %U:%G %a" "/etc/udev/rules.d/$r" || echo "UD-RULE: MISSING $r"
done
grep -c 'uaccess' /etc/udev/rules.d/1-qudelix.rules
if diff -q config/vm-udev-discord/common/etc/udev/rules.d/1-qudelix.rules \
          /etc/udev/rules.d/1-qudelix.rules >/dev/null; then
    echo "UD-RULE-BYTES: qudelix ok"; else echo "UD-RULE-BYTES: qudelix DIFFERS"; fi
if diff -q config/vm-udev-discord/common/etc/udev/rules.d/50-dolphin-UB400.rules \
          /etc/udev/rules.d/50-dolphin-UB400.rules >/dev/null; then
    echo "UD-RULE-BYTES: ub400 ok"; else echo "UD-RULE-BYTES: ub400 DIFFERS"; fi

echo "UD-B: udev itself must accept them (a rule udev rejects is a file, not a rule)"
udevadm verify /etc/udev/rules.d/1-qudelix.rules; echo "UD-VERIFY-QUDELIX-RC=$?"
udevadm verify /etc/udev/rules.d/50-dolphin-UB400.rules; echo "UD-VERIFY-UB400-RC=$?"

echo "UD-C: the Discord dotfile, owned by the user and not by root"
stat -c "UD-DOTFILE: %U:%G %a" /home/test/.config/discord/settings.json \
    || echo "UD-DOTFILE: MISSING"
cat /home/test/.config/discord/settings.json
stat -c "UD-DIR: %n %U:%G" /home/test/.config /home/test/.config/discord

echo "UD-D: check"
$D check "$C" $L; echo "UD-CHECK-RC=$?"

echo "UD-E: plan -> apply -> plan (the second and third must be silent)"
$D plan "$C" --target / $L; echo "UD-PLAN-RC=$?"
$D apply "$C" --target / --yes $L; echo "UD-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "UD-REPLAN-RC=$?"

echo "UD-F: drift — delete one rule, root steals the dotfile; plan must see BOTH"
rm -f /etc/udev/rules.d/50-dolphin-UB400.rules
chown root:root /home/test/.config/discord/settings.json
echo "tampered" > /etc/udev/rules.d/1-qudelix.rules
$D plan "$C" --target / $L; echo "UD-DRIFT-RC=$?"
$D apply "$C" --target / --yes $L; echo "UD-FIX-RC=$?"
stat -c "UD-REPAIRED-DOTFILE: %U:%G" /home/test/.config/discord/settings.json
stat -c "UD-REPAIRED-RULE: %n" /etc/udev/rules.d/50-dolphin-UB400.rules
grep -c 'uaccess' /etc/udev/rules.d/1-qudelix.rules
$D plan "$C" --target / $L; echo "UD-AFTERFIX-RC=$?"

echo "UD-G: generations and rollback (before sync, which widens ownership)"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "UD-ROLLBACK-RC=$?"
$D plan "$C" --target / $L; echo "UD-POSTROLLBACK-RC=$?"

echo "UD-H: sync — both must come back as themselves, and the capture must re-plan to nothing"
# A SPLIT config cannot be copied as a single file: its $include and home_tree
# paths resolve next to it, so the copy has to be the whole directory (the repo
# at /root/repo is a read-only 9p mount, which is why it is copied at all).
rm -rf /root/cfg
cp -r config/vm-udev-discord /root/cfg
CAP=/root/cfg/main.json
$D sync "$CAP" --target / $L; echo "UD-SYNC-RC=$?"
python /root/repo/scripts/vmtest/udev_discord_report.py capture
$D check "$CAP" $L; echo "UD-CAPCHECK-RC=$?"
$D plan "$CAP" --target / $L; echo "UD-CAPPLAN-RC=$?"

echo "UD-I: the block removed — a domain a previous generation owns must be REMOVED, not ignored"
python /root/repo/scripts/vmtest/udev_discord_report.py drop
$D plan /root/cfg/dropped.json --target / $L; echo "UD-DROP-RC=$?"

echo "UD-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
