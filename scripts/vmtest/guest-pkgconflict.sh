#!/bin/bash
# An installed package that blocks a declared one must be NAMED, not dumped.
#
# 2026-09-20, MSI GE63: `jdupes` could not install because the AUR
# `libjodycode-git` was still on the machine. pacman said what to do
# ("Remove libjodycode-git?") and dasik printed the failure twice without ever
# repeating it. Reproduced here with a repo-only pair: `openresolv` and
# `systemd-resolvconf` both provide `resolvconf`, so pacman refuses any
# transaction holding both -- and, as on the GE63, NEITHER package names the
# other (`pacman -Si systemd-resolvconf` conflicts with `resolvconf`, a
# provide, not with openresolv). Metadata cannot answer this; `pacman -Sp` can.
#
# The guest starts converged (the install declared systemd-resolvconf), then
# recreates the failure: remove it, install the blocker.
#
# Every verb: check, plan, apply, sync, generations, rollback, as round trips.
# Emits CONFL-* markers; ends with CONFL-DONE rc=<failures>.
set -x
cd /root/repo || { echo "CONFL-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
D="python -m dasik"
L="--no-log"
C=config/vm-pkgconflict.json
WANT=systemd-resolvconf
BLOCKER=openresolv
FAILS=0
ok()  { echo "$1-RC=0"; }
bad() { echo "$1-RC=1 ($2)"; FAILS=$((FAILS + 1)); }
check() { local m=$1; shift; if "$@"; then ok "$m"; else bad "$m" "$*"; fi; }
not() { ! "$@"; }
gens() { $D generations --target / $L 2>/dev/null | grep -c '^ *Generation [0-9]'; }
# `pacman -Q <name>` RESOLVES A PROVIDE: with openresolv removed it still
# answers "systemd-resolvconf 261.3-1", because systemd-resolvconf provides
# openresolv. Measured in this very guest. So the only honest question about a
# PACKAGE is an exact match in `pacman -Qq`.
pkg() { pacman -Qq | grep -qx "$1"; }
# The config with the opt-in policy, and one declaring BOTH sides of the pair.
mkcfg() {
  python - "$C" "$1" "$2" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg.setdefault("package_policy", {})["conflicts"] = sys.argv[3]
if sys.argv[3] == "replace-both":
    cfg["package_policy"]["conflicts"] = "replace"
    cfg["packages"].append("openresolv")
json.dump(cfg, open(sys.argv[2], "w"), indent=2)
PY
}

echo "CONFL: BEGIN"
mkcfg /root/replace.json replace
mkcfg /root/both.json replace-both

echo "CONFL-A: recreate the GE63 state - the blocker installed, the declared package gone"
pacman -Rns --noconfirm "$WANT"
pacman -S --noconfirm "$BLOCKER"
check CONFL-A-BLOCKER-INSTALLED pkg "$BLOCKER"
check CONFL-A-WANT-ABSENT not pkg "$WANT"
# The bare pacman truth this whole feature exists for: the transaction fails.
pacman -S --noconfirm --needed "$WANT" > /tmp/bare.txt 2>&1
check CONFL-A-PACMAN-REALLY-REFUSES grep -qi "conflict" /tmp/bare.txt

echo "CONFL-B: check accepts every variant"
check CONFL-B-CHECK $D check "$C" $L
check CONFL-B-CHECK-REPLACE $D check /root/replace.json $L
check CONFL-B-CHECK-BOTH $D check /root/both.json $L

echo "CONFL-C: plan names the blocker and the exact command, and mutates nothing"
$D plan --target / $L "$C" > /tmp/plan1.txt 2>&1; echo "plan rc=$?" > /tmp/plan1.status
cat /tmp/plan1.txt
check CONFL-C-PLAN-RC0 grep -q 'plan rc=0' /tmp/plan1.status
check CONFL-C-PLAN-NAMES-BLOCKER grep -q "$BLOCKER" /tmp/plan1.txt
check CONFL-C-PLAN-GIVES-COMMAND grep -q "pacman -Rns $BLOCKER" /tmp/plan1.txt
check CONFL-C-PLAN-STILL-PROPOSES grep -q "install $WANT" /tmp/plan1.txt
check CONFL-C-PLAN-CHANGED-NOTHING pkg "$BLOCKER"

echo "CONFL-R: red proof - with the announcement stubbed out, both verbs go quiet"
for verb in plan apply; do
  python - "$C" "$verb" > /tmp/$verb-nofix.txt 2>&1 <<'RED'
import runpy, sys
import dasik.lib.actions.packages_action as p
p.PackagesAction._announce = lambda self, conflicts, installed: None
verb = sys.argv[2]
sys.argv = ["dasik", verb, sys.argv[1], "--target", "/", "--no-log"] + (
    ["--yes"] if verb == "apply" else [])
runpy.run_module("dasik", run_name="__main__")
RED
  cat /tmp/$verb-nofix.txt
done
check CONFL-R-PLAN-QUIET-WITHOUT-FIX not grep -q "pacman -Rns $BLOCKER" /tmp/plan-nofix.txt
check CONFL-R-APPLY-QUIET-WITHOUT-FIX not grep -q "pacman -Rns $BLOCKER" /tmp/apply-nofix.txt
check CONFL-R-SAME-PLAN-WITHOUT-FIX grep -q "install $WANT" /tmp/plan-nofix.txt
# ... while the raw pacman failure is still there: what went missing is the
# MESSAGE, not the failure.
check CONFL-R-STILL-FAILS grep -qi "conflict" /tmp/apply-nofix.txt

echo "CONFL-D: the default policy says it too, and removes nothing"
G0=$(gens)
$D apply "$C" --target / --yes $L > /tmp/apply1.txt 2>&1; echo "apply rc=$?" > /tmp/apply1.status
cat /tmp/apply1.txt
check CONFL-D-APPLY-RC0 grep -q 'apply rc=0' /tmp/apply1.status
check CONFL-D-APPLY-GIVES-COMMAND grep -q "pacman -Rns $BLOCKER" /tmp/apply1.txt
check CONFL-D-BLOCKER-KEPT pkg "$BLOCKER"
check CONFL-D-WANT-STILL-ABSENT not pkg "$WANT"
check CONFL-D-NOT-CLAIMED-INSTALLED not grep -q "\"$WANT\"" /var/lib/dasik/state.json

echo "CONFL-E: replace refuses when the config declares BOTH sides"
$D apply /root/both.json --target / --yes $L > /tmp/apply-both.txt 2>&1
cat /tmp/apply-both.txt
check CONFL-E-REFUSES grep -q "declares both" /tmp/apply-both.txt
check CONFL-E-BLOCKER-KEPT pkg "$BLOCKER"

echo "CONFL-F: replace clears an undeclared blocker and converges"
$D apply /root/replace.json --target / --yes $L > /tmp/apply2.txt 2>&1; echo "apply rc=$?" > /tmp/apply2.status
cat /tmp/apply2.txt
check CONFL-F-APPLY-RC0 grep -q 'apply rc=0' /tmp/apply2.status
check CONFL-F-BLOCKER-GONE not pkg "$BLOCKER"
check CONFL-F-WANT-INSTALLED pkg "$WANT"

echo "CONFL-G: plan -> apply -> plan is silent (idempotent)"
$D plan --target / $L /root/replace.json > /tmp/plan2.txt 2>&1; cat /tmp/plan2.txt
check CONFL-G-PLAN-SILENT bash -c "! grep -E '^\s+[-+~] \[packages\]' /tmp/plan2.txt"
check CONFL-G-NO-STALE-WARNING not grep -q "pacman -Rns" /tmp/plan2.txt
$D apply /root/replace.json --target / --yes $L > /tmp/apply3.txt 2>&1; echo "apply rc=$?" > /tmp/apply3.status
check CONFL-G-REAPPLY-RC0 grep -q 'apply rc=0' /tmp/apply3.status

echo "CONFL-H: generations and rollback"
$D generations --target / $L
check CONFL-H-HAS-CURRENT bash -c "$D generations --target / $L | grep -q '(current)'"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; echo "rollback rc=$?" > /tmp/rollback.status
cat /tmp/rollback.txt
check CONFL-H-ROLLBACK-RC0 grep -q 'rollback rc=0' /tmp/rollback.status
check CONFL-H-GENERATIONS-KEPT test "$(gens)" -ge "$G0"

echo "CONFL-I: sync -> check -> plan"
rm -rf /root/cfg && mkdir -p /root/cfg && cp /root/replace.json /root/cfg/cap.json
check CONFL-I-SYNC $D sync /root/cfg/cap.json --target / $L
check CONFL-I-CHECK $D check /root/cfg/cap.json $L
check CONFL-I-POLICY-SURVIVES grep -q '"conflicts"' /root/cfg/cap.json
$D plan --target / $L /root/cfg/cap.json > /tmp/plan3.txt 2>&1; cat /tmp/plan3.txt
check CONFL-I-PLAN-SILENT bash -c "! grep -E '^\s+[-+~] \[packages\]' /tmp/plan3.txt"

echo "CONFL-DONE rc=$FAILS"
poweroff -f
