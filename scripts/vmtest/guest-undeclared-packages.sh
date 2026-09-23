#!/bin/bash
# `package_policy.undeclared: remove` -- a package installed by hand, outside
# the config, must show up in `plan` and go away on `apply`.
#
# 2026-09-23, ThinkPad P14s: a package installed outside the config and
# `dasik plan` said nothing. By design dasik only removes what its manifest
# owns; the opt-in policy makes the machine converge to what the config lists.
#
# The config deliberately does NOT list base/linux/linux-firmware/mkinitcpio:
# a hand-written config never does, and the policy must never plan them.
#
# Every verb: check, plan, apply, sync, generations, rollback, as round trips,
# plus the block removed (`keep`). Emits UNDECL-* markers; ends with
# UNDECL-DONE rc=<failures>.
set -x
cd /root/repo || { echo "UNDECL-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo
D="python -m dasik"
L="--no-log"
C=config/vm-undeclared-packages.json
FAILS=0
ok()  { echo "$1-RC=0"; }
bad() { echo "$1-RC=1 ($2)"; FAILS=$((FAILS + 1)); }
check() { local m=$1; shift; if "$@"; then ok "$m"; else bad "$m" "$*"; fi; }
not() { ! "$@"; }
gens() { $D generations --target / $L 2>/dev/null | grep -c '^ *Generation [0-9]'; }
pkg() { pacman -Qq | grep -qx "$1"; }
# A [packages] line in a plan output file.
pkglines() { grep -E '^\s+[-+~] \[packages\]' "$1"; }
plan_to() { $D plan --target / $L "$1" > "$2" 2>&1; echo "plan rc=$?" >> "$2"; cat "$2"; }
# The config with the block dropped: today's ownership model.
python - "$C" /root/keep.json <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg.pop("package_policy")
json.dump(cfg, open(sys.argv[2], "w"), indent=2)
PY

echo "UNDECL: BEGIN"
pacman -Rns --noconfirm cowsay figlet 2>/dev/null
pacman -D --asdeps python-annotated-types 2>/dev/null

echo "UNDECL-A: check, and a fresh install already converges"
check UNDECL-A-CHECK $D check "$C" $L
check UNDECL-A-CHECK-KEEP $D check /root/keep.json $L
plan_to "$C" /tmp/plan0.txt
check UNDECL-A-PLAN-RC0 grep -q 'plan rc=0' /tmp/plan0.txt
check UNDECL-A-PLAN-SILENT not pkglines /tmp/plan0.txt
# The base is explicit on the machine and absent from the config.
check UNDECL-A-BASE-EXPLICIT bash -c "pacman -Qqe | grep -qx linux"
check UNDECL-A-BASE-NOT-DECLARED python -c "import json,sys; sys.exit('linux' in json.load(open('$C'))['packages'])"

echo "UNDECL-B: a hand-installed package is planned for removal"
pacman -S --noconfirm cowsay
check UNDECL-B-INSTALLED pkg cowsay
plan_to "$C" /tmp/plan1.txt
check UNDECL-B-PLAN-REMOVES grep -q 'remove cowsay' /tmp/plan1.txt
check UNDECL-B-PLAN-REASON grep -q 'package_policy.undeclared: remove' /tmp/plan1.txt
check UNDECL-B-ONLY-COWSAY test "$(pkglines /tmp/plan1.txt | wc -l)" -eq 1
check UNDECL-B-PLAN-CHANGED-NOTHING pkg cowsay

echo "UNDECL-K: the block dropped -- keep -- leaves it alone"
plan_to /root/keep.json /tmp/plan-keep.txt
check UNDECL-K-PLAN-RC0 grep -q 'plan rc=0' /tmp/plan-keep.txt
check UNDECL-K-PLAN-SILENT not pkglines /tmp/plan-keep.txt

echo "UNDECL-R: red proof - with the policy stubbed out, plan goes quiet"
python - "$C" > /tmp/plan-nofix.txt 2>&1 <<'RED'
import runpy, sys
import dasik.lib.actions.packages_action as p
p.PackagesAction._undeclared = lambda self, explicit, installed, covered: set()
sys.argv = ["dasik", "plan", sys.argv[1], "--target", "/", "--no-log"]
runpy.run_module("dasik", run_name="__main__")
RED
cat /tmp/plan-nofix.txt
check UNDECL-R-QUIET-WITHOUT-FIX not grep -q 'remove cowsay' /tmp/plan-nofix.txt

echo "UNDECL-D: a package something installed requires is never planned"
pacman -D --asexplicit python-annotated-types
plan_to "$C" /tmp/plan-dep.txt
check UNDECL-D-NOT-PLANNED not grep -q 'remove python-annotated-types' /tmp/plan-dep.txt
check UNDECL-D-WARNS grep -q 'not removing python-annotated-types' /tmp/plan-dep.txt
check UNDECL-D-COWSAY-STILL grep -q 'remove cowsay' /tmp/plan-dep.txt

echo "UNDECL-E: apply removes it, the base stays, and plan -> apply -> plan is silent"
G0=$(gens)
$D apply "$C" --target / --yes $L > /tmp/apply1.txt 2>&1; echo "apply rc=$?" >> /tmp/apply1.txt
cat /tmp/apply1.txt
check UNDECL-E-APPLY-RC0 grep -q 'apply rc=0' /tmp/apply1.txt
check UNDECL-E-COWSAY-GONE not pkg cowsay
check UNDECL-E-DEP-KEPT pkg python-annotated-types
for p in base linux linux-firmware mkinitcpio; do
  check "UNDECL-E-KEPT-$p" pkg "$p"
done
plan_to "$C" /tmp/plan2.txt
check UNDECL-E-PLAN-SILENT not pkglines /tmp/plan2.txt
$D apply "$C" --target / --yes $L > /tmp/apply2.txt 2>&1; echo "apply rc=$?" >> /tmp/apply2.txt
check UNDECL-E-REAPPLY-RC0 grep -q 'apply rc=0' /tmp/apply2.txt
check UNDECL-E-REAPPLY-NOOP not pkglines /tmp/apply2.txt

echo "UNDECL-S: sync is how you keep one -- sync -> check -> plan is silent"
pacman -D --asdeps python-annotated-types
pacman -S --noconfirm figlet
rm -rf /root/cfg && mkdir -p /root/cfg && cp "$C" /root/cfg/cap.json
check UNDECL-S-SYNC $D sync /root/cfg/cap.json --target / $L
cat /root/cfg/cap.json
check UNDECL-S-CAPTURED grep -q '"figlet"' /root/cfg/cap.json
check UNDECL-S-POLICY-SURVIVES grep -q '"undeclared": "remove"' /root/cfg/cap.json
check UNDECL-S-CHECK $D check /root/cfg/cap.json $L
plan_to /root/cfg/cap.json /tmp/plan3.txt
check UNDECL-S-PLAN-SILENT not pkglines /tmp/plan3.txt
check UNDECL-S-FIGLET-KEPT pkg figlet
# ...while the ORIGINAL config, which does not list it, still wants it gone.
plan_to "$C" /tmp/plan4.txt
check UNDECL-S-SEED-STILL-REMOVES grep -q 'remove figlet' /tmp/plan4.txt

echo "UNDECL-P: an explicit package that PROVIDES a declared name is the declaration"
# `netcat` is satisfied by openbsd-netcat (`pacman -Qq netcat` prints it), and
# nothing requires openbsd-netcat, so no dependency check masks the result:
# planning it for removal would re-plan `netcat` on every apply. (bash was the
# first attempt; half of base requires it, so the red could never show.)
python - /root/cfg/cap.json /root/nc.json <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg["packages"].append("netcat")
json.dump(cfg, open(sys.argv[2], "w"), indent=2)
PY
pacman -S --noconfirm openbsd-netcat
check UNDECL-P-CHECK $D check /root/nc.json $L
plan_to /root/nc.json /tmp/plan-nc.txt
check UNDECL-P-PROVIDER-NOT-PLANNED not grep -q 'remove openbsd-netcat' /tmp/plan-nc.txt
check UNDECL-P-NAME-NOT-PLANNED not grep -q 'install netcat' /tmp/plan-nc.txt
check UNDECL-P-PLAN-SILENT not pkglines /tmp/plan-nc.txt
python - /root/nc.json > /tmp/plan-nc-nofix.txt 2>&1 <<'RED'
import runpy, sys
import dasik.lib.actions.packages_action as p
p.PackagesAction._providers_of = lambda self, names: set()
sys.argv = ["dasik", "plan", sys.argv[1], "--target", "/", "--no-log"]
runpy.run_module("dasik", run_name="__main__")
RED
cat /tmp/plan-nc-nofix.txt
check UNDECL-P-RED-WITHOUT-FIX grep -q 'remove openbsd-netcat' /tmp/plan-nc-nofix.txt
pacman -Rns --noconfirm openbsd-netcat

echo "UNDECL-H: generations and rollback"
$D generations --target / $L
check UNDECL-H-HAS-CURRENT bash -c "$D generations --target / $L | grep -q '(current)'"
check UNDECL-H-GENERATIONS-GREW test "$(gens)" -gt "$G0"
$D rollback --target / --yes $L > /tmp/rollback.txt 2>&1; echo "rollback rc=$?" >> /tmp/rollback.txt
cat /tmp/rollback.txt
check UNDECL-H-ROLLBACK-RC0 grep -q 'rollback rc=0' /tmp/rollback.txt
for p in base linux linux-firmware mkinitcpio python; do
  check "UNDECL-H-KEPT-$p" pkg "$p"
done
# Informational: what the restored generation did with the hand-installed figlet.
pkg figlet && echo "UNDECL-H-INFO figlet present after rollback" \
           || echo "UNDECL-H-INFO figlet removed by rollback"
plan_to "$C" /tmp/plan5.txt

echo "UNDECL-DONE rc=$FAILS"
poweroff -f
