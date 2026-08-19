#!/bin/bash
# Two FIDO2 keys declared, zero keys in the machine.
#
# The install that produced this guest ran with `luks_token_policy.
# enroll_failure: warn-and-continue`, so both enrolments failed loudly and the
# install FINISHED — which is the whole point: somebody who declared three keys
# and owns two must not end up with a half-partitioned disk and no system.
#
# What is asserted here is what has to be true AFTERWARDS, and it is the part a
# unit test cannot show: the machine booted, the header carries no token, and
# the skipped keyslots were never recorded as done — so `plan` still asks for
# both, and `sync` invents neither.
#
# Ends with FIDO2KEYS-DONE, then powers off.
set -x
cd /root/repo || { echo "FIDO2KEYS-DONE rc=91"; poweroff -f; }
rc=0

D="python -m dasik"
L="--no-log"                 # the 9p repo is read-only
DEV=$(cryptsetup status cryptroot | awk '/device:/ {print $2}')
export PYTHONPATH=/root/repo

echo "FIDO2KEYS-A: the machine BOOTED, which the old behaviour would not have"
uname -a
[ -n "$DEV" ] || { echo "FIDO2KEYS-NO-CRYPTROOT"; echo "FIDO2KEYS-DONE rc=92"; poweroff -f; }

echo "FIDO2KEYS-B: the header carries a passphrase and NO fido2 token"
cryptsetup luksDump "$DEV" | sed -n '/Keyslots:/,$p' | head -30
tokens=$(cryptsetup luksDump "$DEV" | grep -c 'systemd-fido2')
echo "FIDO2KEYS-TOKENS=$tokens"
[ "$tokens" -eq 0 ] || { echo "FIDO2KEYS-B FAILED: a token exists in a VM with no key"; rc=1; }

echo "FIDO2KEYS-C: a writable copy of the config, day-2 (no reformatting)"
rm -rf /root/cfg && mkdir -p /root/cfg && cp config/vm-fido2-keys.json /root/cfg/main.json
cd /root/cfg || { echo "FIDO2KEYS-DONE rc=93"; poweroff -f; }
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
disk = cfg["disks"]["disks"][0]
disk["wipe_disk"] = False
for part in disk["partitions"]:
    part["format"] = False
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY

echo "FIDO2KEYS-D: plan STILL asks for both keyslots (a skip records nothing)"
$D plan main.json --target / $L > /tmp/plan1.txt 2>&1
grep -a 'luks_token' /tmp/plan1.txt
grep -qa 'cryptroot:fido2$\|cryptroot:fido2 ' /tmp/plan1.txt || \
    { echo "FIDO2KEYS-D FAILED: the first keyslot is not in the plan"; rc=1; }
grep -qa 'cryptroot:fido2#2' /tmp/plan1.txt || \
    { echo "FIDO2KEYS-D FAILED: the second keyslot is not in the plan"; rc=1; }

echo "FIDO2KEYS-E: one key declared instead of two plans exactly one"
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg["disks"]["disks"][0]["partitions"][1]["unlock_fido2"] = True
pathlib.Path("one.json").write_text(json.dumps(cfg, indent=2))
PY
$D plan one.json --target / $L > /tmp/plan2.txt 2>&1
grep -a 'luks_token' /tmp/plan2.txt
n=$(grep -ac 'luks_token.*cryptroot:fido2' /tmp/plan2.txt)
echo "FIDO2KEYS-ONE-COUNT=$n"
[ "$n" -eq 1 ] || { echo "FIDO2KEYS-E FAILED: expected exactly one keyslot planned"; rc=1; }

echo "FIDO2KEYS-F: no keys declared plans nothing (nothing owned, nothing to wipe)"
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg["disks"]["disks"][0]["partitions"][1]["unlock_fido2"] = False
pathlib.Path("none.json").write_text(json.dumps(cfg, indent=2))
PY
$D plan none.json --target / $L > /tmp/plan3.txt 2>&1
grep -a 'luks_token' /tmp/plan3.txt
grep -qa 'luks_token' /tmp/plan3.txt && \
    { echo "FIDO2KEYS-F FAILED: a keyslot nobody has is planned"; rc=1; }

echo "FIDO2KEYS-G: sync invents no key on a machine that has none"
# `sync` rewrites the config file IN PLACE — there is no --output — so it is
# handed a copy and the copy is what gets inspected.
cp main.json /tmp/captured.json
$D sync /tmp/captured.json --target / $L > /tmp/sync.txt 2>&1
tail -5 /tmp/sync.txt
grep -a 'unlock_fido2' /tmp/captured.json
python - <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path("/tmp/captured.json").read_text())
bad = []
for disk in cfg.get("disks", {}).get("disks", []):
    for part in disk.get("partitions", []):
        value = part.get("unlock_fido2", False)
        if value:
            bad.append((part.get("label"), value))
print("FIDO2KEYS-CAPTURED-FIDO2=", bad)
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] || { echo "FIDO2KEYS-G FAILED: sync invented a FIDO2 key"; rc=1; }

echo "FIDO2KEYS-H: the captured config still validates"
$D check /tmp/captured.json > /tmp/check.txt 2>&1
cat /tmp/check.txt
grep -qa 'OK' /tmp/check.txt || { echo "FIDO2KEYS-H FAILED: sync produced a config check rejects"; rc=1; }

echo "FIDO2KEYS-DONE rc=$rc"
sync
sleep 2
poweroff -f
