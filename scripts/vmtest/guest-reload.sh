#!/bin/bash
# Issue #300, day-2 against the LIVE booted guest (target /).
#
#   DASIK_VM_LUKS_PASSWORD=hibpass \
#   qemu.sh drive <image> guest-reload.sh RELOAD-DONE
#
# The claim: a systemd drop-in dasik writes reaches the daemon, not just the
# disk. Before the fix it did not — the file landed exactly as planned, the next
# plan was silent, and systemd carried on with its cached units.
#
# The observable is `systemctl show -p NeedDaemonReload`, the very flag systemd
# consults to print "drop-ins changed on disk. Run 'systemctl daemon-reload'".
# It answers exactly the question at issue: has the daemon taken this file in?
#
# The first attempt used `systemctl show -p Environment`, and Phase A caught it
# out — systemd reports the on-disk Environment of an inactive unit without any
# reload, so every later phase passed vacuously and would have passed against
# the unfixed code too.
#
# Phase A is the counterfactual and the reason this test is not vacuous: the same
# drop-in written BY HAND, with no reload, must NOT be visible. Without it, a
# passing Phase B could just mean systemd re-reads drop-ins on its own.
#
# systemd-timesyncd is the guinea pig: always present, harmless, and its
# Environment is trivially observable.
set -u

rc=0
D="python -m dasik"
L="--no-log"
fail() { echo "BAD: $*"; rc=1; }

UNIT=systemd-timesyncd.service
DROPIN_DIR=/etc/systemd/system/$UNIT.d
DROPIN=$DROPIN_DIR/10-dasik-probe.conf
PROBE=DASIK_RELOAD_PROBE=yes

needs_reload() { [ "$(systemctl show -p NeedDaemonReload --value "$UNIT")" = "yes" ]; }
sees_probe()   { systemctl show -p Environment "$UNIT" | grep -q "$PROBE"; }

# An active unit makes the Environment reading meaningful too; an inactive one is
# re-read from disk on every query, which is what fooled the first version.
systemctl start "$UNIT" 2>/dev/null || true

echo "RELOAD-A: counterfactual — the same drop-in by hand, no reload"
mkdir -p "$DROPIN_DIR"
printf '[Service]\nEnvironment=%s\n' "$PROBE" > "$DROPIN"
if needs_reload; then
    echo "  ok: systemd reports NeedDaemonReload=yes (so the observable is real)"
else
    fail "systemd does not flag a hand-written drop-in — this test cannot prove anything"
fi
rm -rf "$DROPIN_DIR"
systemctl daemon-reload

echo "RELOAD-B: now let dasik write the very same file"
export PYTHONPATH=/root/repo
rm -rf /root/cfg && mkdir -p /root/cfg && cd /root/cfg || {
    echo "RELOAD-DONE rc=93"; poweroff -f; }
# The config the machine was installed from, plus the probe. A minimal config
# would plan to uninstall base/linux/dracut — the reconciler hands every
# previously-owned domain its empty config.
cp /root/repo/config/vm-p14s-hibernate.json main.json
python - <<'PY'
import json
import pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg.setdefault("files", []).append({
    "path": "/etc/systemd/system/systemd-timesyncd.service.d/10-dasik-probe.conf",
    "content": "[Service]\nEnvironment=DASIK_RELOAD_PROBE=yes\n",
})
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY

$D apply main.json --target / --yes $L > /tmp/apply1.txt 2>&1 \
    || { tail -20 /tmp/apply1.txt; fail "apply failed"; }
[ -f "$DROPIN" ] || fail "dasik did not write $DROPIN"

# NO manual daemon-reload here. That is the whole point.
if needs_reload; then
    fail "NeedDaemonReload=yes after dasik wrote the drop-in — issue #300 is not fixed"
else
    echo "  ok: NeedDaemonReload=no — dasik reloaded systemd itself"
fi
if sees_probe; then
    echo "  ok: and the value is in systemd's view of the unit"
else
    fail "the drop-in is not in systemd's view of the unit"
fi
systemctl show -p Environment "$UNIT" | sed 's/^/  /' 

echo "RELOAD-C: plan is silent afterwards (the reload is not a change)"
$D plan main.json --target / $L > /tmp/plan2.txt 2>&1
if grep -qiE '^\s*[-+~] \[files\]' /tmp/plan2.txt; then
    grep -iE '^\s*[-+~] \[files\]' /tmp/plan2.txt | sed 's/^/  /'
    fail "second plan is not silent"
else
    echo "  ok: silent"
fi

echo "RELOAD-D: removing it also reaches the daemon"
python - <<'PY'
import json
import pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg["files"] = [f for f in cfg.get("files", [])
                if "10-dasik-probe" not in f["path"]]
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY
$D apply main.json --target / --yes $L > /tmp/apply2.txt 2>&1 \
    || { tail -20 /tmp/apply2.txt; fail "removal apply failed"; }
[ -f "$DROPIN" ] && fail "$DROPIN survived the removal"
if needs_reload; then
    fail "NeedDaemonReload=yes after the removal — the delete was not reloaded"
else
    echo "  ok: the delete was reloaded too"
fi
if sees_probe; then
    fail "systemd still carries the removed drop-in"
else
    echo "  ok: gone from systemd's view"
fi

echo "RELOAD-E: an install target must NOT be reloaded (no systemd under /mnt)"
mkdir -p /tmp/fakemnt
$D apply main.json --target /tmp/fakemnt --yes $L > /tmp/apply3.txt 2>&1
if grep -qE "systemctl.*daemon-reload" /tmp/apply3.txt; then
    fail "a chroot target was reloaded"
else
    echo "  ok: no reload attempted against a non-live target"
fi

rm -rf "$DROPIN_DIR"; systemctl daemon-reload
echo "RELOAD-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
