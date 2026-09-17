#!/bin/bash
# `pacman.repositories` / `pacman.keys` driven INSIDE the booted guest,
# against the LIVE host (--target /). config/vm-pacman-repo.json installed
# [amt911] + its key + `config-saver` (resolved straight from that repo, no
# package_sources) through the chroot install; this drives every OTHER verb
# from an already-converged machine: check/plan/sync/generations, the two
# hand-edited-drift MODIFY paths (content-correct-but-mispositioned, and
# database-not-synced), the section+key removed by hand (CREATE path), a
# wrong-fingerprint config that must abort BEFORE trusting anything, the whole
# block removed (DELETE path) with an exact-diff proof that nothing else in
# pacman.conf moved, and rollback.
#
# CONVENTION: every PACREPO-<step>-RC=0 line is a PASS; rc() counts the
# failures so the final PACREPO-DONE rc=N is the verdict, not 80 lines to read
# by hand.
#
# Needs NETWORK: the wrong-fingerprint case still downloads the REAL
# amt911.gpg to compare against (that's how the mismatch is detected), and
# `database not synced` re-runs a real `pacman -Sy` against
# https://amt911.github.io/arch-packages/$arch.
#
# The 9p share (/root/repo) is READ-ONLY: every JSON this script drives from
# is a /tmp copy, and every hand-edit of /etc/pacman.conf touches the real
# target file, never the share.
set -x
cd /root/repo || { echo "PACREPO-DONE rc=91"; sync; poweroff -f; exit 91; }
export PYTHONPATH=/root/repo

D="python -m dasik"
L="--no-log"                  # the 9p repo is read-only; the log defaults to $PWD
C=/tmp/repo.json
FPR=6C6568CE34894645A23ABC44B5BD6F8F9023E53B
GNUPGHOME=/etc/pacman.d/gnupg

absent() { ! grep -q "$2" "$1"; }     # rc 0 when the pattern is NOT in the file
present() { grep -q "$2" "$1"; }      # rc 0 when it is
key_trusted() {  # $1 = fingerprint; rc 0 iff a LOCAL sig (FACT-PR-2: class ends 'l') exists
    gpg --homedir "$GNUPGHOME" --with-colons --list-sigs "$1" > /tmp/sigs-check.txt 2>&1
    awk -F: '$1=="sig" && $11 ~ /l$/' /tmp/sigs-check.txt | grep -q .
}

FAILS=0
rc() { local v=$?; [ "$v" -eq 0 ] || FAILS=$((FAILS + 1)); echo "$1-RC=$v"; }

echo "PACREPO: BEGIN (target / = the live booted host)"
echo "PACREPO-DRIVER: $D"

cp config/vm-pacman-repo.json "$C"

echo "PACREPO-A: what the chroot install produced"
pacman -Qi config-saver > /tmp/qi.txt 2>&1; rc PACREPO-PKG-INSTALLED
cat /tmp/qi.txt
pacman -Si config-saver > /tmp/si.txt 2>&1; rc PACREPO-PKG-SI
present /tmp/si.txt 'amt911'; rc PACREPO-PKG-REPO-IS-AMT911
pacman -Sl amt911 > /tmp/sl.txt 2>&1; rc PACREPO-REPO-LISTED
present /tmp/sl.txt 'config-saver'; rc PACREPO-REPO-HAS-PKG
present /tmp/sl.txt 'installed'; rc PACREPO-PKG-MARKED-INSTALLED
key_trusted "$FPR"; rc PACREPO-KEY-TRUSTED
awk '/^\[amt911\]$/{print NR; exit}' /etc/pacman.conf > /tmp/amt_line.txt
awk '/^\[core\]$/{print NR; exit}' /etc/pacman.conf > /tmp/core_line.txt
cat /tmp/amt_line.txt /tmp/core_line.txt
[ -s /tmp/amt_line.txt ] && [ -s /tmp/core_line.txt ] && \
    [ "$(cat /tmp/amt_line.txt)" -lt "$(cat /tmp/core_line.txt)" ]
rc PACREPO-ABOVE-CORE
cp /etc/pacman.conf /tmp/pacman.conf.baseline

echo "PACREPO-B: check, then plan — silent (already converged by the install)"
$D check "$C" $L; rc PACREPO-CHECK
$D plan "$C" --target / $L > /tmp/planB.txt 2>&1; rc PACREPO-PLAN
grep '\[pacman_repositories\]' /tmp/planB.txt
absent /tmp/planB.txt '\[pacman_repositories\]'; rc PACREPO-PLAN-QUIET

echo "PACREPO-C: sync copy -> check -> plan silent; sync from {} captures amt911+fingerprint"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; rc PACREPO-SYNC
python3 - <<'PY'
import json
block = json.load(open("/tmp/captured.json")).get("pacman")
print(json.dumps(block, indent=2))
PY
$D check /tmp/captured.json $L; rc PACREPO-SYNC-CHECK
$D plan /tmp/captured.json --target / $L > /tmp/planC.txt 2>&1; rc PACREPO-SYNC-PLAN
absent /tmp/planC.txt '\[pacman_repositories\]'; rc PACREPO-SYNC-PLAN-QUIET

echo '{}' > /tmp/empty.json
$D sync /tmp/empty.json --target / $L; rc PACREPO-EMPTYSYNC
python3 - <<'PY'
import json, sys
cfg = json.load(open("/tmp/empty.json"))
pacman = cfg.get("pacman") or {}
repos = pacman.get("repositories") or []
keys = pacman.get("keys") or []
print(json.dumps(pacman, indent=2))
ok = (any(r.get("name") == "amt911" for r in repos)
      and any(k.get("fingerprint") == "6C6568CE34894645A23ABC44B5BD6F8F9023E53B" for k in keys))
sys.exit(0 if ok else 1)
PY
rc PACREPO-EMPTYSYNC-CAPTURED

echo "PACREPO-D: generations list it; state.json records the domain"
$D generations --target / $L > /tmp/gens.txt 2>&1; rc PACREPO-GENERATIONS
cat /tmp/gens.txt
present /tmp/gens.txt '^Generation '; rc PACREPO-GENERATIONS-LISTED
present /var/lib/dasik/state.json 'pacman_repositories'; rc PACREPO-MANIFEST

echo "PACREPO-E: remove section+key BY HAND -> plan proposes both creates -> apply -> replan silent"
python3 - <<'PY'
p = "/etc/pacman.conf"
text = open(p).read()
block = "[amt911]\nSigLevel = Required\nServer = https://amt911.github.io/arch-packages/$arch\n\n"
assert block in text, "amt911 block not found verbatim"
open(p, "w").write(text.replace(block, "", 1))
PY
rc PACREPO-HANDREMOVE
pacman-key --delete "$FPR"; rc PACREPO-KEYDEL
$D plan "$C" --target / $L > /tmp/planE.txt 2>&1; rc PACREPO-PLANE
grep '\[pacman_repositories\]' /tmp/planE.txt
present /tmp/planE.txt "create key:$FPR"; rc PACREPO-PLANE-KEY
present /tmp/planE.txt 'create repo:amt911'; rc PACREPO-PLANE-REPO
$D apply "$C" --target / --yes $L; rc PACREPO-APPLYE
$D plan "$C" --target / $L > /tmp/planE2.txt 2>&1; rc PACREPO-REPLANE
absent /tmp/planE2.txt '\[pacman_repositories\]'; rc PACREPO-REPLANE-QUIET

echo "PACREPO-F: move the section BELOW [core] BY HAND -> plan shows 'below [core]' -> apply -> silent"
python3 - <<'PY'
p = "/etc/pacman.conf"
text = open(p).read()
block = "[amt911]\nSigLevel = Required\nServer = https://amt911.github.io/arch-packages/$arch\n\n"
assert block in text, "amt911 block not found verbatim before move"
open(p, "w").write(text.replace(block, "", 1) + block)
PY
rc PACREPO-HANDMOVE
$D plan "$C" --target / $L > /tmp/planF.txt 2>&1; rc PACREPO-PLANF
grep '\[pacman_repositories\]' /tmp/planF.txt
present /tmp/planF.txt 'below \[core\]'; rc PACREPO-PLANF-BELOWCORE
$D apply "$C" --target / --yes $L; rc PACREPO-APPLYF
$D plan "$C" --target / $L > /tmp/planF2.txt 2>&1; rc PACREPO-REPLANF
absent /tmp/planF2.txt '\[pacman_repositories\]'; rc PACREPO-REPLANF-QUIET

echo "PACREPO-G: delete amt911.db -> plan shows 'database not synced' -> apply restores it, core/extra mtimes untouched"
stat -c '%Y' /var/lib/pacman/sync/core.db > /tmp/core_before.txt
stat -c '%Y' /var/lib/pacman/sync/extra.db > /tmp/extra_before.txt
rm -f /var/lib/pacman/sync/amt911.db /var/lib/pacman/sync/amt911.db.sig
$D plan "$C" --target / $L > /tmp/planG.txt 2>&1; rc PACREPO-PLANG
grep '\[pacman_repositories\]' /tmp/planG.txt
present /tmp/planG.txt 'database not synced'; rc PACREPO-PLANG-DBSYNC
$D apply "$C" --target / --yes -v $L > /tmp/applyG.txt 2>&1; rc PACREPO-APPLYG
cat /tmp/applyG.txt
test -f /var/lib/pacman/sync/amt911.db; rc PACREPO-DB-BACK
stat -c '%Y' /var/lib/pacman/sync/core.db > /tmp/core_after.txt
stat -c '%Y' /var/lib/pacman/sync/extra.db > /tmp/extra_after.txt
diff /tmp/core_before.txt /tmp/core_after.txt; rc PACREPO-CORE-MTIME-UNCHANGED
diff /tmp/extra_before.txt /tmp/extra_after.txt; rc PACREPO-EXTRA-MTIME-UNCHANGED
# Stronger than the mtime check above (pacman may skip rewriting a db whose
# content didn't change upstream even on a full -Sy, which would let a
# regression here hide behind "nothing changed to fetch"): the -v apply
# stream must show pacman touching ONLY amt911's database, never core/extra's
# (FACT-PR-4 — the whole reason the single-repo temp conf exists).
absent /tmp/applyG.txt ' core is up to date\| core \|core\.db'; rc PACREPO-SYNC-ISOLATED-CORE
absent /tmp/applyG.txt ' extra is up to date\| extra \|extra\.db'; rc PACREPO-SYNC-ISOLATED-EXTRA
present /tmp/applyG.txt 'amt911'; rc PACREPO-SYNC-TOUCHED-AMT911
$D plan "$C" --target / $L > /tmp/planG2.txt 2>&1; rc PACREPO-REPLANG
absent /tmp/planG2.txt '\[pacman_repositories\]'; rc PACREPO-REPLANG-QUIET

echo "PACREPO-H: a WRONG fingerprint for the same URL must abort before trusting anything"
WRONGFPR=$(python3 -c "print('0' * 40)")
printf '%s' "$WRONGFPR" > /tmp/wrongfpr.txt
python3 - <<'PY'
import json
cfg = json.load(open("/tmp/repo.json"))
wrong = open("/tmp/wrongfpr.txt").read().strip()
cfg["pacman"]["keys"][0]["fingerprint"] = wrong
json.dump(cfg, open("/tmp/wrongfp.json", "w"), indent=2)
PY
rc PACREPO-WRONGFP-BUILD
$D apply /tmp/wrongfp.json --target / --yes $L > /tmp/wrongfp-out.txt 2>&1
wrongfp_apply_rc=$?
echo "PACREPO-WRONGFP-APPLY-RC=$wrongfp_apply_rc"   # inverted: PASS means non-zero
[ "$wrongfp_apply_rc" -eq 0 ] && FAILS=$((FAILS + 1))
cat /tmp/wrongfp-out.txt
# The specific message dasik's own fingerprint check raises — not just "both
# fingerprints appear somewhere", which a plan render (create key:WRONG /
# delete key:REAL) would also satisfy even if the check itself were gutted
# and gpg's own refusal to lsign an unknown key id happened to abort apply
# for an unrelated reason instead.
present /tmp/wrongfp-out.txt 'does not match the downloaded key file'; rc PACREPO-WRONGFP-MISMATCH-MESSAGE
present /tmp/wrongfp-out.txt "$WRONGFPR"; rc PACREPO-WRONGFP-NAMES-WRONG
present /tmp/wrongfp-out.txt "$FPR"; rc PACREPO-WRONGFP-NAMES-REAL
pacman-key --list-keys "$WRONGFPR" > /tmp/wrongkey-lookup.txt 2>&1
wronglookup_rc=$?
echo "PACREPO-WRONGFP-LOOKUP-RC=$wronglookup_rc"   # inverted: PASS means non-zero (never added)
cat /tmp/wrongkey-lookup.txt
[ "$wronglookup_rc" -eq 0 ] && FAILS=$((FAILS + 1))
# the REAL key must still be exactly as trusted as before the failed apply
key_trusted "$FPR"; rc PACREPO-WRONGFP-REAL-KEY-STILL-TRUSTED

echo "PACREPO-I: the whole block removed -> DELETE key+repo -> apply -> plan silent -> pacman.conf == baseline minus our section"
python3 - <<'PY'
import json
cfg = json.load(open("/tmp/repo.json"))
del cfg["pacman"]["repositories"]
del cfg["pacman"]["keys"]
json.dump(cfg, open("/tmp/noblock.json", "w"), indent=2)
PY
rc PACREPO-NOBLOCK-BUILD
$D plan /tmp/noblock.json --target / $L > /tmp/planI.txt 2>&1; rc PACREPO-PLANI
grep '\[pacman_repositories\]' /tmp/planI.txt
present /tmp/planI.txt "delete key:$FPR"; rc PACREPO-PLANI-DELKEY
present /tmp/planI.txt 'delete repo:amt911'; rc PACREPO-PLANI-DELREPO
$D apply /tmp/noblock.json --target / --yes $L; rc PACREPO-APPLYI
gpg --homedir "$GNUPGHOME" --list-keys "$FPR" > /tmp/keygone.txt 2>&1
keygone_rc=$?
[ "$keygone_rc" -ne 0 ]; rc PACREPO-KEY-GONE
grep -q '^\[amt911\]$' /etc/pacman.conf
section_present_rc=$?
[ "$section_present_rc" -ne 0 ]; rc PACREPO-SECTION-GONE
$D plan /tmp/noblock.json --target / $L > /tmp/planI2.txt 2>&1; rc PACREPO-REPLANI
absent /tmp/planI2.txt '\[pacman_repositories\]'; rc PACREPO-REPLANI-QUIET
python3 - <<'PY'
import sys
baseline = open("/tmp/pacman.conf.baseline").read()
block = "[amt911]\nSigLevel = Required\nServer = https://amt911.github.io/arch-packages/$arch\n\n"
assert block in baseline, "baseline missing block?!"
expected = baseline.replace(block, "", 1)
actual = open("/etc/pacman.conf").read()
if expected != actual:
    import difflib
    sys.stdout.writelines(difflib.unified_diff(
        expected.splitlines(True), actual.splitlines(True),
        fromfile="expected", tofile="actual"))
sys.exit(0 if expected == actual else 1)
PY
rc PACREPO-CONF-OTHERWISE-UNCHANGED

echo "PACREPO-J: rollback restores the section+key, plan silent again"
$D generations --target / $L | tail -20
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; rc PACREPO-ROLLBACK
tail -15 /tmp/rollback.txt
grep -q '^\[amt911\]$' /etc/pacman.conf; rc PACREPO-ROLLBACK-SECTION-BACK
key_trusted "$FPR"; rc PACREPO-ROLLBACK-KEY-TRUSTED
$D plan "$C" --target / $L > /tmp/planJ.txt 2>&1; rc PACREPO-REPLANJ
absent /tmp/planJ.txt '\[pacman_repositories\]'; rc PACREPO-REPLANJ-QUIET

echo "PACREPO-K: the pacman key ABSENT from the WHOLE config (not just its repositories/keys sub-fields, as PACREPO-I already covers) -> still DELETE what the manifest owns. This is finding 1's fix: Reconciler._any_managed_for probes an optional action whose config slice is absent via cls.__new__(cls) (no __init__); PacmanRepositoriesAction used to raise AttributeError there (self._repos/self._keys never set), got swallowed, and the whole domain was skipped instead of planning the DELETE."
python3 - <<'PY'
import json
cfg = json.load(open("/tmp/repo.json"))
del cfg["pacman"]
json.dump(cfg, open("/tmp/nopacman.json", "w"), indent=2)
PY
rc PACREPO-NOPACMAN-BUILD
$D plan /tmp/nopacman.json --target / $L > /tmp/planK.txt 2>&1; rc PACREPO-PLANK
grep '\[pacman_repositories\]' /tmp/planK.txt
present /tmp/planK.txt "delete key:$FPR"; rc PACREPO-PLANK-DELKEY
present /tmp/planK.txt 'delete repo:amt911'; rc PACREPO-PLANK-DELREPO
$D apply /tmp/nopacman.json --target / --yes $L; rc PACREPO-APPLYK
grep -q '^\[amt911\]$' /etc/pacman.conf
amt911_section_rc=$?
[ "$amt911_section_rc" -ne 0 ]; rc PACREPO-K-SECTION-GONE
gpg --homedir "$GNUPGHOME" --list-keys "$FPR" > /tmp/keygoneK.txt 2>&1
keygoneK_rc=$?
cat /tmp/keygoneK.txt
[ "$keygoneK_rc" -ne 0 ]; rc PACREPO-K-KEY-GONE
$D plan /tmp/nopacman.json --target / $L > /tmp/planK2.txt 2>&1; rc PACREPO-REPLANK
absent /tmp/planK2.txt '\[pacman_repositories\]'; rc PACREPO-REPLANK-QUIET

echo "PACREPO: END with $FAILS failure(s)"
echo "PACREPO-DONE rc=$FAILS"
sync
poweroff -f
