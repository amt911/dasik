#!/bin/bash
# dracut: the uhid driver, the generic fallback image and orphan pruning, driven
# INSIDE the booted guest against the LIVE host (--target /).
#
#   qemu.sh install-driven config/vm-dracut-fallback.json
#   DASIK_VM_LUKS_PASSWORD=fallbackpass qemu.sh drive <vda.qcow2> \
#       guest-dracut-fallback.sh FB-DONE
#   DASIK_VM_LUKS_PASSWORD=fallbackpass qemu.sh drive <vda.qcow2> \
#       guest-dracut-fallback-boot.sh FBBOOT-DONE
#
# What only a guest can prove: that dracut really puts uhid.ko in the image,
# that `--no-hostonly` really builds a second image the rescue entry can load,
# that pacman really runs dasik's hook after dracut's own and rebuilds it, and —
# in the second script — that the generic image really opens the LUKS root.
#
# Phases: A install result, B pacman hook, C stale fallback, D rescue entry
# retarget, E orphans, F uhid upgrade, G remove hook, H sync->check->plan,
# I generations/rollback, J block removed, K restore + one-shot fallback boot.
#
# CONVENTION: every FB-<step>-RC=0 line is a PASS; FB-DONE rc=N counts failures.
set -x
cd /root/repo || { echo "FB-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
C=config/vm-dracut-fallback.json
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
MAIN=/boot/initramfs-linux.img
FB=/boot/initramfs-linux-fallback.img
ENTRY=/boot/loader/entries/arch-fallback.conf
OLD=1577836800                # 2020-01-01: older than anything this run builds

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }
absent() { ! grep -q -- "$2" "$1"; }
present() { grep -q -- "$2" "$1"; }
quiet_plan() {                 # plan must say "No changes"
    $D plan "$1" --target / $L > "$2" 2>&1
    grep -q 'No changes' "$2" || { tail -20 "$2"; return 1; }
}
has_mod() { lsinitrd "$1" 2>/dev/null | grep -Eq "/$2\.ko"; }
mtime() { stat -c %Y "$1"; }

echo "FB: BEGIN (target / = the live booted host)"
df -h /boot

echo "FB-A: what the install left behind"
has_mod "$MAIN" uhid; rc FB-A-UHID-MAIN
test -f "$FB"; rc FB-A-FALLBACK-EXISTS
lsinitrd "$FB" > /tmp/fb.lsinitrd 2>&1
present /tmp/fb.lsinitrd 'no-hostonly'; rc FB-A-FALLBACK-GENERIC
grep -m1 -i 'arguments' /tmp/fb.lsinitrd
has_mod "$FB" uhid; rc FB-A-UHID-FALLBACK
has_mod "$FB" systemd-cryptsetup || present /tmp/fb.lsinitrd 'systemd-cryptsetup'
rc FB-A-FALLBACK-CRYPT
present "$ENTRY" '^initrd /initramfs-linux-fallback.img$'; rc FB-A-ENTRY
present "$ENTRY" 'rd.luks.name='; rc FB-A-ENTRY-OPTIONS
present /etc/pacman.d/hooks/91-dasik-dracut-fallback.hook 'dasik-dracut-fallback'
rc FB-A-HOOK
present /etc/pacman.d/hooks/60-dasik-dracut-fallback-remove.hook 'dasik-dracut-fallback'
rc FB-A-REMOVE-HOOK
ls -la /boot
$D check "$C" $L; rc FB-A-CHECK
quiet_plan "$C" /tmp/pA.txt; rc FB-A-PLAN-QUIET

echo "FB-B: a kernel reinstall rebuilds the fallback through dasik's hook"
touch -d @$OLD "$FB"
pacman -S --noconfirm linux > /tmp/pacman.txt 2>&1; rc FB-B-PACMAN
grep -iE 'fallback|dracut' /tmp/pacman.txt
present /tmp/pacman.txt 'Updating fallback initramfs with dracut (dasik)'; rc FB-B-HOOK-RAN
[ "$(mtime "$FB")" -gt "$OLD" ]; rc FB-B-REBUILT
[ "$(mtime "$FB")" -ge "$(mtime /boot/vmlinuz-linux)" ]; rc FB-B-NEWER-THAN-KERNEL
quiet_plan "$C" /tmp/pB.txt; rc FB-B-PLAN-QUIET

echo "FB-C: a fallback older than its kernel is planned and rebuilt"
touch -d @$OLD "$FB"
$D plan "$C" --target / $L > /tmp/pC.txt 2>&1
present /tmp/pC.txt '\[initramfs\]'; rc FB-C-PLANNED
$D apply "$C" --target / --yes $L > /tmp/aC.txt 2>&1; rc FB-C-APPLY
[ "$(mtime "$FB")" -gt "$OLD" ]; rc FB-C-REBUILT
quiet_plan "$C" /tmp/pC2.txt; rc FB-C-PLAN-QUIET

echo "FB-D: a rescue entry on the main image is retargeted, options untouched"
opts_before="$(grep '^options' "$ENTRY")"
sed -i 's#^initrd /initramfs-linux-fallback.img$#initrd /initramfs-linux.img#' "$ENTRY"
$D plan "$C" --target / $L > /tmp/pD.txt 2>&1
present /tmp/pD.txt 'fallback-entry'; rc FB-D-PLANNED
$D apply "$C" --target / --yes $L > /tmp/aD.txt 2>&1; rc FB-D-APPLY
present "$ENTRY" '^initrd /initramfs-linux-fallback.img$'; rc FB-D-RETARGETED
[ "$(grep '^options' "$ENTRY")" = "$opts_before" ]; rc FB-D-OPTIONS-KEPT
quiet_plan "$C" /tmp/pD2.txt; rc FB-D-PLAN-QUIET

echo "FB-E: kver-named orphans are pruned; referenced and pkgbase-named images are kept"
cp "$MAIN" "/boot/initramfs-$(uname -r).img"          # what --regenerate-all writes
cp "$MAIN" /boot/initramfs-6.0.0-gone.img             # a removed kernel's
cp "$MAIN" /boot/initramfs-5.0.0-kept.img
cp "$MAIN" /boot/initramfs-linux-known-good.img
printf 'title custom\nlinux /vmlinuz-linux\ninitrd /initramfs-5.0.0-kept.img\noptions rw\n' \
    > /boot/loader/entries/zz-custom.conf
$D plan "$C" --target / $L > /tmp/pE.txt 2>&1
present /tmp/pE.txt "initramfs-$(uname -r).img"; rc FB-E-PLANNED-KVER
present /tmp/pE.txt 'initramfs-6.0.0-gone.img'; rc FB-E-PLANNED-GONE
absent /tmp/pE.txt 'initramfs-5.0.0-kept.img'; rc FB-E-REFERENCED-NOT-PLANNED
absent /tmp/pE.txt 'known-good'; rc FB-E-PKGBASE-NOT-PLANNED
$D apply "$C" --target / --yes $L > /tmp/aE.txt 2>&1; rc FB-E-APPLY
test ! -e "/boot/initramfs-$(uname -r).img"; rc FB-E-KVER-GONE
test ! -e /boot/initramfs-6.0.0-gone.img; rc FB-E-GONE-GONE
test -f /boot/initramfs-5.0.0-kept.img && test -f /boot/initramfs-linux-known-good.img; rc FB-E-KEPT
test -f "$MAIN" && test -f "$FB"; rc FB-E-OWN-IMAGES-KEPT
quiet_plan "$C" /tmp/pE2.txt; rc FB-E-PLAN-QUIET
rm -f /boot/initramfs-5.0.0-kept.img /boot/initramfs-linux-known-good.img /boot/loader/entries/zz-custom.conf

echo "FB-F: a conf an older dasik wrote (no uhid) is planned and rebuilt"
sed -i '/add_drivers/d' /etc/dracut.conf.d/dasik.conf
cat /etc/dracut.conf.d/dasik.conf
$D plan "$C" --target / $L > /tmp/pF.txt 2>&1
present /tmp/pF.txt '\[initramfs\]'; rc FB-F-PLANNED
$D apply "$C" --target / --yes $L > /tmp/aF.txt 2>&1; rc FB-F-APPLY
present /etc/dracut.conf.d/dasik.conf 'add_drivers+=" uhid "'; rc FB-F-CONF
has_mod "$MAIN" uhid; rc FB-F-UHID-MAIN
quiet_plan "$C" /tmp/pF2.txt; rc FB-F-PLAN-QUIET

echo "FB-G: the remove hook's Exec deletes a removed kernel's fallback"
# Removing the only kernel is not an option; replay the hook's own Exec with the
# stdin pacman would give it (NeedsTargets), split exactly as alpm splits it.
mkdir -p /usr/lib/modules/0.0.0-fake && echo fakek > /usr/lib/modules/0.0.0-fake/pkgbase
touch /boot/initramfs-fakek-fallback.img
script="$(python - <<'PY'
import shlex
for line in open("/etc/pacman.d/hooks/60-dasik-dracut-fallback-remove.hook"):
    if line.startswith("Exec = "):
        print(shlex.split(line[len("Exec = "):])[2])
PY
)"
echo "usr/lib/modules/0.0.0-fake/pkgbase" | bash -c "$script"; rc FB-G-EXEC
test ! -e /boot/initramfs-fakek-fallback.img; rc FB-G-REMOVED
test -f "$FB"; rc FB-G-OTHERS-KEPT
rm -rf /usr/lib/modules/0.0.0-fake

echo "FB-H: sync -> check -> plan is silent"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc FB-H-SYNC
python - <<'PY'; rc FB-H-SYNC-BLOCKS
import json, sys
c = json.load(open("/tmp/captured.json"))
print(c.get("initramfs"), c.get("bluetooth"))
sys.exit(0 if c.get("initramfs") == "dracut"
         and (c.get("bluetooth") or {}).get("in_initramfs") else 1)
PY
$D check /tmp/captured.json $L; rc FB-H-CHECK
quiet_plan /tmp/captured.json /tmp/pH.txt; rc FB-H-PLAN-QUIET

echo "FB-I: generations record the domains, rollback re-plans to nothing"
$D generations --target / $L | tail -15; rc FB-I-GENERATIONS
present /var/lib/dasik/state.json 'initramfs_orphans'; rc FB-I-MANIFEST
$D rollback --target / --yes $L > /tmp/rb.txt 2>&1; rc FB-I-ROLLBACK
tail -5 /tmp/rb.txt
quiet_plan "$C" /tmp/pI.txt; rc FB-I-PLAN-QUIET

echo "FB-J: the bluetooth-in-initramfs block removed"
python - <<'PY'
import json
c = json.load(open("config/vm-dracut-fallback.json"))
c["bluetooth"].pop("in_initramfs")
json.dump(c, open("/tmp/nobt.json", "w"), indent=2)
PY
$D plan /tmp/nobt.json --target / $L > /tmp/pJ.txt 2>&1
present /tmp/pJ.txt '\[initramfs\]'; rc FB-J-PLANNED
$D apply /tmp/nobt.json --target / --yes $L > /tmp/aJ.txt 2>&1; rc FB-J-APPLY
absent /etc/dracut.conf.d/dasik.conf 'uhid'; rc FB-J-NO-UHID
test -f "$FB"; rc FB-J-FALLBACK-STAYS
quiet_plan /tmp/nobt.json /tmp/pJ2.txt; rc FB-J-PLAN-QUIET

echo "FB-K: restore the declared config, then boot the rescue entry once"
$D apply "$C" --target / --yes $L > /tmp/aK.txt 2>&1; rc FB-K-APPLY
has_mod "$MAIN" uhid && has_mod "$FB" uhid; rc FB-K-UHID
quiet_plan "$C" /tmp/pK.txt; rc FB-K-PLAN-QUIET
bootctl set-oneshot arch-fallback.conf; rc FB-K-ONESHOT
ls -la /boot

echo "FB-DONE rc=$FAILS"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
