#!/bin/bash
# The end of an apply names the declared packages it did NOT install.
#
# config/vm-unknown-git.json declares dasik-package-does-not-exist-12345 under
# package_policy.unknown: warn-and-skip (the default), so every apply ends with
# exit 0 and that package missing. The skip is warned when it happens — among
# thousands of lines of pacman output — and before this fix the run then ended
# on a success line. PackagesAction.finalize_apply() now repeats it as the last
# thing printed.
#
# Red proof: the same apply with finalize_apply deleted in memory must NOT end
# with the summary.
#
# Every verb: check, plan, apply, generations, rollback, sync -> check -> plan.
# Emits SKIP-* markers; ends with SKIP-DONE rc=<failures>.
set -x
cd /root/repo || { echo "SKIP-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
D="python -m dasik"
L="--no-log"
C=config/vm-unknown-git.json
GHOST=dasik-package-does-not-exist-12345
FAILS=0
ok()  { echo "$1-RC=0"; }
bad() { echo "$1-RC=1 ($2)"; FAILS=$((FAILS + 1)); }
check() { local m=$1; shift; if "$@"; then ok "$m"; else bad "$m" "$*"; fi; }
not() { ! "$@"; }
# The last 4 non-empty lines of a run: what the user actually reads at the end.
tail_of() { sed 's/\x1b\[[0-9;]*m//g' "$1" | grep -v '^\s*$' | tail -4; }
ends_with_summary() { tail_of "$1" | grep -q "NOT installed by this apply.*$GHOST"; }
gens() { $D generations --target / $L 2>/dev/null | grep -c '^ *Generation [0-9]'; }

echo "SKIP: BEGIN"
check SKIP-GHOST-ABSENT not pacman -Q "$GHOST"

echo "SKIP-A: check"
check SKIP-A-CHECK $D check "$C" $L

echo "SKIP-B: plan keeps proposing the package it could not install"
$D plan --target / $L "$C" > /tmp/plan1.txt 2>&1; cat /tmp/plan1.txt
check SKIP-B-PLAN-LISTS-GHOST grep -q "install $GHOST" /tmp/plan1.txt

echo "SKIP-C: apply ends with the summary"
G0=$(gens)
$D apply "$C" --target / --yes $L > /tmp/apply.txt 2>&1; echo "apply rc=$?" >> /tmp/apply.status
cat /tmp/apply.txt; tail_of /tmp/apply.txt
check SKIP-C-APPLY-RC0 grep -q 'apply rc=0' /tmp/apply.status
check SKIP-C-ENDS-WITH-SUMMARY ends_with_summary /tmp/apply.txt
check SKIP-C-GHOST-STILL-ABSENT not pacman -Q "$GHOST"

echo "SKIP-R: red proof - without finalize_apply the run does NOT end with it"
python - "$C" > /tmp/apply-nofix.txt 2>&1 <<'PY'
import runpy, sys
import dasik.lib.actions.packages_action as p
del p.PackagesAction.finalize_apply          # back to the inherited no-op
sys.argv = ["dasik", "apply", sys.argv[1], "--target", "/", "--yes", "--no-log"]
runpy.run_module("dasik", run_name="__main__")
PY
tail_of /tmp/apply-nofix.txt
check SKIP-R-NO-SUMMARY-WITHOUT-FIX not ends_with_summary /tmp/apply-nofix.txt
check SKIP-R-SKIP-STILL-WARNED-MIDWAY grep -q "packages skipped because no source was found: $GHOST" /tmp/apply-nofix.txt

echo "SKIP-D: plan after apply still lists it (retried, not forgotten)"
$D plan --target / $L "$C" > /tmp/plan2.txt 2>&1; cat /tmp/plan2.txt
check SKIP-D-REPLAN-LISTS-GHOST grep -q "install $GHOST" /tmp/plan2.txt

echo "SKIP-E: generations"
$D generations --target / $L
check SKIP-E-HAS-CURRENT bash -c "$D generations --target / $L | grep -q '(current)'"
check SKIP-E-MANIFEST-DOES-NOT-CLAIM-GHOST not grep -rq "$GHOST" /var/lib/dasik/state.json

echo "SKIP-F: rollback (re-applies: the summary shows there too)"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; echo "rollback rc=$?" > /tmp/rollback.status
cat /tmp/rollback.txt
check SKIP-F-ROLLBACK-RC0 grep -q 'rollback rc=0' /tmp/rollback.status
check SKIP-F-GENERATIONS-KEPT test "$(gens)" -ge "$G0"

echo "SKIP-G: sync -> check -> plan"
rm -rf /root/cfg && mkdir -p /root/cfg && cp "$C" /root/cfg/cap.json
check SKIP-G-SYNC $D sync /root/cfg/cap.json --target / $L
check SKIP-G-CHECK $D check /root/cfg/cap.json $L
check SKIP-G-SYNC-REPORTS-REALITY not grep -q "$GHOST" /root/cfg/cap.json
$D plan --target / $L /root/cfg/cap.json > /tmp/plan3.txt 2>&1; cat /tmp/plan3.txt
check SKIP-G-PLAN-SILENT grep -q "No changes" /tmp/plan3.txt

echo "SKIP-DONE rc=$FAILS"
poweroff -f
