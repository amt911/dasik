#!/bin/bash
# A /etc/dracut.conf.d drop-in dasik did NOT write must still rebuild the image.
#
#   qemu.sh drive <image installed from config/vm-confd-base.json> \
#                 guest-dracut-conf-d.sh CONFD-DONE
#
# dracut reads every *.conf in that directory. dasik counted only its own
# dasik.conf (plus plymouthd.conf when a splash is declared) as an input to the
# image, so a drop-in added through `etc_tree` was written by `apply`, reported
# CONVERGED by the very next `plan`, and never reached the initramfs — until an
# unrelated kernel upgrade happened to run dracut, days or weeks later. The
# damage is not the delay, it is that you cannot tell "applied" from "ignored".
#
# Four phases against the LIVE host, which is the only place the whole chain
# (DropFilesAction -> InitramfsAction -> dracut) actually runs:
#
#   A  baseline — the config the machine was installed from re-plans to nothing,
#      and the fingerprint module is NOT in the image
#   B  apply vm-confd-dropin.json: same machine, one extra file. The image must
#      be rebuilt and must now carry xfs.ko
#   C  plan again -> silent (it converges instead of planning forever)
#   D  apply the BASE config again, which REMOVES the drop-in. Removal is the
#      half a file list cannot see: every file left behind is older than the
#      image, so only the directory's own mtime betrays it. The image must be
#      rebuilt again and xfs.ko must be gone.
#
# Ends with a single CONFD-DONE rc=<n> line the host driver greps.
set -u

rc=0
fail() { echo "BAD: $*"; rc=1; }
ok()   { echo "ok: $*"; }

cd /root/repo || { echo "CONFD-DONE rc=91"; poweroff -f; }

BASE=config/vm-confd-base.json
DROPIN=config/vm-confd-dropin.json
DROPIN_FILE=/etc/dracut.conf.d/50-vmtest-fingerprint.conf

img()     { ls -1 /boot/initramfs-*.img | head -1; }
has_xfs() { lsinitrd "$(img)" 2>/dev/null | grep -Eqi '(^|[ /])xfs\.ko'; }
stamp()   { stat -c %Y "$(img)"; }

echo "CONFD-A: baseline"
ls -1 /etc/dracut.conf.d/
python -m dasik plan "$BASE" --target / --no-log > /tmp/p0.txt 2>&1
grep -q 'No changes' /tmp/p0.txt && ok "the installed config re-plans to nothing" \
    || { fail "baseline plan is not silent"; tail -20 /tmp/p0.txt; }
if has_xfs; then
    fail "xfs.ko is already in the image — the fingerprint proves nothing"
else
    ok "xfs.ko not in the image yet"
fi
before="$(stamp)"

echo "CONFD-B: apply the config that adds the foreign drop-in"
python -m dasik plan "$DROPIN" --target / --no-log 2>&1 | grep -E '^\s+[+~-]|No changes' | tail -5
python -m dasik apply "$DROPIN" --target / --yes --no-log > /tmp/a1.txt 2>&1
echo "apply rc=$?"
grep -E '^\s+[+~-]' /tmp/a1.txt | tail -6
[ -f "$DROPIN_FILE" ] && ok "the drop-in is on disk" || fail "the drop-in was not written"
after="$(stamp)"
if [ "$after" != "$before" ]; then
    ok "the image was rebuilt ($before -> $after)"
else
    fail "the image was NOT rebuilt — apply reported success and changed nothing"
fi
if has_xfs; then
    ok "xfs.ko reached the image"
else
    fail "the drop-in never reached the initramfs"
fi

echo "CONFD-C: it converges instead of planning forever"
python -m dasik plan "$DROPIN" --target / --no-log > /tmp/p1.txt 2>&1
grep -q 'No changes' /tmp/p1.txt && ok "plan silent after the rebuild" \
    || { fail "plan keeps planning the same change"; tail -20 /tmp/p1.txt; }

echo "CONFD-D: removal — apply the base config again"
mid="$(stamp)"
python -m dasik apply "$BASE" --target / --yes --no-log > /tmp/a2.txt 2>&1
echo "apply rc=$?"
grep -E '^\s+[+~-]' /tmp/a2.txt | tail -6
[ -f "$DROPIN_FILE" ] && fail "the drop-in is still on disk" || ok "the drop-in was removed"
if [ "$(stamp)" != "$mid" ]; then
    ok "the image was rebuilt after the removal"
else
    fail "removal did NOT rebuild the image — only the directory mtime can catch this"
fi
if has_xfs; then
    fail "xfs.ko survives in an image built from a file that no longer exists"
else
    ok "xfs.ko is gone from the image"
fi
python -m dasik plan "$BASE" --target / --no-log > /tmp/p2.txt 2>&1
grep -q 'No changes' /tmp/p2.txt && ok "plan silent again" \
    || { fail "plan not silent after the removal"; tail -20 /tmp/p2.txt; }

echo "CONFD-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
