#!/bin/bash
# `uv_tools` with a pinned interpreter, through all six verbs, in the guest.
#
# The pin exists because uv's default interpreter is the wrong one for at least
# one real tool (inkscape_mcp -> inkex -> lxml 5.4.0, no cp314 wheel). What only
# a machine can answer: that `uv tool install --python` really builds the venv
# on that interpreter, that a venv rebuilt on the default is SEEN as drift, and
# that the capture carries the pin so re-applying it does not move the tool back.
# Ends with UVP-DONE, then powers off.
set -x
cd /root/repo || { echo "UVP-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-uv-python.json
V=/home/test/.local/share/uv/tools/pycowsay/pyvenv.cfg
echo "UVP: BEGIN (target / = the live booted host)"

echo "UVP-A: check"
$D check "$C" $L; echo "UVP-CHECK-RC=$?"

echo "UVP-B: plan — the tool is missing, so it must be planned"
$D plan "$C" --target / $L; echo "UVP-PLAN-RC=$?"

echo "UVP-C: apply, then read the interpreter uv actually used"
$D apply "$C" --target / --yes $L; echo "UVP-APPLY-RC=$?"
grep -a version_info "$V" || echo "UVP-VENV: MISSING"
# uv warns that ~/.local/bin is not on a login shell's PATH — which is exactly
# why this domain reads uv's directory and not a command. Run it by path.
su - test -c '~/.local/bin/pycowsay hola' >/dev/null 2>&1; echo "UVP-RUNS-RC=$?"

echo "UVP-D: plan again (expect silence)"
$D plan "$C" --target / $L; echo "UVP-REPLAN-RC=$?"

echo "UVP-E: drift — rebuild the SAME tool on the WRONG interpreter"
# `--force` alone reuses the tool's existing python, so it produces no drift at
# all (measured: the venv stayed on 3.13). The interpreter has to be named for
# the environment to actually move, which is the state the pin exists to catch.
su - test -c 'uv tool install --force --python 3.14 pycowsay'
grep -a version_info "$V"
$D plan "$C" --target / $L; echo "UVP-DRIFT-RC=$?"
$D apply "$C" --target / --yes $L; echo "UVP-FIX-RC=$?"
grep -a version_info "$V"
$D plan "$C" --target / $L; echo "UVP-AFTERFIX-RC=$?"

echo "UVP-F: generations and rollback"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "UVP-ROLLBACK-RC=$?"
$D plan "$C" --target / $L; echo "UVP-POSTROLLBACK-RC=$?"

echo "UVP-G: sync — the pin must come back, or re-applying moves the tool"
cp "$C" /root/captured.json
$D sync /root/captured.json --target / $L; echo "UVP-SYNC-RC=$?"
python - <<'PY'
import json
print("UVP-CAPTURED:", json.dumps(json.load(open("/root/captured.json")).get("uv_tools")))
PY
$D check /root/captured.json $L; echo "UVP-CAPCHECK-RC=$?"
$D plan /root/captured.json --target / $L; echo "UVP-CAPPLAN-RC=$?"

echo "UVP-H: the block emptied — an owned tool must be REMOVED"
python - <<'PY'
import json
cfg = json.load(open("/root/captured.json"))
cfg["uv_tools"] = {"tools": []}
json.dump(cfg, open("/root/dropped.json", "w"), indent=2)
PY
$D plan /root/dropped.json --target / $L; echo "UVP-DROP-RC=$?"

echo "UVP-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
