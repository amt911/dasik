#!/bin/bash
# A declared pacman group must converge, capture and remove like anything else.
#
# `plan()` reads `pacman -Qq`, which lists packages and never groups, so a
# config declaring `fprint` used to plan `+ install fprint` on every run, and
# the first `sync` rewrote the declaration into its two members. This drives
# the whole matrix against the LIVE host (target /), and needs no network: the
# group was installed during the install, and every step below only reads.
#
# Ends with GROUP-DONE, then powers off.
set -x
cd /root/repo || { echo "GROUP-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-pacman-group.json
W=/root/group-work
mkdir -p "$W"
echo "GROUP: BEGIN"

echo "GROUP-A: the members are installed, the group name is not a package"
pacman -Qq fprintd libfprint
pacman -Qq fprint; echo "GROUP-NAME-IS-NOT-A-PACKAGE-RC=$?"
pacman -Sgq fprint

echo "GROUP-B: the plan right after the install must be SILENT"
$D plan "$C" --target / $L; echo "GROUP-PLAN-RC=$?"
$D plan "$C" --target / $L | grep -c "fprint"; echo "GROUP-PLAN-MENTIONS=$?"

echo "GROUP-C: apply then plan again — plan/apply/plan must end in silence"
$D apply "$C" --target / --yes $L; echo "GROUP-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "GROUP-REPLAN-RC=$?"

# `sync` has no --output: it writes back THROUGH the config it is given, so it
# runs against a copy and the tracked sample is left alone.
echo "GROUP-D: sync must keep the GROUP, not explode it into its members"
cp "$C" "$W/captured.json"
$D sync "$W/captured.json" --target / $L; echo "GROUP-SYNC-RC=$?"
python - <<'EOF'
import json
pkgs = json.load(open('/root/group-work/captured.json'))['packages']
names = [p if isinstance(p, str) else p['name'] for p in pkgs]
print('GROUP-CAPTURED-HAS-GROUP=%s' % ('fprint' in names))
print('GROUP-CAPTURED-HAS-MEMBERS=%s' % any(n in ('fprintd', 'libfprint') for n in names))
EOF

echo "GROUP-E: the capture must validate and re-plan to nothing"
$D check "$W/captured.json"; echo "GROUP-CHECK-RC=$?"
$D plan "$W/captured.json" --target / $L; echo "GROUP-SYNCPLAN-RC=$?"

# Before anything destructive: the migration every real config makes. A capture
# lists the members; the admin replaces them with the group they came from. The
# manifest still owns the member names, so without the covered-member rule one
# apply installs the group and then deletes packages out of it — `apply`
# installs before it removes, so the machine ends up missing them.
echo "GROUP-H: members -> group must remove NOTHING"
python - <<'EOF'
import json
cfg = json.load(open('config/vm-pacman-group.json'))
cfg['packages'] = [p for p in cfg['packages'] if p != 'fprint'] + ['fprintd', 'libfprint']
json.dump(cfg, open('/root/group-work/members.json', 'w'), indent=2)
EOF
$D apply "$W/members.json" --target / --yes $L; echo "GROUP-MEMBERS-APPLY-RC=$?"
$D plan "$W/members.json" --target / $L; echo "GROUP-MEMBERS-PLAN-RC=$?"
echo "GROUP-H2: now switch back to the group — nothing may be removed"
$D plan "$C" --target / $L | grep -E "remove (fprintd|libfprint)"; echo "GROUP-MIGRATION-REMOVES-RC=$?"
$D apply "$C" --target / --yes $L; echo "GROUP-MIGRATION-APPLY-RC=$?"
pacman -Qq fprintd libfprint; echo "GROUP-MEMBERS-STILL-THERE-RC=$?"
$D plan "$C" --target / $L; echo "GROUP-MIGRATION-REPLAN-RC=$?"

echo "GROUP-F: break the group — one member gone means the group is PLANNED"
pacman -Rdd --noconfirm libfprint
pacman -Qq libfprint; echo "GROUP-MEMBER-GONE-RC=$?"
$D plan "$C" --target / $L | grep -E "install fprint$"; echo "GROUP-INCOMPLETE-RC=$?"

echo "GROUP-G: drop the group from the config — the MEMBERS are what leaves"
python - <<'EOF'
import json
cfg = json.load(open('config/vm-pacman-group.json'))
cfg['packages'] = [p for p in cfg['packages'] if p != 'fprint']
json.dump(cfg, open('/root/group-work/nogroup.json', 'w'), indent=2)
EOF
$D plan "$W/nogroup.json" --target / $L | grep -E "remove fprintd"; echo "GROUP-REMOVE-MEMBER-RC=$?"
$D plan "$W/nogroup.json" --target / $L | grep -E "remove fprint$"; echo "GROUP-REMOVE-GROUPNAME-RC=$?"

echo "GROUP-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
