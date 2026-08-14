#!/bin/bash
# `etc_tree` + a split config + config-saver, checked INSIDE the booted guest.
#
# The install already wrote every /etc file from the tree. What needs a real
# machine is the rest of the round trip:
#   - the files are there, with the modes the tree implies (0755 from the
#     executable bit, 0640 because etc_tree_modes said so);
#   - re-planning against the LIVE host is silent (day-2 idempotency);
#   - `sync` writes the capture back INTO the tree instead of inlining bodies
#     into the JSON, and the config it produces still validates and re-plans to
#     nothing.
#
# The 9p repo is read-only, so the config is copied to /root/cfg first — which
# is also what a real user does with a config that lives in a Git repository.
#
# Ends with TREE-DONE, then powers off.
set -x
cd /root/repo || { echo "TREE-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
echo "TREE: BEGIN (target / = the live booted host)"

echo "TREE-A: the files the tree declared"
rc=0
for f in /etc/pam.d/dasik-vm /etc/profile.d/dasik-vm.sh \
         /etc/udev/rules.d/99-dasik-vm.rules /etc/modprobe.d/dasik-vm.conf \
         /etc/sysctl.d/99-dasik-vm.conf; do
    if [ -f "$f" ]; then echo "TREE-FILE ok $f"; else echo "TREE-FILE MISSING $f"; rc=1; fi
done
grep -q DASIK_ETC_TREE /etc/profile.d/dasik-vm.sh || { echo "TREE-CONTENT BAD"; rc=1; }

echo "TREE-B: the modes"
# 0755 comes from the executable bit in Git; 0640 from etc_tree_modes.
stat -c '%a %n' /etc/profile.d/dasik-vm.sh /etc/pam.d/dasik-vm
[ "$(stat -c '%a' /etc/profile.d/dasik-vm.sh)" = "755" ] || { echo "TREE-MODE BAD exec"; rc=1; }
[ "$(stat -c '%a' /etc/pam.d/dasik-vm)" = "640" ] || { echo "TREE-MODE BAD declared"; rc=1; }

echo "TREE-C: config-saver came from its Git PKGBUILD"
pacman -Q config-saver && echo "TREE-PKG ok" || { echo "TREE-PKG MISSING"; rc=1; }

echo "TREE-D: a copy of the config, as a real user keeps it"
rm -rf /root/cfg && cp -r config/vm-etc-tree /root/cfg
# dasik runs from the copied repo, so it stays importable after the cd. Without
# this every command below fails with "No module named dasik" and the greps
# that follow report success — a green run that proved nothing.
export PYTHONPATH=/root/repo
cd /root/cfg || { echo "TREE-DONE rc=92"; poweroff -f; }
python -c 'import dasik' || { echo "TREE-IMPORT BROKEN"; echo "TREE-DONE rc=93"; poweroff -f; }

echo "TREE-E: re-plan against the live host must be silent"
if ! $D plan main.json --target / $L > /tmp/plan1.txt 2>&1; then
    echo "TREE-PLAN FAILED"; rc=1
fi
cat /tmp/plan1.txt
if grep -qE '^\s*[-+~] ' /tmp/plan1.txt; then echo "TREE-PLAN NOT-SILENT"; rc=1
else echo "TREE-PLAN silent"; fi

echo "TREE-F: sync writes back INTO the tree"
if ! $D sync main.json --target / $L > /tmp/sync.txt 2>&1; then
    echo "TREE-SYNC FAILED"; rc=1
fi
tail -20 /tmp/sync.txt
# No body belonging to the tree may have moved into the JSON. The config also
# declares one `files` entry inline on purpose (the serial autologin drop-in),
# so counting entries under the tree is the assertion — grepping for "content"
# would match that one and always "fail".
python - <<'PY' || rc=1
import json, pathlib, sys
raw = json.loads(pathlib.Path("main.json").read_text())
tree = {f"/etc/{p.relative_to('etc')}" for p in pathlib.Path("etc").rglob("*") if p.is_file()}
inlined = [e["path"] for e in raw.get("files", []) if e.get("path") in tree]
print("TREE-SYNC INLINED-BODIES" if inlined else "TREE-SYNC no inline bodies", inlined)
sys.exit(1 if inlined else 0)
PY
# and the tree must still hold real files
for f in etc/pam.d/dasik-vm etc/profile.d/dasik-vm.sh; do
    if [ -s "$f" ]; then echo "TREE-SYNC kept $f"; else echo "TREE-SYNC LOST $f"; rc=1; fi
done

echo "TREE-G: the capture still validates and re-plans to nothing"
if ! $D check main.json; then echo "TREE-CHECK REFUSED-ITS-OWN-CAPTURE"; rc=1; fi
if ! $D plan main.json --target / $L > /tmp/plan2.txt 2>&1; then
    echo "TREE-REPLAN FAILED"; rc=1
fi
cat /tmp/plan2.txt
if grep -qE '^\s*[-+~] ' /tmp/plan2.txt; then echo "TREE-REPLAN NOT-SILENT"; rc=1
else echo "TREE-REPLAN silent"; fi

echo "TREE-H: config-saver has something to run (no exit 6)"
ls -la /etc/config-saver/configs/ || true
systemctl is-enabled config-saver@test.timer; echo "TREE-TIMER-RC=$?"

echo "TREE-DONE rc=$rc"
sync
poweroff -f
