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
# Re-review round 2 (scratchpad/rereview-rootflags-findings.md) added:
#
#   SF-1 (B1 residual). A duplicate that already contains the config's own
#       literal read as converged forever -- sections O2-R2 below reproduce
#       that exact shape (the captured literal itself as the duplicate, not
#       an unrelated zstd:1) and drive plan -> apply -> plan across it.
#   SF-4. Applying a SYNCED config takes ownership, BY DECLARATION, of every
#       enabled unit sync captured as drift (getty@.service and friends,
#       enabled by systemd's own presets, never declared by
#       config/vm-rootflags-drift.json) -- planning an undeclared config then
#       proposes disabling them by the documented "owned but no longer
#       declared -> REMOVE" rule. That is correct, INTENDED behaviour, not
#       "_build_new_manifest widening ownership on apply" as an earlier draft
#       of this comment claimed (re-review item 4: no such widening exists --
#       SystemdAction.managed_keys() is the DECLARED set, never actual()).
#       It is simply orthogonal to what THIS script tests, so every derived
#       config from RFD-C onward now strips the captured "systemd" block
#       (RFD-STRIP-SYSTEMD-DRIFT) before use: no generation in this run ever
#       DECLARES those units, so none of them is ever enabled or disabled for
#       real by this script, and the whole-plan "No changes" assertions hold
#       at RFD-N and RFD-W (not just a [kernel_cmdline]-scoped one). RFD-BASELINE-*
#       /RFD-FINAL-* independently prove it: getty@.service and
#       systemd-networkd.socket are read right after install and compared
#       byte-for-byte against their state at the very end of the run.
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

# SF-4 (re-review round 2): baseline preset-unit state, right after install,
# before sync ever captures anything as drift. Compared byte-for-byte
# against RFD-FINAL-* at the very end -- the invariant this run is supposed
# to hold end to end is that NEITHER preset unit is ever toggled for real,
# because no config used below ever declares them (see RFD-STRIP-SYSTEMD-DRIFT).
GETTY_BASELINE=$(systemctl is-enabled getty@.service 2>&1)
NETWORKD_BASELINE=$(systemctl is-enabled systemd-networkd.socket 2>&1)
echo "RFD-BASELINE-GETTY=$GETTY_BASELINE RFD-BASELINE-NETWORKD=$NETWORKD_BASELINE"

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

echo "RFD-STRIP-SYSTEMD-DRIFT: SF-4 (re-review round 2) -- drop the captured"
echo "  'systemd' block so no generation in this run ever DECLARES the preset"
echo "  units sync captured as drift (getty@.service and friends); applying a"
echo "  config that declares them takes ownership of them, and this script's"
echo "  job is kernel_cmdline/rootflags, not systemd preset toggling."
python - <<'PY'
import json
path = "/tmp/captured.json"
c = json.load(open(path))
had = c.pop("systemd", None)
json.dump(c, open(path, "w"), indent=2)
print("RFD-STRIPPED-SYSTEMD-KEYS:", sorted(had) if had else None)
PY
rc RFD-STRIP-SYSTEMD-DRIFT

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
# SF-1 (re-review round 2, B1 residual): a duplicate where one of the two
# live tokens IS ALREADY the config's own literal must still be planned as
# an INSTALL -- returning the literal unchanged from the plan-time diff is
# not the same as `compute_changes` actually planning it once that literal
# is already an element of `actual()`. Shape A from the unit tests: a bare
# `zstd` variant standing next to the already-correct literal.
# --------------------------------------------------------------------- #
echo "RFD-O2: SF-1 -- hand-write a duplicate where one token IS the captured literal (shape A)"
python - <<'PY'
import re
path = "/boot/loader/entries/arch.conf"
text = open(path).read()
m = re.search(r'rootflags=\S+', text)
assert m, text
literal = m.group(0)
bare = re.sub(r'compress-force=zstd:\d+', 'compress-force=zstd', literal)
assert bare != literal, f"could not build a distinct bare duplicate from {literal!r}"
text = text.replace(literal, bare + " " + literal, 1)
open(path, "w").write(text)
print("RFD-O2-ENTRY-OPTIONS:", [l for l in text.splitlines() if l.startswith("options")])
PY
rc RFD-O2-BUILD-DUPLICATE
grep options "$ENTRY"

echo "RFD-P2: SF-1 -- plan still INSTALLs even though the literal is already ONE of the duplicates"
$D plan /tmp/captured.json --target / $L > /tmp/plan-o2.txt 2>&1; rc RFD-PLAN-O2
cat /tmp/plan-o2.txt
absent /tmp/plan-o2.txt 'No changes'; rc RFD-PLAN-O2-NOT-SILENT
present /tmp/plan-o2.txt 'rootflags='; rc RFD-PLAN-O2-HAS-ROOTFLAGS

echo "RFD-Q2: SF-1 -- apply --yes collapses the shape-A duplicate to exactly one rootflags="
$D apply /tmp/captured.json --target / --yes $L > /tmp/apply-o2.txt 2>&1; rc RFD-APPLY-O2
cat /tmp/apply-o2.txt
grep options "$ENTRY"
one_rootflags_everywhere; rc RFD-APPLY-O2-SINGLE-ROOTFLAGS

echo "RFD-R2: SF-1 -- plan is now silent"
$D plan /tmp/captured.json --target / $L > /tmp/plan-o2-after.txt 2>&1; rc RFD-PLAN-O2-AFTER
cat /tmp/plan-o2-after.txt
present /tmp/plan-o2-after.txt 'No changes'; rc RFD-PLAN-O2-AFTER-SILENT

# --------------------------------------------------------------------- #
# B2 (blocker, review round 1): an explicit `ro` must converge (plan once,
# apply, plan silent), not flip-flop with the always-derived `rw` forever.
# --------------------------------------------------------------------- #
echo "RFD-S: B2 -- config copy declaring explicit kernel_cmdline ro"
# SF-4 (re-review round 2): derived from the (systemd-stripped) captured.json
# lineage, not bare $C -- every config applied from RFD-C onward now agrees
# on declaring NOTHING for systemd, so the presets are never enabled or
# disabled for real anywhere in this run (see RFD-STRIP-SYSTEMD-DRIFT).
python - <<'PY'
import json
c = json.load(open("/tmp/captured.json"))
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
# SF-4 (re-review round 2): the WHOLE plan is asserted silent here, not just
# `[kernel_cmdline]` -- by this point TWO real applies have run back-to-back
# with no intervening rollback-to-a-clean-generation (RFD-Q then RFD-U), but
# every config used since RFD-STRIP-SYSTEMD-DRIFT (zstd1.json, the B1
# collapse's captured.json, ro.json) agrees on declaring NOTHING for
# systemd, so `SystemdAction`'s managed set never grows to include the
# preset-enabled units in the first place -- there is nothing here for the
# documented "owned but no longer declared -> REMOVE" rule to catch, so the
# whole plan, including systemd, is genuinely silent.
present /tmp/plan-after-rollback-ro.txt 'No changes'; rc RFD-PLAN-AFTER-ROLLBACK-RO-SILENT

# SF-4: the stronger, direct proof -- neither preset unit was ever toggled
# for real anywhere in this run, not just that the FINAL plan is quiet about
# them. Compared byte-for-byte against RFD-BASELINE-* (captured right after
# install, before sync ever ran).
GETTY_FINAL=$(systemctl is-enabled getty@.service 2>&1)
NETWORKD_FINAL=$(systemctl is-enabled systemd-networkd.socket 2>&1)
echo "RFD-FINAL-GETTY=$GETTY_FINAL RFD-FINAL-NETWORKD=$NETWORKD_FINAL"
[ "$GETTY_FINAL" = "$GETTY_BASELINE" ]; rc RFD-PRESET-GETTY-NEVER-TOGGLED
[ "$NETWORKD_FINAL" = "$NETWORKD_BASELINE" ]; rc RFD-PRESET-NETWORKD-NEVER-TOGGLED

echo "RFD-DONE rc=$FAILS"
sync
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
