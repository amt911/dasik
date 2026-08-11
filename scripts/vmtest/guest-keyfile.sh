#!/bin/bash
# Pendrive LUKS unlock check, run INSIDE the booted (already-installed, encrypted)
# guest — issue #173 block B.
#
# THE PROOF IS THAT THIS SCRIPT RUNS AT ALL. `qemu.sh drive` boots the image with
# the key device attached and WITHOUT DASIK_VM_LUKS_PASSWORD, so the driver never
# types a passphrase. Reaching a root shell therefore means the initramfs opened
# the encrypted root from the keyfile on /dev/vdb, unattended. Everything below
# is the corroborating evidence.
#
# Emits KEYFILE-* markers; ends with KEYFILE-DONE rc=<failures>.
set -x
cd /root/repo || { echo "KEYFILE-DONE rc=91"; poweroff -f; }
echo "KEYFILE: BEGIN (booted with NO passphrase typed)"

rc=0
ok()  { echo "KEYFILE-OK: $*"; }
bad() { echo "KEYFILE-BAD: $*"; rc=$((rc + 1)); }

KEYDEV_UUID="1234-ABCD"
KEYFILE="/keyfile-dasik"
MNT=/run/keydev-check

echo "KEYFILE-INFO: cmdline=$(cat /proc/cmdline)"

# --- boundary evidence (printed before the assertions, so a FAILED unlock still
# --- says which layer dropped the key) ---------------------------------------
echo "KEYFILE-DIAG: loader entries ->"
for e in /boot/loader/entries/*.conf; do echo "  $e: $(tr '\n' '|' < "$e")"; done
echo "KEYFILE-DIAG: dracut conf ->"; cat /etc/dracut.conf.d/dasik.conf
echo "KEYFILE-DIAG: initramfs vfat module ->"
img="$(ls -1 /boot/initramfs-*.img 2>/dev/null | grep -v fallback | head -1)"
echo "  image=$img"
lsinitrd "$img" 2>/dev/null | grep -iE 'vfat|nls_|fat\.ko' | head -10 || echo "  (lsinitrd unavailable)"
echo "KEYFILE-DIAG: initramfs cryptsetup generator ->"
lsinitrd "$img" 2>/dev/null | grep -iE 'systemd-cryptsetup' | head -5
echo "KEYFILE-DIAG: cryptsetup journal from THIS boot ->"
journalctl -b --no-pager 2>/dev/null | grep -iE 'cryptsetup|keydev|keyfile|luks' | head -30

# 1. The root really is the LUKS mapping (not some unencrypted fallback).
src="$(findmnt -no SOURCE /)"
echo "KEYFILE-INFO: root source=$src"
case "$src" in
    /dev/mapper/cryptroot) ok "/ is the LUKS mapping /dev/mapper/cryptroot" ;;
    *) bad "/ is '$src', not /dev/mapper/cryptroot" ;;
esac

# 2. The kernel parameters dasik derives for a pendrive unlock.
cmdline="$(cat /proc/cmdline)"
case "$cmdline" in
    *"rd.luks.key="*":UUID=$KEYDEV_UUID"*) ok "rd.luks.key names the key device by UUID" ;;
    *) bad "rd.luks.key=<uuid>=$KEYFILE:UUID=$KEYDEV_UUID missing from the cmdline" ;;
esac
case "$cmdline" in
    *keyfile-timeout=10s*) ok "keyfile-timeout=10s present (falls back to the prompt)" ;;
    *) bad "no keyfile-timeout — an absent pendrive would hang the boot forever" ;;
esac
case "$cmdline" in
    *splash*) ok "splash present (plymouth block derives it)" ;;
    *) bad "no splash on the cmdline" ;;
esac

# 3. The key device is there and carries the keyfile dasik created.
if [ -e "/dev/disk/by-uuid/$KEYDEV_UUID" ]; then
    ok "key device /dev/disk/by-uuid/$KEYDEV_UUID present"
    mkdir -p "$MNT"
    if mount -o ro,umask=0077 "/dev/disk/by-uuid/$KEYDEV_UUID" "$MNT"; then
        if [ -f "$MNT$KEYFILE" ]; then
            ok "keyfile $KEYFILE exists on the key device ($(stat -c%s "$MNT$KEYFILE") bytes)"
            # 4. THE enrollment check: that exact file opens the volume.
            backing="$(cryptsetup status cryptroot | sed -n 's/.*device:[[:space:]]*//p' | head -1)"
            echo "KEYFILE-INFO: backing device=$backing"
            if cryptsetup open --test-passphrase --key-file "$MNT$KEYFILE" "$backing"; then
                ok "the keyfile is enrolled as a LUKS keyslot on $backing"
            else
                bad "the keyfile does NOT open $backing"
            fi
            # 5. The declared passphrase must still work — never lock the user out.
            if echo -n "keyfilepass" | cryptsetup open --test-passphrase --key-file - "$backing"; then
                ok "the passphrase still opens the volume (keyfile is an EXTRA slot)"
            else
                bad "the passphrase no longer opens the volume"
            fi
            slots="$(cryptsetup luksDump "$backing" | grep -cE '^[[:space:]]+[0-9]+: luks2')"
            echo "KEYFILE-INFO: keyslots=$slots"
            [ "${slots:-0}" -ge 2 ] && ok "two keyslots (passphrase + keyfile)" \
                                    || bad "expected >=2 keyslots, got $slots"
        else
            bad "no $KEYFILE on the key device"
        fi
        umount "$MNT"
    else
        bad "could not mount the key device"
    fi
else
    bad "key device /dev/disk/by-uuid/$KEYDEV_UUID is not attached"
fi

# 6. The initramfs inputs: the key device's filesystem module must be forced in,
#    or the image cannot read the pendrive at all.
if grep -q 'filesystems+=" vfat "' /etc/dracut.conf.d/dasik.conf; then
    ok "dracut carries the key device's vfat module"
else
    bad "dracut.conf.d/dasik.conf does not force the vfat module"
fi
if grep -q 'plymouth' /etc/dracut.conf.d/dasik.conf; then
    ok "dracut carries the plymouth module"
else
    bad "dracut.conf.d/dasik.conf does not force the plymouth module"
fi
pacman -Qq plymouth >/dev/null 2>&1 && ok "plymouth installed" || bad "plymouth not installed"

# 7. Day-2 idempotency on the LIVE host: a converged machine plans nothing for
#    the keyfile. This is the check that only exists because the enrollment is a
#    real probe (`cryptsetup --test-passphrase`) rather than a marker file.
echo "KEYFILE: plan against the live host (expect no [luks_keyfile] change)"
python -m dasik plan config/vm-luks-keyfile.json --target / > /root/plan.txt 2>&1
echo "KEYFILE-INFO: plan rc=$?"
if grep -q '\[luks_keyfile\]' /root/plan.txt; then
    bad "the converged machine still plans a keyfile change:"
    grep '\[luks_keyfile\]' /root/plan.txt
else
    ok "no [luks_keyfile] change planned (idempotent)"
fi

# 8. …and sync captures the unlock back into the partition.
cp config/vm-luks-keyfile.json /root/synced.json
python -m dasik sync /root/synced.json --target / >/dev/null 2>&1
python - <<PY
import json, sys
d = json.load(open("/root/synced.json"))
parts = [p for disk in d.get("disks", {}).get("disks", []) for p in disk["partitions"]]
enc = [p for p in parts if p.get("encrypt")]
fails = 0
def check(name, cond):
    global fails
    print(f"KEYFILE-{'OK' if cond else 'BAD'}: {name}")
    fails += 0 if cond else 1
check("sync captured an encrypted partition", bool(enc))
if enc:
    p = enc[0]
    print(f"KEYFILE-INFO: synced unlock_keyfile={p.get('unlock_keyfile')!r} "
          f"unlock_keydev={p.get('unlock_keydev')!r} "
          f"unlock_keydev_fs={p.get('unlock_keydev_fs')!r}")
    check("unlock_keyfile captured", p.get("unlock_keyfile") == "$KEYFILE")
    check("unlock_keydev captured", str(p.get("unlock_keydev", "")).endswith("$KEYDEV_UUID"))
    check("unlock_keydev_fs captured", p.get("unlock_keydev_fs") == "vfat")
check("plymouth block captured", d.get("plymouth") is not None)
sys.exit(fails)
PY
sync_rc=$?
rc=$((rc + sync_rc))

# 9. The synced config must re-plan to nothing (round-trip no-op).
if python -m dasik plan /root/synced.json --target / | grep -qE '\[luks_keyfile\]|\[plymouth\]'; then
    bad "the synced config still plans a keyfile/plymouth change"
else
    ok "synced config plans no keyfile/plymouth change (round-trip no-op)"
fi

echo "KEYFILE-DONE rc=$rc"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
