#!/bin/bash
# A file a BLOCK derives must stay owned across a sync (issue #197).
#
# `reflector` derives /etc/xdg/reflector/reflector.conf. Before the fix, one
# sync was enough to disown it: dropping the block afterwards planned nothing
# and the file stayed on the machine forever. Ends with OWN-DONE, then powers off.
set -x
cd /root/repo || { echo "OWN-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-minimal.json
echo "OWN: BEGIN"

echo "OWN-A: the derived file is there"
cat /etc/xdg/reflector/reflector.conf
systemctl is-enabled reflector.timer; echo "OWN-TIMER-RC=$?"

echo "OWN-B: BEFORE any sync — dropping the block must plan its deletion"
python - <<'PY'
import json
cfg = json.load(open("config/vm-minimal.json"))
cfg.pop("reflector", None)
cfg["packages"] = [p for p in cfg["packages"] if p != "reflector"]
json.dump(cfg, open("/tmp/no-reflector.json", "w"), indent=2)
PY
$D plan /tmp/no-reflector.json --target / $L | grep -E "reflector" ; echo "OWN-BEFORE-RC=$?"

echo "OWN-C: sync"
cp "$C" /tmp/captured.json
$D sync /tmp/captured.json --target / $L; echo "OWN-SYNC-RC=$?"
python -c 'import json;c=json.load(open("/tmp/captured.json"));print("OWN-CAPTURED-REFLECTOR:",json.dumps(c.get("reflector")));print("OWN-CAPTURED-FILES:",[f["path"] for f in c.get("files",[])])'

echo "OWN-D: AFTER the sync — the same plan must STILL propose the deletion"
$D plan /tmp/no-reflector.json --target / $L | grep -E "reflector"; echo "OWN-AFTER-RC=$?"

echo "OWN-E: and the capture itself still converges"
$D check /tmp/captured.json $L; echo "OWN-CAPCHECK-RC=$?"
$D plan /tmp/captured.json --target / $L; echo "OWN-CAPPLAN-RC=$?"

echo "OWN-F: rollback after the sync — it must NOT offer to dismantle the machine"
$D generations --target / $L
$D rollback 1 --target / --yes $L; echo "OWN-ROLLBACK-RC=$?"
grep options /boot/loader/entries/arch.conf
pacman -Q mkinitcpio && echo "OWN-MKINITCPIO: still here" || echo "OWN-MKINITCPIO: GONE"
systemctl is-enabled getty@tty1.service; echo "OWN-GETTY-RC=$?"

echo "OWN-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
