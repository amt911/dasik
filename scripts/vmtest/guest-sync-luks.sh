#!/bin/bash
# Encrypted-sync check, run INSIDE the booted (already-installed, LUKS-encrypted)
# guest. Proves `dasik sync` captures the REAL disk/LUKS layout non-destructively
# against the live host (target /): the encrypted root partition comes back with
# format:false and the real LUKS header UUID, and the plaintext luks_password is
# dropped. Emits SYNCLUKS-* markers; ends with SYNCLUKS-DONE rc=<fails>.
set -x
cd /root/repo || { echo "SYNCLUKS-DONE rc=91"; poweroff -f; }
echo "SYNCLUKS: BEGIN (target / = the live encrypted host)"

cp config/vm-day2-luks.json /root/synced.json
python -m dasik sync /root/synced.json --target /
echo "SYNCLUKS-SYNC-RC=$?"

python - <<'PY'
import json, sys
d = json.load(open("/root/synced.json"))
parts = [p for disk in d.get("disks", {}).get("disks", []) for p in disk["partitions"]]
enc = [p for p in parts if p.get("encrypt")]
fails = 0
def check(name, ok):
    global fails
    print(f"SYNCLUKS-{'OK' if ok else 'BAD'}: {name}")
    fails += 0 if ok else 1
check("disks section captured", bool(parts))
check("all partitions format:false", all(p.get("format") is False for p in parts))
check("encrypted root present", bool(enc))
if enc:
    root = enc[0]
    check("real luks_uuid captured", bool(root.get("luks_uuid")))
    check("plaintext luks_password dropped", "luks_password" not in root)
    print(f"SYNCLUKS-INFO: luks_uuid={root.get('luks_uuid')}")
sys.exit(fails)
PY
rc=$?
echo "SYNCLUKS-ASSERT-RC=$rc"

# The captured config must PLAN to nothing on the disk domain (round-trip no-op).
# Use plan (read-only) rather than apply — this is the live host.
echo "SYNCLUKS: plan the synced config (expect no [disks] changes)"
if python -m dasik plan /root/synced.json --target / | grep -q "\[disks\]"; then
    echo "SYNCLUKS-BAD: synced config still plans a disk change"; rc=$((rc + 1))
else
    echo "SYNCLUKS-OK: synced config plans no disk change (round-trip no-op)"
fi

echo "SYNCLUKS-DONE rc=$rc"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
