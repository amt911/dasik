#!/bin/bash
# Second half of the GE63-K investigation: does omit_drivers actually keep the
# NVIDIA modules out, once dracut is made to run again?
#
#   qemu.sh drive <image> guest-ge63-kms2.sh KMS2-DONE
#
# The previous pass (guest-ge63-kms.sh) dropped
# /etc/dracut.conf.d/10-no-nvidia-early-kms.conf but the image never changed —
# `dasik apply` regenerates the initramfs from ITS OWN inputs, and an unrelated
# /etc file is not one of them. On a fresh install the ordering saves us
# (DropFilesAction runs long before InitramfsAction), so this rebuilds the image
# the way dasik does at install time and checks the result.
set -u
rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

kver="$(ls -1 /usr/lib/modules | head -1)"
pkgbase="$(cat "/usr/lib/modules/$kver/pkgbase" 2>/dev/null || echo linux)"
img="/boot/initramfs-$pkgbase.img"
echo "KMS2-A: rebuilding $img for $kver, exactly as dasik does"
before="$(stat -c %s "$img" 2>/dev/null || echo 0)"
dracut --force --fstab "$img" "$kver" 2>&1 | tail -5
echo "dracut rc=$?"
after="$(stat -c %s "$img" 2>/dev/null || echo 0)"
echo "size: $before -> $after bytes"

echo "KMS2-B: nvidia content of the rebuilt image"
lsinitrd "$img" | grep -i nvidia || echo "(no nvidia entries at all)"
kmods="$(lsinitrd "$img" 2>/dev/null | grep -Ei '(^|[ /])nvidia[^ /]*\.ko' || true)"
if [ -n "$kmods" ]; then
    fail "nvidia kernel modules survive omit_drivers"; echo "$kmods"
else
    ok "no nvidia kernel module — omit_drivers works"
fi
fw="$(lsinitrd "$img" 2>/dev/null | grep -i 'firmware/nvidia' || true)"
if [ -n "$fw" ]; then
    fail "the NVIDIA GSP firmware is still in the image (that is the ~113MB)"; echo "$fw"
else
    ok "no NVIDIA firmware in the image"
fi

echo "KMS2-C: the boot chain still has what it needs"
lsinitrd "$img" | grep -E 'resume|systemd-cryptsetup' | head -5
lsinitrd "$img" 2>/dev/null | grep -q 'resume' \
    && ok "resume module still present" || fail "resume module LOST by the rebuild"

echo "KMS2-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
