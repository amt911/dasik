#!/bin/bash
# rootflags sync drift on a btrfs root (`sync` -> `plan` was not silent).
#
# Original three bugs (round 0):
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
# Review round 1 added (see scratchpad/review-rootflags-findings.md):
#
#   B1. plan() picked an arbitrary live rootflags= token via next() over a SET
#       when the entry carried two (round 0's pre-fix duplicate shape) --
#       PYTHONHASHSEED-dependent, so a genuinely divergent machine could read
#       as silently converged. Sections O-R below drive this for real: each
#       `dasik plan` invocation is a fresh Python process with ASLR/hash-seed
#       randomization on by default, so three consecutive real invocations are
#       themselves a hash-seed spread, not just the same process rerun.
#   B2. rw/ro were two unrelated keys, so an explicit "ro" could never
#       suppress the always-derived "rw" and the entry flip-flopped forever.
#       Sections S-W drive this for real.
#   S1/S2/S4 (capture correctness) and N2 (this script's own gaps) are
#       exercised by the tightened assertions throughout.
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
FALLBACK=/boot/loader/entries/arch-fallback.conf

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is

# Exactly one `rootflags=` token on the entry -- the invariant root cause C
# breaks (a duplicate is a bug regardless of which value "wins").
one_rootflags() {
    [ "$(grep -o 'rootflags=[^ "]*' "$ENTRY" | wc -l)" -eq 1 ]
}

# N2: the SAME invariant, but on EVERY entry dasik owns -- apply() writes
# every owned sd-boot entry (kernel_cmdline_action.py's `_our_sdboot_entries`),
# and bootloader_action.py claims arch.conf/arch-fallback.conf "stay in step".
# This is the only thing that actually MEASURES that claim.
one_rootflags_everywhere() {
    local f count bad=0
    for f in "$ENTRY" "$FALLBACK"; do
        [ -f "$f" ] || continue
        count=$(grep -o 'rootflags=[^ "]*' "$f" | wc -l)
        if [ "$count" -ne 1 ]; then
            echo "RFD-MULTI-ROOTFLAGS: $f has $count rootflags= token(s)" >&2
            bad=1
        fi
    done
    return "$bad"
}

echo "RFD: BEGIN (target / = the freshly installed host)"
echo "RFD-DRIVER: $D  CONFIG: $C"

echo "RFD-A: the entry as installed"
grep options "$ENTRY"
[ -f "$FALLBACK" ] && grep options "$FALLBACK"
one_rootflags_everywhere; rc RFD-INSTALL-SINGLE-ROOTFLAGS

# --------------------------------------------------------------------- #
# S2/S6 measurement: btrfs compression-level + atime-flag normalization,
# on a loopback image mounted with the REAL running kernel. Never trust the
# clamping hypothesis -- measure it. Each RFD-MEASURE-<label>=<value> line is
# the raw `findmnt -no OPTIONS` string (or MOUNT-FAILED if the spelling is
# rejected outright), so docs/FACTS.md can be updated from what actually
# happened here, not from a guess.
# --------------------------------------------------------------------- #
echo "RFD-MEASURE: btrfs compression-level + atime-flag normalization"
IMG=/root/measure.img
MNT=/root/measure-mnt
mkdir -p "$MNT"
truncate -s 256M "$IMG"; rc RFD-MEASURE-TRUNCATE
mkfs.btrfs -f "$IMG" > /root/mkfs-measure.log 2>&1; rc RFD-MEASURE-MKFS

measure_opt() {
    # $1 = mount -o value, $2 = label for the RFD-MEASURE-<label> line
    if mount -o "$1" "$IMG" "$MNT" 2> /root/mount-"$2".err; then
        echo "RFD-MEASURE-$2=$(findmnt -no OPTIONS "$MNT")"
        umount "$MNT"
    else
        echo "RFD-MEASURE-$2=MOUNT-FAILED: $(cat /root/mount-"$2".err)"
    fi
}

measure_opt "compress=zstd"            ZSTD-BARE
measure_opt "compress-force=zstd"      ZSTD-FORCE-BARE
measure_opt "compress-force=zstd:0"    ZSTD-FORCE-0
measure_opt "compress-force=zstd:1"    ZSTD-FORCE-1
measure_opt "compress-force=zstd:15"   ZSTD-FORCE-15
measure_opt "compress-force=zstd:16"   ZSTD-FORCE-16
measure_opt "compress=zlib"            ZLIB-BARE
measure_opt "compress=zlib:0"          ZLIB-0
measure_opt "compress=zlib:9"          ZLIB-9
measure_opt "compress=zlib:12"         ZLIB-12
measure_opt "compress=lzo"             LZO-BARE
measure_opt "compress"                 COMPRESS-BARE
measure_opt "compress-force"           COMPRESS-FORCE-BARE
measure_opt "compress=no"              COMPRESS-NO
measure_opt "strictatime"              STRICTATIME
measure_opt "noatime"                  NOATIME
measure_opt "nodiratime"               NODIRATIME
measure_opt "lazytime"                 LAZYTIME
measure_opt "relatime"                 RELATIME
rmdir "$MNT" 2>/dev/null || true

echo "RFD-B: plan with the install config is silent"
$D plan "$C" --target / $L > /tmp/plan-a.txt 2>&1; rc RFD-PLAN-A
cat /tmp/plan-a.txt
present /tmp/plan-a.txt 'No changes'; rc RFD-PLAN-A-SILENT

echo "RFD-GEN-BASELINE: generation count before any real change"
$D generations --target / $L > /tmp/gen-before.txt 2>&1; rc RFD-GENERATIONS-BASELINE
cat /tmp/gen-before.txt
GEN_BEFORE=$(grep -c '^Generation ' /tmp/gen-before.txt)

echo "RFD-C: sync -> captured.json"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc RFD-SYNC

echo "RFD-D: the capture validates"
$D check /tmp/captured.json $L; rc RFD-CAPTURE-CHECK

echo "RFD-E: the capture declares noatime on the root subvolume; N2: no kernel bookkeeping invented"
python - <<'PY'
import json, sys
c = json.load(open("/tmp/captured.json"))
# The ESP is partitions[0]; find the btrfs ROOT by its non-empty
# btrfs_subvolumes rather than assuming a position (model_dump() gives the
# ESP an empty `btrfs_subvolumes: []` too, so `p["btrfs_subvolumes"]` alone
# does not distinguish them -- a *non-empty* one does).
part = next(p for p in c["disks"]["disks"][0]["partitions"] if p.get("btrfs_subvolumes"))
root_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@")
home_sv = next(s for s in part["btrfs_subvolumes"] if s["name"] == "@home")
opts = set(part.get("mount_options", [])) | set(root_sv.get("mount_options", []))
print("RFD-CAPTURED-ROOT-OPTS:", sorted(opts))
print("RFD-CAPTURED-HOME-OPTS:", sorted(set(home_sv.get("mount_options", []))))
ok = "noatime" in opts
# N2: negative assertion -- none of the kernel's own bookkeeping/defaults were
# invented into the capture. relatime is checked as a token boundary (not a
# substring of nodiratime/strictatime).
never = ("ssd", "discard=async", "subvolid=", "subvol=")
ok = ok and not any(any(o.startswith(n) or o == n for n in never) for o in opts)
ok = ok and not any(o == "relatime" or o.startswith("space_cache") for o in opts)
sys.exit(0 if ok else 1)
PY
rc RFD-CAPTURE-HAS-NOATIME-AND-NO-BOOKKEEPING

echo "RFD-F: plan of the capture is SILENT (root cause B: bare zstd == zstd:3)"
$D plan /tmp/captured.json --target / $L > /tmp/plan-captured.txt 2>&1; rc RFD-PLAN-CAPTURED
cat /tmp/plan-captured.txt
present /tmp/plan-captured.txt 'No changes'; rc RFD-PLAN-CAPTURED-SILENT

echo "RFD-G: apply --yes the capture -- no change written"
cp "$ENTRY" /tmp/entry-before-noop-apply
$D apply /tmp/captured.json --target / --yes $L > /tmp/apply-noop.txt 2>&1; rc RFD-APPLY-NOOP
cat /tmp/apply-noop.txt
diff /tmp/entry-before-noop-apply "$ENTRY"; rc RFD-APPLY-NOOP-ENTRY-UNCHANGED
one_rootflags_everywhere; rc RFD-APPLY-NOOP-SINGLE-ROOTFLAGS

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
one_rootflags_everywhere; rc RFD-APPLY-ZSTD1-SINGLE-ROOTFLAGS
present "$ENTRY" 'rootflags=[^ ]*zstd:1'; rc RFD-APPLY-ZSTD1-CARRIES-LEVEL

echo "RFD-K: plan of the replacement is now silent (converged)"
$D plan /tmp/zstd1.json --target / $L > /tmp/plan-zstd1-2.txt 2>&1; rc RFD-PLAN-ZSTD1-AGAIN
cat /tmp/plan-zstd1-2.txt
present /tmp/plan-zstd1-2.txt 'No changes'; rc RFD-PLAN-ZSTD1-AGAIN-SILENT

echo "RFD-L: generations grew by EXACTLY ONE (N2 -- predictable, per the review)"
$D generations --target / $L > /tmp/generations.txt 2>&1; rc RFD-GENERATIONS
cat /tmp/generations.txt
GEN_AFTER=$(grep -c '^Generation ' /tmp/generations.txt)
echo "RFD-GEN-BEFORE=$GEN_BEFORE RFD-GEN-AFTER=$GEN_AFTER"
[ "$GEN_AFTER" -eq $((GEN_BEFORE + 1)) ]; rc RFD-GENERATIONS-GREW-BY-ONE

echo "RFD-M: rollback to the previous generation -- one rootflags=, the old value"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc RFD-ROLLBACK
cat /tmp/rollback.txt
grep options "$ENTRY"
one_rootflags_everywhere; rc RFD-ROLLBACK-SINGLE-ROOTFLAGS
absent "$ENTRY" 'zstd:1'; rc RFD-ROLLBACK-LEVEL-GONE

echo "RFD-N: plan of the ORIGINAL install config is silent again"
$D plan "$C" --target / $L > /tmp/plan-after-rollback.txt 2>&1; rc RFD-PLAN-AFTER-ROLLBACK
cat /tmp/plan-after-rollback.txt
present /tmp/plan-after-rollback.txt 'No changes'; rc RFD-PLAN-AFTER-ROLLBACK-SILENT

# --------------------------------------------------------------------- #
# B1 (blocker, review round 1): a live entry with TWO rootflags= tokens must
# never read as converged, deterministically. The entry right now (post
# rollback) carries exactly one bare token -- hand-write a second one, in the
# exact pre-fix duplicate shape root cause C used to produce.
# --------------------------------------------------------------------- #
echo "RFD-O: B1 -- hand-write a SECOND rootflags= into the entry (duplicate, root cause C shape)"
python - <<'PY'
import re
path = "/boot/loader/entries/arch.conf"
text = open(path).read()
m = re.search(r'rootflags=\S+', text)
assert m, text
orig = m.group(0)
dup = re.sub(r'compress-force=zstd(?!:)', 'compress-force=zstd:1', orig)
assert dup != orig, f"could not build a distinct duplicate from {orig!r}"
text = text.replace(orig, orig + " " + dup, 1)
open(path, "w").write(text)
print("RFD-B1-ENTRY-OPTIONS:", [l for l in text.splitlines() if l.startswith("options")])
PY
rc RFD-B1-BUILD-DUPLICATE
grep options "$ENTRY"

echo "RFD-P: B1 -- plan of the captured config INSTALLs on 3 consecutive (separate-process) runs, identically"
for i in 1 2 3; do
    $D plan /tmp/captured.json --target / $L > /tmp/plan-b1-$i.txt 2>&1; rc RFD-PLAN-B1-$i
    cat /tmp/plan-b1-$i.txt
    absent /tmp/plan-b1-$i.txt 'No changes'; rc RFD-PLAN-B1-$i-NOT-SILENT
    present /tmp/plan-b1-$i.txt 'rootflags='; rc RFD-PLAN-B1-$i-HAS-ROOTFLAGS
    grep -o 'rootflags=[^ "]*' /tmp/plan-b1-$i.txt > /tmp/plan-b1-$i-rootflags.txt
done
diff /tmp/plan-b1-1-rootflags.txt /tmp/plan-b1-2-rootflags.txt; rc RFD-PLAN-B1-DETERMINISTIC-1-2
diff /tmp/plan-b1-2-rootflags.txt /tmp/plan-b1-3-rootflags.txt; rc RFD-PLAN-B1-DETERMINISTIC-2-3

echo "RFD-Q: B1 -- apply --yes collapses the duplicate to exactly one rootflags="
$D apply /tmp/captured.json --target / --yes $L > /tmp/apply-b1.txt 2>&1; rc RFD-APPLY-B1
cat /tmp/apply-b1.txt
grep options "$ENTRY"
one_rootflags_everywhere; rc RFD-APPLY-B1-SINGLE-ROOTFLAGS

echo "RFD-R: B1 -- plan is now silent"
$D plan /tmp/captured.json --target / $L > /tmp/plan-b1-after.txt 2>&1; rc RFD-PLAN-B1-AFTER
cat /tmp/plan-b1-after.txt
present /tmp/plan-b1-after.txt 'No changes'; rc RFD-PLAN-B1-AFTER-SILENT

# --------------------------------------------------------------------- #
# B2 (blocker, review round 1): an explicit `ro` must converge (plan once,
# apply, plan silent), not flip-flop with the always-derived `rw` forever.
# --------------------------------------------------------------------- #
echo "RFD-S: B2 -- config copy declaring explicit kernel_cmdline ro"
python - <<PY
import json
c = json.load(open("$C"))
c["kernel_cmdline"] = ["console=ttyS0,115200", "ro"]
json.dump(c, open("/tmp/ro.json", "w"), indent=2)
PY
rc RFD-BUILD-RO
$D check /tmp/ro.json $L; rc RFD-RO-CHECK

echo "RFD-T: B2 -- plan shows the ro install"
$D plan /tmp/ro.json --target / $L > /tmp/plan-ro.txt 2>&1; rc RFD-PLAN-RO
cat /tmp/plan-ro.txt
absent /tmp/plan-ro.txt 'No changes'; rc RFD-PLAN-RO-NOT-SILENT
present /tmp/plan-ro.txt 'install ro'; rc RFD-PLAN-RO-HAS-INSTALL

echo "RFD-U: B2 -- apply --yes; the entry carries ro, not rw"
$D apply /tmp/ro.json --target / --yes $L > /tmp/apply-ro.txt 2>&1; rc RFD-APPLY-RO
cat /tmp/apply-ro.txt
grep options "$ENTRY"
python - <<'PY'
import sys
text = open("/boot/loader/entries/arch.conf").read()
line = next(l for l in text.splitlines() if l.startswith("options"))
tokens = line.split()
print("RFD-RO-TOKENS:", tokens)
sys.exit(0 if ("ro" in tokens and "rw" not in tokens) else 1)
PY
rc RFD-APPLY-RO-TOKEN-CHECK
one_rootflags_everywhere; rc RFD-APPLY-RO-SINGLE-ROOTFLAGS

echo "RFD-V: B2 -- plan is silent (converged, NOT flip-flopping)"
$D plan /tmp/ro.json --target / $L > /tmp/plan-ro-2.txt 2>&1; rc RFD-PLAN-RO-AGAIN
cat /tmp/plan-ro-2.txt
present /tmp/plan-ro-2.txt 'No changes'; rc RFD-PLAN-RO-AGAIN-SILENT

echo "RFD-W: B2 -- rollback to the previous generation; plan of the ORIGINAL (rw) config is silent"
$D rollback --target / --yes $L > /tmp/rollback-ro.txt 2>&1; rc RFD-ROLLBACK-RO
cat /tmp/rollback-ro.txt
grep options "$ENTRY"
python - <<'PY'
import sys
text = open("/boot/loader/entries/arch.conf").read()
line = next(l for l in text.splitlines() if l.startswith("options"))
tokens = line.split()
print("RFD-POST-ROLLBACK-RO-TOKENS:", tokens)
sys.exit(0 if ("rw" in tokens and "ro" not in tokens) else 1)
PY
rc RFD-ROLLBACK-RO-RESTORED-RW
$D plan "$C" --target / $L > /tmp/plan-after-rollback-ro.txt 2>&1; rc RFD-PLAN-AFTER-ROLLBACK-RO
cat /tmp/plan-after-rollback-ro.txt
# Scoped to [kernel_cmdline] (what B2 tests), not the whole plan: by this
# point in the script TWO real applies have run back-to-back with no
# intervening rollback-to-a-clean-generation (RFD-Q then RFD-U), and
# `Reconciler._build_new_manifest` records EVERY action's `managed_keys()`
# on every apply with no "actual ∩ (claimable ∪ declared)" narrowing the way
# `sync`'s `_owned_after_sync` has -- so SystemdAction ends up owning
# whatever units happen to be enabled (getty@.service and friends, enabled by
# systemd's own presets, never declared by this config) and the next plan
# against a config that doesn't declare them proposes disabling them. That is
# a real, pre-existing, ORTHOGONAL gap in the apply-manifest path (out of
# scope for this fix -- flagged in the report), not a rootflags/kernel_cmdline
# regression; asserting the whole plan is silent here would fail for a
# reason this fix does not own.
absent /tmp/plan-after-rollback-ro.txt '\[kernel_cmdline\]'; rc RFD-PLAN-AFTER-ROLLBACK-RO-SILENT

echo "RFD-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
