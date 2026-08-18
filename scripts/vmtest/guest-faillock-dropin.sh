#!/bin/bash
# faillock polkit-sandbox drop-in regression, run INSIDE the booted guest via
# `qemu.sh drive <image> guest-faillock-dropin.sh FAILLOCK-DONE` after
# `qemu.sh install-driven config/vm-day2.json`.
#
# Declaring pam.faillock must contribute the polkit-agent-helper drop-in
# (ReadWritePaths for the tally dir) through the files domain: applied, then
# silent, and REMOVED when the pam block is dropped (the empty-config trap).
# polkit itself is not needed — the files-domain mechanics are what dasik owns.
#
# QEMU-only. Never run on a real host.
set -u

cd /root || { echo "FAILLOCK-DONE rc=91"; poweroff -f; }
export PYTHONPATH=/root/repo

BASE=/root/repo/config/vm-day2.json
WITH_PAM=/root/vm-faillock.json
DROPIN=/etc/systemd/system/polkit-agent-helper@.service.d/10-dasik-faillock.conf
rc=0

fail() {
    echo "BAD: $*"
    rc=1
}

python - "$BASE" "$WITH_PAM" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
data["pam"] = {"faillock": {"deny": 5, "persistent": True}}
Path(sys.argv[2]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
[ "$?" -eq 0 ] || fail "could not derive the pam config"

echo "FAILLOCK: apply must place the drop-in"
python -m dasik apply "$WITH_PAM" --target / --yes --no-log
[ "$?" -eq 0 ] || fail "apply with pam block failed"
[ -e "$DROPIN" ] || fail "drop-in missing after apply"
grep -q 'ReadWritePaths=/var/lib/faillock' "$DROPIN" \
    || fail "drop-in lacks the persistent tally dir"

echo "FAILLOCK: re-apply must be silent"
second="$(python -m dasik apply "$WITH_PAM" --target / --yes --no-log 2>&1)"
printf '%s\n' "$second"
printf '%s\n' "$second" | grep -F "No changes" >/dev/null \
    || fail "re-apply was not a no-op"

echo "FAILLOCK: dropping the pam block must REMOVE the drop-in"
python -m dasik apply "$BASE" --target / --yes --no-log
[ "$?" -eq 0 ] || fail "apply without pam block failed"
if [ -e "$DROPIN" ]; then
    fail "drop-in survived the block removal"
fi

echo "FAILLOCK-DONE rc=$rc"
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
