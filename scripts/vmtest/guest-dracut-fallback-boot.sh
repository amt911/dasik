#!/bin/bash
# Second boot of the vm-dracut-fallback image: guest-dracut-fallback.sh left a
# one-shot pointing at arch-fallback.conf, so THIS boot went through the generic
# (--no-hostonly) image. Reaching this shell at all proves it opened the LUKS
# root; the checks pin down that it was really the rescue entry that did it.
#
#   DASIK_VM_LUKS_PASSWORD=fallbackpass qemu.sh drive <vda.qcow2> \
#       guest-dracut-fallback-boot.sh FBBOOT-DONE
set -x
FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

var=/sys/firmware/efi/efivars/LoaderEntrySelected-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f
selected="$(tail -c +5 "$var" | tr -d '\0')"
echo "selected entry: $selected"
[ "$selected" = "arch-fallback.conf" ]; rc FBBOOT-ENTRY
findmnt -no SOURCE / | grep -q '/dev/mapper/cryptroot'; rc FBBOOT-CRYPTROOT
grep -q 'rd.luks.name=' /proc/cmdline; rc FBBOOT-CMDLINE
systemctl is-system-running --wait; systemctl --failed --no-legend
[ -z "$(systemctl --failed --no-legend)" ]; rc FBBOOT-NO-FAILED-UNITS

echo "FBBOOT-DONE rc=$FAILS"
sync
poweroff -f
