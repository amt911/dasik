#!/bin/bash
# `package_sources` round trip, checked INSIDE the booted guest.
#
# The install built config-saver from a Git PKGBUILD (it is in no repo and in no
# AUR) and skipped an impossible name under warn-and-skip. The question only a
# real machine answers: does `sync` carry the SOURCE back, so the captured
# config can rebuild the package instead of silently dropping it?
#
# Ends with SRC-DONE, then powers off.
set -x
cd /root/repo || { echo "SRC-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"                # the 9p repo is read-only; the run log defaults to $PWD
C=config/vm-unknown-git.json
echo "SRC: BEGIN (target / = the live booted host)"

echo "SRC-A: what the install produced"
pacman -Q config-saver && echo "SRC-PKG: ok" || echo "SRC-PKG: MISSING"
pacman -Q htop
pacman -Q dasik-package-does-not-exist-12345 && echo "SRC-SKIPPED: WRONG" \
    || echo "SRC-SKIPPED: ok (warn-and-skip left it out)"

echo "SRC-B: check / plan / apply / plan"
$D check "$C" $L; echo "SRC-CHECK-RC=$?"
$D plan "$C" --target / $L; echo "SRC-PLAN-RC=$?"
$D apply "$C" --target / --yes $L; echo "SRC-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "SRC-REPLAN-RC=$?"

echo "SRC-C: sync from the DECLARED config — the source must survive"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "SRC-SYNC-RC=$?"
python -c 'import json;print("SRC-CAPTURED:",json.dumps(json.load(open("/tmp/captured.json")).get("package_sources")))'
$D check /tmp/captured.json $L; echo "SRC-CAPCHECK-RC=$?"

echo "SRC-D: sync from an EMPTY seed — the manifest is all there is"
python - <<'PY'
import json
json.dump({"hostname": "dasik-unknown-git"}, open("/tmp/bare.json", "w"))
PY
$D sync /tmp/bare.json --target / $L; echo "SRC-BARESYNC-RC=$?"
python -c 'import json;print("SRC-BARE-CAPTURED:",json.dumps(json.load(open("/tmp/bare.json")).get("package_sources")))'

echo "SRC-E: generations"
$D generations --target / $L

echo "SRC-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
