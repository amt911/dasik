#!/bin/bash
# Two WireGuard tunnels, one per backend, checked INSIDE the booted guest
# against the LIVE host.
#
# The two backends are deliberately different shapes: wg-quick needs the file
# AND its unit; NetworkManager needs only the keyfile, because its keyfile
# plugin reads the directory at startup — which is what lets an install-time
# apply configure it at all, with no daemon inside the chroot.
#
# Ends with WG-DONE, then powers off.
set -x
cd /root/repo || { echo "WG-DONE rc=91"; poweroff -f; }
rc=0

D="python -m dasik"
L="--no-log"                # the 9p repo is read-only; the log defaults to $PWD
WG=/etc/wireguard/vmwg.conf
NM=/etc/NetworkManager/system-connections/vmnm.nmconnection

echo "WG-A: both tunnels placed, each where its own backend reads it"
for f in "$WG" "$NM"; do
    if [ -f "$f" ]; then
        stat -c '%U:%G %a %n' "$f"
        [ "$(stat -c '%a' "$f")" = "600" ] || { echo "WG-MODE BAD $f"; rc=1; }
    else
        echo "WG-MISSING $f"; rc=1
    fi
done
grep -q 'PrivateKey' "$WG" || { echo "WG-BODY BAD wg-quick"; rc=1; }
grep -q 'type=wireguard' "$NM" || { echo "WG-BODY BAD nm"; rc=1; }

echo "WG-B: the wg-quick half — package and unit"
pacman -Q wireguard-tools || { echo "WG-PKG MISSING"; rc=1; }
systemctl is-enabled wg-quick@vmwg.service || { echo "WG-UNIT NOT-ENABLED"; rc=1; }

echo "WG-C: NetworkManager parses the keyfile dasik wrote"
# The assertion behind "dasik never translates": the file is NM's own format,
# so NM understands it without dasik having converted anything.
nmcli -t -f NAME,TYPE connection show || true
nmcli -t -f NAME,TYPE connection show | grep -q '^vmnm:wireguard$' \
    || { echo "WG-NM CONNECTION-MISSING"; rc=1; }

echo "WG-D: /etc/hosts carries the block the wiki recommends"
cat /etc/hosts
grep -q '127.0.1.1 dasik-wireguard' /etc/hosts || { echo "WG-HOSTS MISSING"; rc=1; }

echo "WG-E: a copy of the config, as a real user keeps it"
rm -rf /root/cfg && cp -r config/vm-wireguard /root/cfg
export PYTHONPATH=/root/repo
cd /root/cfg || { echo "WG-DONE rc=92"; poweroff -f; }
python -c 'import dasik' || { echo "WG-IMPORT BROKEN"; echo "WG-DONE rc=93"; poweroff -f; }

echo "WG-F: plan -> apply -> plan, both silent"
$D plan main.json --target / $L > /tmp/plan1.txt 2>&1 || { echo "WG-PLAN FAILED"; rc=1; }
cat /tmp/plan1.txt
grep -qE '^\s*[-+~] ' /tmp/plan1.txt && { echo "WG-PLAN NOT-SILENT"; rc=1; } \
    || echo "WG-PLAN silent"
$D apply main.json --target / --yes $L > /tmp/apply.txt 2>&1 || { echo "WG-APPLY FAILED"; rc=1; }
tail -5 /tmp/apply.txt
$D plan main.json --target / $L > /tmp/plan2.txt 2>&1
grep -qE '^\s*[-+~] ' /tmp/plan2.txt && { echo "WG-REPLAN NOT-SILENT"; rc=1; } \
    || echo "WG-REPLAN silent"

echo "WG-G: sync captures the tunnels as the block, with the files beside it"
$D sync main.json --target / $L > /tmp/sync.txt 2>&1 || { echo "WG-SYNC FAILED"; rc=1; }
tail -20 /tmp/sync.txt
ls -la wg/ && stat -c '%a %n' wg/*
python - <<'PY' || rc=1
import json, pathlib, sys
cfg = json.loads(pathlib.Path("main.json").read_text())
tunnels = {t["name"]: t for t in cfg.get("wireguard") or []}
print("captured:", json.dumps(cfg.get("wireguard"), indent=1))
ok = True
for name, suffix in (("vmwg", ".conf"), ("vmnm", ".nmconnection")):
    t = tunnels.get(name)
    if not t:
        print(f"WG-CAPTURE MISSING {name}"); ok = False; continue
    if "content" in t:
        print(f"WG-CAPTURE INLINED {name}"); ok = False
    if not pathlib.Path(t["source"]).is_file():
        print(f"WG-CAPTURE NO-FILE {name} -> {t['source']}"); ok = False
    if not t["source"].endswith(suffix):
        print(f"WG-CAPTURE WRONG-SUFFIX {name} -> {t['source']}"); ok = False
# The tunnel must NOT come back a second time as a raw files entry.
dupes = [f["path"] for f in cfg.get("files") or []
         if "/etc/wireguard/" in f["path"] or f["path"].endswith(".nmconnection")]
if dupes:
    print("WG-CAPTURE DOUBLE:", dupes); ok = False
sys.exit(0 if ok else 1)
PY
[ "$(stat -c '%a' wg/vmwg.conf)" = "600" ] || { echo "WG-CAPTURED-MODE BAD"; rc=1; }

echo "WG-H: sync -> check -> plan, silent"
$D check main.json $L || { echo "WG-CAPTURE REJECTED-BY-CHECK"; rc=1; }
$D plan main.json --target / $L > /tmp/plan3.txt 2>&1
cat /tmp/plan3.txt
grep -qE '^\s*[-+~] ' /tmp/plan3.txt && { echo "WG-PLAN-AFTER-SYNC NOT-SILENT"; rc=1; } \
    || echo "WG-PLAN-AFTER-SYNC silent"

echo "WG-I: generations and rollback"
$D generations --target / $L | tail -5
$D rollback 1 --target / --yes $L > /tmp/rollback.txt 2>&1 || { echo "WG-ROLLBACK FAILED"; rc=1; }
tail -5 /tmp/rollback.txt
$D plan main.json --target / $L > /tmp/plan4.txt 2>&1
cat /tmp/plan4.txt

echo "WG-J: the block removed — a REMOVAL, never a modify"
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg.pop("wireguard", None)
pathlib.Path("/root/no-wg.json").write_text(json.dumps(cfg, indent=2))
PY
$D plan /root/no-wg.json --target / $L > /tmp/plan5.txt 2>&1
cat /tmp/plan5.txt
grep -qE '^\s*- .*(vmwg|vmnm)' /tmp/plan5.txt \
    || { echo "WG-DROP NO-REMOVAL"; rc=1; }
grep -qE '^\s*~ .*(vmwg|vmnm)' /tmp/plan5.txt \
    && { echo "WG-DROP PLANNED-MODIFY"; rc=1; }

echo "WG-DONE rc=$rc"
sync
poweroff -f
