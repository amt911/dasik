#!/bin/bash
# `containers` block, checked INSIDE the booted guest against the LIVE host.
#
# What only a real machine can answer: that the subuid/subgid ranges are there
# for the declared user, that podman actually runs rootless with them, that
# podman-docker really put a `docker` on PATH, and that the socket unit is
# enabled. Then the six verbs, as the two round trips the repo demands:
# plan -> apply -> plan silent, and sync -> check -> plan silent.
#
# No image is pulled: an installed guest has no working network here, and the
# question "does the id map work" is answered by `podman unshare`, which needs
# none. Emits CONT-* markers; ends with CONT-DONE then powers off.
set -x
cd /root/repo || { echo "CONT-DONE rc=91"; poweroff -f; }

D="python -m dasik"
# The repo is 9p-mounted READ-ONLY, and the run log defaults to $PWD.
L="--no-log"
C=config/vm-containers.json
echo "CONT: BEGIN (target / = the live booted host)"

echo "CONT-A: the id maps the block owns"
grep '^test:' /etc/subuid && echo "CONT-SUBUID: present" || echo "CONT-SUBUID: MISSING"
grep '^test:' /etc/subgid && echo "CONT-SUBGID: present" || echo "CONT-SUBGID: MISSING"

echo "CONT-B: rootless podman uses them"
su - test -c 'podman unshare cat /proc/self/uid_map' && echo "CONT-UNSHARE: ok" \
    || echo "CONT-UNSHARE: FAILED"

echo "CONT-C: podman-docker and the socket"
command -v docker && docker --version 2>&1 | head -1
systemctl is-enabled podman.socket; echo "CONT-SOCKET-RC=$?"

echo "CONT-D: check"
$D check "$C" $L; echo "CONT-CHECK-RC=$?"

echo "CONT-E: plan (expect: No changes — the install already converged)"
$D plan "$C" --target / $L
echo "CONT-PLAN-RC=$?"

echo "CONT-F: apply, then plan again (expect both silent)"
$D apply "$C" --target / --yes $L; echo "CONT-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "CONT-REPLAN-RC=$?"

echo "CONT-I: generations and rollback (BEFORE sync — see the comment below)"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "CONT-ROLLBACK-RC=$?"
$D plan "$C" --target / $L; echo "CONT-POSTROLLBACK-RC=$?"

echo "CONT-G: sync, and what it captured"
# `sync` REWRITES the config it is given and the repo is mounted read-only, so
# it works on a copy.
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "CONT-SYNC-RC=$?"
python -c 'import json;print("CONT-CAPTURED:",json.dumps(json.load(open("/tmp/captured.json")).get("containers")))'

echo "CONT-H: the capture validates, and re-plans to nothing"
$D check /tmp/captured.json $L; echo "CONT-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L; echo "CONT-CAPPLAN-RC=$?"

echo "CONT-J: the block REMOVED — an owned id map must be proposed for removal"
python - <<'PY'
import json
cfg = json.load(open("config/vm-containers.json"))
cfg.pop("containers", None)
json.dump(cfg, open("/tmp/no-containers.json", "w"), indent=2)
PY
$D plan /tmp/no-containers.json --target / $L
echo "CONT-DROPPED-RC=$?"

echo "CONT-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
