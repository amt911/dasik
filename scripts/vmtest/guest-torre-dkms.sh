#!/bin/bash
# Does omit_drivers reach the DKMS-built NVIDIA modules too?
#
#   qemu.sh drive <image> guest-torre-dkms.sh DKMS-DONE
#
# The GE63 rehearsal measured this for nvidia-open, whose modules ship under
# usr/lib/modules/<kver>/extramodules/. The torre runs nvidia-open-dkms, which
# BUILDS into updates/dkms/ — a different path. omit_drivers matches module
# NAMES, not paths, so it should hold; this proves it rather than assuming it.
#
# Three rebuilds, so the test cannot pass vacuously:
#   A  as installed (drop-in present)      -> expect NO nvidia modules
#   B  drop-in moved away, rebuilt         -> expect the modules BACK (proves
#                                             the DKMS path really is pulled in,
#                                             and that A was not just an image
#                                             that never had them)
#   C  drop-in restored, rebuilt           -> expect them gone again
set -u
rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

CONF=/etc/dracut.conf.d/10-no-nvidia-early-kms.conf
kver="$(ls -1 /usr/lib/modules | grep -- '-' | head -1)"
pkgbase="$(cat "/usr/lib/modules/$kver/pkgbase" 2>/dev/null || echo linux)"
img="/boot/initramfs-$pkgbase.img"

echo "DKMS-0: where the modules actually live"
find /usr/lib/modules/"$kver" -name 'nvidia*.ko*' | head -5
pacman -Q nvidia-open-dkms nvidia-utils dkms 2>&1 | head -3
dkms status 2>&1 | head -3

count_mods() { lsinitrd "$1" 2>/dev/null | grep -Ec '(^|[ /])nvidia[^ /]*\.ko' || true; }
size_of()    { stat -c %s "$1" 2>/dev/null || echo 0; }

echo "DKMS-A: as installed, with the drop-in"
[ -f "$CONF" ] && ok "drop-in present" || fail "drop-in missing"
a_mods="$(count_mods "$img")"; a_size="$(size_of "$img")"
echo "modules=$a_mods size=$a_size"
[ "$a_mods" -eq 0 ] && ok "no nvidia modules in the installed image" \
                    || { fail "$a_mods nvidia modules in the installed image"; \
                         lsinitrd "$img" | grep -Ei 'nvidia[^ /]*\.ko'; }

echo "DKMS-B: WITHOUT the drop-in (control — the modules must come back)"
mv "$CONF" /root/conf.bak
dracut --force --fstab "$img" "$kver" >/dev/null 2>&1
echo "dracut rc=$?"
b_mods="$(count_mods "$img")"; b_size="$(size_of "$img")"
echo "modules=$b_mods size=$b_size"
if [ "$b_mods" -gt 0 ]; then
    ok "control confirms it: $b_mods nvidia module(s) get pulled in from updates/dkms"
    lsinitrd "$img" | grep -Ei 'nvidia[^ /]*\.ko|firmware/nvidia.*bin'
else
    fail "control FAILED — without the drop-in the modules are still absent, so this VM proves nothing about DKMS"
fi

echo "DKMS-C: drop-in restored"
mv /root/conf.bak "$CONF"
dracut --force --fstab "$img" "$kver" >/dev/null 2>&1
c_mods="$(count_mods "$img")"; c_size="$(size_of "$img")"
echo "modules=$c_mods size=$c_size"
[ "$c_mods" -eq 0 ] && ok "omit_drivers reaches the DKMS modules too" \
                    || fail "omit_drivers does NOT stop the DKMS modules"
echo "SIZES: with=$c_size without=$b_size saved=$((b_size - c_size)) bytes"

echo "DKMS-D: the boot chain survived the rebuild"
lsinitrd "$img" 2>/dev/null | grep -q 'systemd-cryptsetup' \
    && ok "systemd-cryptsetup still in the image" \
    || fail "systemd-cryptsetup LOST — this image would not open the LUKS root"

echo "DKMS-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
