#!/bin/bash
# rootflags sync drift on a btrfs root (`sync` -> `plan` was not silent).
#
# Two bugs together made a re-synced btrfs-root config replan forever:
#
#   A. `_live_subvol_options`/`_btrfs_subvols` kept only `compress*` options
#      from findmnt, so a REAL declared option like `noatime` was silently
#      dropped from the captured config -- a reinstall from the capture lost
#      it.
#   B. Even once `noatime` survives, the kernel reports the compression LEVEL
#      (a bare `compress-force=zstd` comes back `compress-force=zstd:3`), so
#      the captured config's derived `rootflags=` never string-matched the
#      boot entry it was captured FROM -- `plan` announced a change nobody
#      made, forever.
#   C. A GENUINE `rootflags=` change (a different compression level) used to
#      be INSTALLed alongside the old token instead of replacing it: two
#      tokens with the same key on one entry, and the kernel takes the last.
#
# This drives `check`/`plan`/`sync`/`apply`/`generations`/`rollback` for real
# against the guest's own /dev/vda, installed from config/vm-rootflags-drift.json
# (btrfs `@` + `@home`, both declaring `compress-force=zstd,noatime`).
#
# CONVENTION: every RFD-<step>-RC=0 line is a PASS; rc() counts the failures
# so the final RFD-DONE rc=N is the verdict. Ends with RFD-DONE, then powers off.
set -x
export PYTHONPATH=/root/repo
cd /root/repo || { echo "RFD-DONE rc=91"; poweroff -f; }

if command -v dasik > /dev/null 2>&1; then D="dasik"; else D="python -m dasik"; fi
C=config/vm-rootflags-drift.json
L="--no-log"
ENTRY=/boot/loader/entries/arch.conf

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

# Exactly one `rootflags=` token on the entry -- the invariant root cause C
# breaks (a duplicate is a bug regardless of which value "wins").
one_rootflags() {
    [ "$(grep -o 'rootflags=[^ "]*' "$ENTRY" | wc -l)" -eq 1 ]
}

echo "RFD: BEGIN (target / = the freshly installed host)"
echo "RFD-DRIVER: $D  CONFIG: $C"

echo "RFD-A: the entry as installed"
grep options "$ENTRY"
one_rootflags; rc RFD-INSTALL-SINGLE-ROOTFLAGS

echo "RFD-B: plan with the install config is silent"
$D plan "$C" --target / $L > /tmp/plan-a.txt 2>&1; rc RFD-PLAN-A
cat /tmp/plan-a.txt
present /tmp/plan-a.txt 'No changes'; rc RFD-PLAN-A-SILENT

echo "RFD-C: sync -> captured.json"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc RFD-SYNC

echo "RFD-D: the capture validates"
$D check /tmp/captured.json $L; rc RFD-CAPTURE-CHECK

echo "RFD-E: the capture declares noatime on the root subvolume"
python - <<'PY'
import json, sys
c = json.load(open("/tmp/captured.json"))
# The ESP is partitions[0]; find the btrfs ROOT by its non-empty
# btrfs_subvolumes rather than assuming a position (model_dump() gives the
# ESP an empty `btrfs_subvolumes: []` too, so `p["btrfs_subvolumes"]` alone
# does not distinguish them -- a *non-empty* one does).
part = next(p for p in c["disks"]["disks"][0]["partitions"] if p.get("btrfs_subvolumes"))
root_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")
opts = part.get("mount_options", []) + root_sv.get("mount_options", [])
print("RFD-CAPTURED-ROOT-OPTS:", opts)
sys.exit(0 if "noatime" in opts else 1)
PY
rc RFD-CAPTURE-HAS-NOATIME

echo "RFD-F: plan of the capture is SILENT (root cause B: bare zstd == zstd:3)"
$D plan /tmp/captured.json --target / $L > /tmp/plan-captured.txt 2>&1; rc RFD-PLAN-CAPTURED
cat /tmp/plan-captured.txt
present /tmp/plan-captured.txt 'No changes'; rc RFD-PLAN-CAPTURED-SILENT

echo "RFD-G: apply --yes the capture -- no change written"
cp "$ENTRY" /tmp/entry-before-noop-apply
$D apply /tmp/captured.json --target / --yes $L > /tmp/apply-noop.txt 2>&1; rc RFD-APPLY-NOOP
cat /tmp/apply-noop.txt
diff /tmp/entry-before-noop-apply "$ENTRY"; rc RFD-APPLY-NOOP-ENTRY-UNCHANGED
one_rootflags; rc RFD-APPLY-NOOP-SINGLE-ROOTFLAGS

echo "RFD-H: a real replacement -- compress-force=zstd:1 on the root subvolume"
python - <<'PY'
import json
c = json.load(open("/tmp/captured.json"))
part = next(p for p in c["disks"]["disks"][0]["partitions"] if p.get("btrfs_subvolumes"))
for sv in part["btrfs_subvolumes"]:
    if sv["name"] != "@":
        continue
    opts = part.get("mount_options", []) + sv.get("mount_options", [])
    new_opts = [o for o in opts if not o.startswith("compress")] + ["compress-force=zstd:1"]
    # Keep whatever the capture put at partition level untouched; only the
    # subvolume's OWN list changes -- mirrors how a human edits the file.
    base = set(part.get("mount_options", []))
    sv["mount_options"] = [o for o in new_opts if o not in base]
json.dump(c, open("/tmp/zstd1.json", "w"), indent=2)
PY
rc RFD-BUILD-REPLACEMENT
$D check /tmp/zstd1.json $L; rc RFD-REPLACEMENT-CHECK

echo "RFD-I: plan shows the rootflags INSTALL"
$D plan /tmp/zstd1.json --target / $L > /tmp/plan-zstd1.txt 2>&1; rc RFD-PLAN-ZSTD1
cat /tmp/plan-zstd1.txt
present /tmp/plan-zstd1.txt 'rootflags='; rc RFD-PLAN-ZSTD1-HAS-ROOTFLAGS
present /tmp/plan-zstd1.txt 'zstd:1'; rc RFD-PLAN-ZSTD1-HAS-LEVEL

echo "RFD-J: apply --yes the replacement"
$D apply /tmp/zstd1.json --target / --yes $L > /tmp/apply-zstd1.txt 2>&1; rc RFD-APPLY-ZSTD1
cat /tmp/apply-zstd1.txt
grep options "$ENTRY"
one_rootflags; rc RFD-APPLY-ZSTD1-SINGLE-ROOTFLAGS
present "$ENTRY" 'rootflags=[^ ]*zstd:1'; rc RFD-APPLY-ZSTD1-CARRIES-LEVEL

echo "RFD-K: plan of the replacement is now silent (converged)"
$D plan /tmp/zstd1.json --target / $L > /tmp/plan-zstd1-2.txt 2>&1; rc RFD-PLAN-ZSTD1-AGAIN
cat /tmp/plan-zstd1-2.txt
present /tmp/plan-zstd1-2.txt 'No changes'; rc RFD-PLAN-ZSTD1-AGAIN-SILENT

echo "RFD-L: generations lists the new one"
$D generations --target / $L > /tmp/generations.txt 2>&1; rc RFD-GENERATIONS
cat /tmp/generations.txt

echo "RFD-M: rollback to the previous generation -- one rootflags=, the old value"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc RFD-ROLLBACK
cat /tmp/rollback.txt
grep options "$ENTRY"
one_rootflags; rc RFD-ROLLBACK-SINGLE-ROOTFLAGS
absent "$ENTRY" 'zstd:1'; rc RFD-ROLLBACK-LEVEL-GONE

echo "RFD-N: plan of the ORIGINAL install config is silent again"
$D plan "$C" --target / $L > /tmp/plan-after-rollback.txt 2>&1; rc RFD-PLAN-AFTER-ROLLBACK
cat /tmp/plan-after-rollback.txt
present /tmp/plan-after-rollback.txt 'No changes'; rc RFD-PLAN-AFTER-ROLLBACK-SILENT

echo "RFD-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
