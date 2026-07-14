#!/bin/bash
# Generation lifecycle check, run INSIDE the booted (already-installed) guest.
#
# Proves dasik's NixOS-like generation management against the LIVE host (target /,
# not /mnt): every apply records a numbered generation, `generations` lists them,
# `rollback` restores a prior generation's config AND re-converges the system to it
# (here: an owned file added by the modified config is REMOVED on rollback), and
# `sync` captures system reality back into a config non-destructively (with a .bak).
#
# The installed image comes from vm-day2.json, which autologins root on ttyS0 and
# ships python-pydantic/colorama, so dasik runs straight from the 9p-mounted repo.
# Emits LIFE-* markers the host driver greps; ends with LIFE-DONE rc=<n> (n>0 if any
# assertion below failed) then powers off.
set -x
cd /root/repo || { echo "LIFE-DONE rc=91"; poweroff -f; }

D="python -m dasik"
MARKER=/etc/dasik-day2-marker.conf
fails=0
check() { # check <label> <condition-rc>
    if [ "$2" -eq 0 ]; then echo "LIFE-OK: $1"; else echo "LIFE-BAD: $1"; fails=$((fails + 1)); fi
}
# True (rc 0) iff package $1 is in the packages array of config $2.
pkg_in() { python -c "import json,sys; sys.exit(0 if '$1' in json.load(open('$2')).get('packages',[]) else 1)"; }

echo "LIFE: BEGIN (target / = the live booted host)"

echo "LIFE-A: initial generations (expect gen 1 from the install-time apply)"
$D generations
$D generations | grep -q "Generation 1 (current)"; check "install recorded generation 1, current" $?

echo "LIFE-B: apply the MODIFIED config (adds one owned file) -> records a new generation"
$D apply config/vm-day2-mod.json --target / --yes
[ -f "$MARKER" ]; check "modified apply created the owned marker file" $?
$D generations | grep -q "Generation 2 (current)"; check "apply recorded generation 2, current" $?

echo "LIFE-C: rollback (no N -> previous generation) -> re-converge to the prior config"
$D rollback --target / --yes
# The owned marker file, present in the rolled-from generation but not the target
# one, must be DELETED by the rollback's re-apply (ownership-based set-math).
[ ! -f "$MARKER" ]; check "rollback removed the marker (real re-convergence, not just config swap)" $?
$D generations | grep -q "Generation 3 (current)"; check "rollback recorded a new generation 3, current" $?

echo "LIFE-D: sync system reality into an UNDER-declared config (non-destructive, .bak)"
# Start from a config declaring only 'base' so sync has real drift to capture: the
# installed 'linux' package (explicit, not declared here) must be pulled back in.
python - <<'PY'
import json
c = json.load(open("config/vm-day2.json"))
c["packages"] = ["base"]
json.dump(c, open("/root/reduced.json", "w"), indent=2)
PY
if pkg_in linux /root/reduced.json; then check "precondition: 'linux' absent from the under-declared config" 1; else check "precondition: 'linux' absent from the under-declared config" 0; fi
$D sync /root/reduced.json --target /
[ -f /root/reduced.json.bak ]; check "sync wrote a .bak backup" $?
pkg_in linux /root/reduced.json; check "sync captured the installed 'linux' package into the config" $?

echo "LIFE-DONE rc=$fails"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
