#!/bin/bash
# The case `_process_disk` could never reach: enrolling a hardware token on a
# machine that is ALREADY INSTALLED.
#
# The guest was installed with a LUKS passphrase and no token at all. Here the
# config gains `unlock_tpm2: true` and dasik has to notice, enrol, converge —
# and then, with the flag dropped again, wipe the keyslot it owns. The header
# itself is the witness: `cryptsetup luksDump` before and after.
#
# Ends with TOKEN-DONE, then powers off.
set -x
cd /root/repo || { echo "TOKEN-DONE rc=91"; poweroff -f; }
rc=0

D="python -m dasik"
L="--no-log"                 # the 9p repo is read-only
DEV=$(cryptsetup status cryptroot | awk '/device:/ {print $2}')

echo "TOKEN-A: the machine boots with a passphrase and NO token"
cryptsetup luksDump "$DEV" | sed -n '/Keyslots:/,$p' | head -30
cryptsetup luksDump "$DEV" | grep -q 'systemd-tpm2' && { echo "TOKEN-PRECONDITION BAD (already enrolled)"; rc=1; }
# The TPM the harness attaches must actually be there, or this proves nothing.
[ -c /dev/tpmrm0 ] || { echo "TOKEN-NO-TPM"; echo "TOKEN-DONE rc=92"; poweroff -f; }

echo "TOKEN-B: a writable copy of the config, with the flag turned ON"
rm -rf /root/cfg && cp -r config/vm-luks-token /root/cfg
export PYTHONPATH=/root/repo
cd /root/cfg || { echo "TOKEN-DONE rc=93"; poweroff -f; }
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
part = cfg["disks"]["disks"][0]["partitions"][1]
part["unlock_tpm2"] = True
part["format"] = False          # never reformat an installed machine
cfg["disks"]["disks"][0]["wipe_disk"] = False
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY

echo "TOKEN-C: plan must PROPOSE the enrolment (the old code was silent here)"
$D plan main.json --target / $L > /tmp/plan1.txt 2>&1
cat /tmp/plan1.txt
grep -qE '\+ \[luks_token\] .*cryptroot:tpm2' /tmp/plan1.txt \
    || { echo "TOKEN-PLAN NOT-PROPOSED"; rc=1; }

echo "TOKEN-D: apply enrols it into the header"
$D apply main.json --target / --yes $L > /tmp/apply1.txt 2>&1 || { echo "TOKEN-APPLY FAILED"; rc=1; }
tail -15 /tmp/apply1.txt
cryptsetup luksDump "$DEV" | sed -n '/Tokens:/,/Digests:/p'
cryptsetup luksDump "$DEV" | grep -q 'systemd-tpm2' \
    || { echo "TOKEN-NOT-ENROLLED"; rc=1; }

echo "TOKEN-E: and then plan is silent (idempotent)"
$D plan main.json --target / $L > /tmp/plan2.txt 2>&1
cat /tmp/plan2.txt
grep -qE '^\s*[-+~] \[luks_token\]' /tmp/plan2.txt && { echo "TOKEN-REPLAN NOT-SILENT"; rc=1; } \
    || echo "TOKEN-REPLAN silent"

echo "TOKEN-F: sync captures the flag from the header"
$D sync main.json --target / $L > /tmp/sync.txt 2>&1 || { echo "TOKEN-SYNC FAILED"; rc=1; }
grep -n 'unlock_tpm2' main.json || { echo "TOKEN-SYNC DID-NOT-CAPTURE"; rc=1; }
$D check main.json $L || { echo "TOKEN-CAPTURE REJECTED-BY-CHECK"; rc=1; }

echo "TOKEN-G: drop the flag — the keyslot dasik owns is wiped"
# sync dropped luks_password (a secret is never captured), so put it back: the
# wipe does not need it, but the config has to stay valid and applyable.
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
for disk in cfg["disks"]["disks"]:
    for part in disk["partitions"]:
        part.pop("unlock_tpm2", None)
        if part.get("encrypt"):
            part["luks_password"] = "tpmpass"
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY
$D plan main.json --target / $L > /tmp/plan3.txt 2>&1
cat /tmp/plan3.txt
grep -qE '\- \[luks_token\] .*cryptroot:tpm2' /tmp/plan3.txt \
    || { echo "TOKEN-DROP NOT-PLANNED"; rc=1; }
$D apply main.json --target / --yes $L > /tmp/apply2.txt 2>&1 || { echo "TOKEN-WIPE FAILED"; rc=1; }
cryptsetup luksDump "$DEV" | sed -n '/Tokens:/,/Digests:/p'
cryptsetup luksDump "$DEV" | grep -q 'systemd-tpm2' && { echo "TOKEN-STILL-ENROLLED"; rc=1; }

echo "TOKEN-H: the passphrase still opens the volume — nothing was lost"
echo -n tpmpass | cryptsetup open --test-passphrase "$DEV" - \
    && echo "TOKEN-PASSPHRASE ok" || { echo "TOKEN-PASSPHRASE BROKEN"; rc=1; }

$D plan main.json --target / $L > /tmp/plan4.txt 2>&1
grep -qE '^\s*[-+~] \[luks_token\]' /tmp/plan4.txt && { echo "TOKEN-FINAL NOT-SILENT"; rc=1; } \
    || echo "TOKEN-FINAL silent"

echo "TOKEN-DONE rc=$rc"
sync
poweroff -f
