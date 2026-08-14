#!/bin/bash
# The second apply must have nothing left to do (issue #188).
#
# `audit` is declared and pacman brings it in as a dependency of `apparmor`, so
# the first apply used to leave `~ [packages] modify audit (install reason)` for
# the next one. Ends with REASON-DONE, then powers off.
set -x
cd /root/repo || { echo "REASON-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-apparmor.json
echo "REASON: BEGIN"

echo "REASON-A: how pacman recorded the two packages"
pacman -Qi audit    | grep -E '^Install Reason'
pacman -Qi apparmor | grep -E '^Install Reason'

echo "REASON-B: the plan right after the install must be silent"
$D plan "$C" --target / $L; echo "REASON-PLAN-RC=$?"

echo "REASON-C: apply, then plan again"
$D apply "$C" --target / --yes $L; echo "REASON-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "REASON-REPLAN-RC=$?"

echo "REASON-D: force the drift back and check apply repairs it in ONE pass"
pacman -D --asdeps audit
pacman -Qi audit | grep -E '^Install Reason'
$D plan "$C" --target / $L | grep -E "audit"; echo "REASON-DRIFT-RC=$?"
$D apply "$C" --target / --yes $L; echo "REASON-FIX-RC=$?"
pacman -Qi audit | grep -E '^Install Reason'
$D plan "$C" --target / $L; echo "REASON-AFTERFIX-RC=$?"

echo "REASON-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
