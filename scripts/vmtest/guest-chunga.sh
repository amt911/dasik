#!/bin/bash
# Full-stack 'chunga' day-2 check, run INSIDE the booted (installed, LUKS+btrfs+
# snapper+kvm+firewall+wireguard+cups+bluetooth+AUR+user) guest. Exercises the
# whole management surface against the LIVE host (target /):
#   A) re-apply the ENTIRE chunga config     -> expect "No changes" (day-2 no-op)
#   B) apply a modified config (+1 file)      -> records a new generation
#   C) rollback                               -> re-converges, the owned file is gone
#   D) sync                                   -> captures reality (encrypted btrfs)
# Emits CHUNGA-* markers; ends CHUNGA-DONE rc=<fails>. Prints the A plan diff if it
# is NOT a no-op so a day-2 idempotency regression is diagnosable.
set -x
cd /root/repo || { echo "CHUNGA-DONE rc=91"; poweroff -f; }
D="python -m dasik"
CFG=config/vm-chunga-full.json
MARKER=/etc/dasik-chunga-marker.conf
fails=0
check() { if [ "$2" -eq 0 ]; then echo "CHUNGA-OK: $1"; else echo "CHUNGA-BAD: $1"; fails=$((fails + 1)); fi; }

echo "CHUNGA: BEGIN (target / = the live full-stack host)"

echo "CHUNGA-A: re-apply the ENTIRE stack (expect No changes)"
$D apply "$CFG" --target / --yes > /tmp/chunga-a.out 2>&1
cat /tmp/chunga-a.out
grep -q "No changes" /tmp/chunga-a.out; check "day-2 re-apply of the full stack is a no-op" $?
grep -q "No changes" /tmp/chunga-a.out || { echo "CHUNGA-A-DIFF:"; grep -E '^\s*[+~-] \[' /tmp/chunga-a.out; }

echo "CHUNGA-B: apply a modified config (+1 owned file) -> new generation"
python - <<'PY'
import json
c = json.load(open("config/vm-chunga-full.json"))
c.setdefault("files", []).append({"path": "/etc/dasik-chunga-marker.conf", "content": "chunga day-2 marker\n"})
json.dump(c, open("/root/chunga-mod.json", "w"), indent=2)
PY
$D apply /root/chunga-mod.json --target / --yes
[ -f "$MARKER" ]; check "modified apply created the owned marker" $?
echo "CHUNGA-B-GENS:"; $D generations

echo "CHUNGA-C: rollback -> re-converge, marker removed"
$D rollback --target / --yes
[ ! -f "$MARKER" ]; check "rollback removed the marker (real re-convergence)" $?
echo "CHUNGA-C-GENS:"; $D generations

echo "CHUNGA-D: sync captures reality (encrypted btrfs layout)"
cp "$CFG" /root/chunga-synced.json
$D sync /root/chunga-synced.json --target /
python - <<'PY'
import json, sys
d = json.load(open("/root/chunga-synced.json"))
parts = [p for disk in d.get("disks", {}).get("disks", []) for p in disk["partitions"]]
enc = [p for p in parts if p.get("encrypt")]
fails = 0
def check(name, ok):
    global fails; print(f"CHUNGA-{'OK' if ok else 'BAD'}: {name}"); fails += 0 if ok else 1
check("sync captured the disks section", bool(parts))
check("all partitions format:false after sync", all(p.get("format") is False for p in parts))
check("encrypted btrfs root captured with luks_uuid", bool(enc) and bool(enc[0].get("luks_uuid")))
sys.exit(fails)
PY
check "sync captured the encrypted layout" $?

echo "CHUNGA-DONE rc=$fails"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
