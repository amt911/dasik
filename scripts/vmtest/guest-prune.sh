#!/bin/bash
# Generations pile up for ever unless somebody prunes them — and a prune must
# never take the one the machine is running, nor leave a history that cannot be
# rolled back to.
#
# Ends with PRUNE-DONE, then powers off.
set -x
cd /root/repo || { echo "PRUNE-DONE rc=91"; poweroff -f; }
rc=0
D="python -m dasik"
L="--no-log"

rm -rf /root/cfg && cp -r config/vm-default-hosts.json /root/cfg.json
export PYTHONPATH=/root/repo
cd /root || { echo "PRUNE-DONE rc=92"; poweroff -f; }

echo "PRUNE-A: pile up generations with real applies"
for i in 1 2 3 4; do
    python - "$i" <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path("/root/cfg.json").read_text())
# Something harmless that differs per apply, so each one is a real generation.
cfg.setdefault("etc_environment", [])
cfg["etc_environment"] = [f"DASIK_PRUNE_ROUND={sys.argv[1]}"]
pathlib.Path("/root/cfg.json").write_text(json.dumps(cfg, indent=2))
PY
    $D apply /root/cfg.json --target / --yes $L > /tmp/apply$i.txt 2>&1 \
        || { echo "PRUNE-APPLY$i FAILED"; tail -5 /tmp/apply$i.txt; rc=1; }
done
$D generations --target / $L | tee /tmp/gens1.txt
before=$(grep -c '^Generation' /tmp/gens1.txt)
[ "$before" -ge 4 ] || { echo "PRUNE-SETUP too few generations ($before)"; rc=1; }
ls -1 /var/lib/dasik/generations | sort -n

echo "PRUNE-B: prune down to 2"
$D generations --target / --prune 2 $L | tee /tmp/prune.txt
grep -q '^Pruned ' /tmp/prune.txt || { echo "PRUNE-NOTHING-REPORTED"; rc=1; }
after=$(grep -c '^Generation' /tmp/prune.txt)
[ "$after" -le 3 ] || { echo "PRUNE-TOO-MANY-LEFT ($after)"; rc=1; }
ls -1 /var/lib/dasik/generations | sort -n

echo "PRUNE-C: the current generation survived, and is still the current one"
$D generations --target / $L | tee /tmp/gens2.txt
grep -q 'current' /tmp/gens2.txt || { echo "PRUNE-LOST-CURRENT"; rc=1; }
readlink /var/lib/dasik/generations/current

echo "PRUNE-D: pruning again is a no-op"
$D generations --target / --prune 2 $L | tee /tmp/prune2.txt
grep -q 'Nothing to prune' /tmp/prune2.txt || { echo "PRUNE-NOT-IDEMPOTENT"; rc=1; }

echo "PRUNE-E: a survivor is still rollback-able, and the plan after is silent"
oldest=$(ls -1 /var/lib/dasik/generations | grep -E '^[0-9]+$' | sort -n | head -1)
$D rollback "$oldest" --target / --yes $L > /tmp/rollback.txt 2>&1 \
    || { echo "PRUNE-ROLLBACK FAILED"; tail -5 /tmp/rollback.txt; rc=1; }
tail -3 /tmp/rollback.txt

echo "PRUNE-F: --prune 0 is refused"
$D generations --target / --prune 0 $L > /tmp/prune0.txt 2>&1
grep -q 'keep must be at least 1' /tmp/prune0.txt || { echo "PRUNE-ZERO-NOT-REFUSED"; rc=1; }
cat /tmp/prune0.txt

echo "PRUNE-DONE rc=$rc"
sync
poweroff -f
