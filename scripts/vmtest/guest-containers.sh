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

echo "CONT-C2: the registry drop-in, and what PODMAN ITSELF makes of it"
# The contract test: Arch installs no /etc/containers/registries.conf at all
# (containers-common ships only the sample under /usr/share), so the question
# is whether podman honours the drop-in ALONE. Nothing but a real podman can
# answer it — the unit suite mocks the file away.
cat /etc/containers/registries.conf.d/10-unqualified-search-registries.conf
podman info --format '{{ .Registries }}'; echo "CONT-REGINFO-RC=$?"
podman info --format '{{ .Registries }}' | grep -q 'docker.io' \
    && echo "CONT-REGISTRY: podman searches docker.io" \
    || echo "CONT-REGISTRY: NOT SEARCHED"
# And the failure this domain exists to remove, asked in BOTH directions.
#
# The name is deliberately one that cannot exist and is not in the aliases
# containers-common ships (/usr/share/containers/registries.conf.d/
# 00-shortnames.conf). Both matter:
#   * an ALIASED name resolves with no drop-in at all, so it proves nothing;
#   * a name already in LOCAL STORAGE resolves without consulting any registry
#     config — which is exactly how the first version of this check fooled
#     itself, because the pull above had just put the image there.
# Neither can happen with a name nothing has and nothing aliases: the pull
# always fails, and the assertion is WHICH failure comes back.
NOPE=dasik-no-such-image-xyz:1

echo "CONT-C2a: with the drop-in, the short name must get PAST resolution"
podman pull "$NOPE" 2>&1 | tail -1
podman pull "$NOPE" 2>&1 | grep -qi 'short-name.*did not resolve' \
    && echo "CONT-SHORTNAME: STILL UNRESOLVED" \
    || echo "CONT-SHORTNAME: resolves"

echo "CONT-C2b: and with the config taken away it must NOT — else this is no test"
# Both overrides: CONTAINERS_REGISTRIES_CONF replaces the FILE only, the drop-in
# DIRECTORY is read regardless until CONTAINERS_REGISTRIES_CONF_DIR moves it.
: > /tmp/empty-registries.conf; mkdir -p /tmp/empty-registries.d
CONTAINERS_REGISTRIES_CONF=/tmp/empty-registries.conf \
CONTAINERS_REGISTRIES_CONF_DIR=/tmp/empty-registries.d \
    podman pull "$NOPE" 2>&1 | tail -1
CONTAINERS_REGISTRIES_CONF=/tmp/empty-registries.conf \
CONTAINERS_REGISTRIES_CONF_DIR=/tmp/empty-registries.d \
    podman pull "$NOPE" 2>&1 | grep -qi 'short-name.*did not resolve' \
    && echo "CONT-SHORTNAME-NEGATIVE: unresolved without the drop-in, as it must be" \
    || echo "CONT-SHORTNAME-NEGATIVE: VACUOUS CHECK — resolves with no config at all"

echo "CONT-C2c: and the real thing the user hit — a REAL short name pulls"
# Last, because a successful pull puts the image in local storage and would
# make the two checks above answer from there instead of from the config.
podman pull postgres:17.5 2>&1 | tail -1; echo "CONT-REALPULL-RC=$?"

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
python -c 'import json,sys;c=json.load(open("/tmp/captured.json")).get("containers") or {};sys.exit(0 if c.get("search_registries")==["docker.io"] else 1)' \
    && echo "CONT-CAPREG: captured" || echo "CONT-CAPREG: LOST"

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
$D plan /tmp/no-containers.json --target / $L | grep -q 'docker.io' \
    && echo "CONT-DROPPED-REGISTRY: removal planned" \
    || echo "CONT-DROPPED-REGISTRY: NOT PLANNED"

echo "CONT-K: and applying that removal takes the drop-in away, not empties it"
# `unqualified-search-registries = []` would mean "search nothing", which is
# the broken state the domain exists to fix — so the file must be GONE.
$D apply /tmp/no-containers.json --target / --yes $L; echo "CONT-DROPAPPLY-RC=$?"
test -e /etc/containers/registries.conf.d/10-unqualified-search-registries.conf \
    && echo "CONT-DROPFILE: STILL THERE" || echo "CONT-DROPFILE: gone"
# Put the machine back the way the config says, so the run ends converged.
$D apply "$C" --target / --yes $L; echo "CONT-REAPPLY-RC=$?"
podman info --format '{{ .Registries }}'

echo "CONT-L: the search ORDER is policy, and set-math cannot see a reorder"
# Two registries first, in one order...
python - <<'PY'
import json
cfg = json.load(open("config/vm-containers.json"))
cfg["containers"]["search_registries"] = ["docker.io", "quay.io"]
json.dump(cfg, open("/tmp/order-a.json", "w"), indent=2)
cfg["containers"]["search_registries"] = ["quay.io", "docker.io"]
json.dump(cfg, open("/tmp/order-b.json", "w"), indent=2)
PY
$D apply /tmp/order-a.json --target / --yes $L; echo "CONT-ORDERA-RC=$?"
podman info --format '{{ .Registries }}'
# ...then the SAME SET in the other order. D/M/A calls those equal, so without
# the ordering check the plan is silent and the config claims an order the
# machine does not have.
$D plan /tmp/order-b.json --target / $L | grep -q 'container_registries' \
    && echo "CONT-REORDER-PLANNED: yes" || echo "CONT-REORDER-PLANNED: NO — silent reorder"
$D apply /tmp/order-b.json --target / --yes $L; echo "CONT-REORDERAPPLY-RC=$?"
podman info --format '{{ .Registries }}'
podman info --format '{{ .Registries }}' | grep -q 'search:\[quay.io docker.io\]' \
    && echo "CONT-REORDER-APPLIED: podman searches quay.io first" \
    || echo "CONT-REORDER-APPLIED: ORDER NOT HONOURED"
$D plan /tmp/order-b.json --target / $L; echo "CONT-REORDER-REPLAN-RC=$?"
# Back to what the tracked config says, so the run ends converged.
$D apply "$C" --target / --yes $L; echo "CONT-RESTORE-RC=$?"
podman info --format '{{ .Registries }}'

echo "CONT-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
