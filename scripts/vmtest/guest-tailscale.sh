#!/bin/bash
# The `tailscale` domain, driven day-2 against the LIVE booted guest (target /).
#
#   DASIK_VM_LUKS_PASSWORD=hibpass \
#   qemu.sh drive <image> guest-tailscale.sh TS-DONE
#
# The unit suite proves the decision; only a running daemon proves the machine.
# Two things here cannot be asserted anywhere else:
#
#   * that tailscaled actually GOT the --config flag. dasik writes a drop-in to
#     override FLAGS from EnvironmentFile=/etc/default/tailscaled, and this repo
#     has already shipped "a systemd drop-in another file outranked: planned,
#     applied, planned again, forever". So read /proc/<pid>/cmdline, not the file.
#   * that the conffile is AUTHORITATIVE — `tailscale set` must be refused, which
#     is the whole justification for choosing the conffile over `tailscale set`.
#
# Plus the round trips CLAUDE.md requires: plan -> apply -> plan silent, and
# sync -> check -> plan silent, and the domain with its block REMOVED.
#
# Ends with a single TS-DONE rc=<n> line. QEMU-only: applies against /.
set -u

rc=0
D="python -m dasik"
L="--no-log"                 # the 9p repo is read-only
fail() { echo "BAD: $*"; rc=1; }

CONF=/etc/tailscale/tailscaled.conf
DEFAULTS=/etc/default/tailscaled

# ---- network (dasik's network action writes only hostname/hosts) ------------
iface=""
for link in /sys/class/net/*; do
    case "${link##*/}" in lo) continue;; *) iface="${link##*/}"; break;; esac
done
ip link set "$iface" up
ip addr add 10.0.2.15/24 dev "$iface" 2>/dev/null
ip route add default via 10.0.2.2 2>/dev/null
printf 'nameserver 10.0.2.3\n' > /etc/resolv.conf
getent hosts archlinux.org >/dev/null 2>&1 || {
    echo "TS-DONE rc=90 (no DNS in guest on $iface)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f; }

echo "TS-A: precondition — no conffile, tailscale not installed by dasik yet"
[ -e "$CONF" ] && fail "$CONF already exists; this proves nothing"

export PYTHONPATH=/root/repo
rm -rf /root/cfg && mkdir -p /root/cfg && cd /root/cfg || {
    echo "TS-DONE rc=93"; poweroff -f; }
# The config the machine was INSTALLED from, plus the block under test.
#
# A minimal config here is not a smaller test, it is a different one: the
# reconciler hands an action its EMPTY config for every domain a previous
# generation owned, so a bare {"hostname":…, "tailscale":…} correctly plans to
# uninstall base, linux and dracut. The first run of this script did exactly
# that, and pacman refused it because pacman is a HoldPkg. Start from the real
# config so the only divergence is the one under test.
cp /root/repo/config/vm-p14s-hibernate.json main.json
python - <<'PY'
import json
import pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg["tailscale"] = {
    "accept_routes": True,
    "accept_dns": False,
    "shields_up": True,
    "hostname": "spike-box",
}
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY

echo "TS-B: plan sees the domain on a machine that lacks it"
$D plan main.json --target / $L > /tmp/plan1.txt 2>&1
cat /tmp/plan1.txt | grep -iE "tailscale" | head -10
grep -qiE '\[tailscale\]' /tmp/plan1.txt \
    || fail "the tailscale domain is invisible in plan"
grep -qE "/etc/default/tailscaled" /tmp/plan1.txt \
    || fail "the --config EnvironmentFile is not announced in plan"

echo "TS-C: apply"
$D apply main.json --target / --yes $L > /tmp/apply1.txt 2>&1 \
    || { tail -20 /tmp/apply1.txt; fail "apply failed"; }
echo "  --- conffile as written ---"; cat "$CONF" 2>&1 | sed 's/^/  /'
echo "  --- EnvironmentFile as written ---";  cat "$DEFAULTS" 2>&1 | sed 's/^/  /'
grep -q '"AcceptRoutes": true' "$CONF" || fail "AcceptRoutes missing from $CONF"
grep -q '"AcceptDNS": false'   "$CONF" || fail "AcceptDNS missing from $CONF"
grep -q '"version": "alpha0"'  "$CONF" || fail "the mandatory version is missing"

echo "TS-D: plan again — must be silent (idempotent)"
$D plan main.json --target / $L > /tmp/plan2.txt 2>&1
grep -qiE '^\s*[-+~] \[(tailscale|files)\]' /tmp/plan2.txt \
    && { grep -iE '^\s*[-+~] \[(tailscale|files)\]' /tmp/plan2.txt; \
         fail "second plan is not silent"; } || echo "  ok: silent"

echo "TS-E: the DAEMON got the flag (not just the file on disk)"
systemctl daemon-reload
systemctl restart tailscaled
sleep 3
pid=$(systemctl show -p MainPID --value tailscaled)
if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    systemctl status tailscaled --no-pager | tail -15
    fail "tailscaled is not running after apply"
else
    cmdline=$(tr '\0' ' ' < /proc/$pid/cmdline)
    echo "  /proc/$pid/cmdline: $cmdline"
    case "$cmdline" in
        *"--config=$CONF"*) echo "  ok: the daemon got the flag" ;;
        *) fail "tailscaled was started WITHOUT --config" ;;
    esac
fi

echo "TS-F: the daemon HONOURED it (conffile AcceptRoutes -> prefs RouteAll)"
tailscale debug prefs 2>/dev/null | grep -E '"(RouteAll|CorpDNS|ShieldsUp|Hostname)"' | sed 's/^/  /'
tailscale debug prefs 2>/dev/null | grep -q '"RouteAll": true' \
    || fail "RouteAll is not true — the conffile was read but not applied"
tailscale debug prefs 2>/dev/null | grep -q '"CorpDNS": false' \
    || fail "CorpDNS is not false (conffile AcceptDNS)"

echo "TS-G: the conffile is AUTHORITATIVE — the CLI must be refused"
out=$(tailscale set --accept-routes=false 2>&1 | head -2)
echo "  $out"
case "$out" in
    *"config file is locked"*|*"can't reconfigure"*) echo "  ok: CLI locked out" ;;
    *) fail "tailscale set was NOT refused — the conffile is not authoritative" ;;
esac

echo "TS-H: sync captures it, check accepts the capture, plan stays silent"
$D sync main.json --target / $L > /tmp/sync.txt 2>&1 || fail "sync failed"
grep -A8 '"tailscale"' main.json | head -10 | sed 's/^/  /'
grep -q 'accept_routes' main.json || fail "sync did not capture accept_routes"
grep -q 'shields_up'    main.json || fail "sync did not capture shields_up"
$D check main.json $L || fail "the captured config is rejected by check"
$D plan main.json --target / $L > /tmp/plan3.txt 2>&1
if grep -qiE '^\s*[-+~] \[tailscale\]' /tmp/plan3.txt; then
    fail "sync -> plan is not silent"
else
    echo "  ok: sync -> check -> plan silent"
fi

echo "TS-I: the block REMOVED — both files go, and the CLI is free again"
python - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("main.json").read_text())
cfg.pop("tailscale", None)
pathlib.Path("main.json").write_text(json.dumps(cfg, indent=2))
PY
$D plan main.json --target / $L > /tmp/plan4.txt 2>&1
grep -iE '^\s*[-~] \[(tailscale|files)\]' /tmp/plan4.txt | head -5 | sed 's/^/  /'
grep -qiE '^\s*[-~] \[tailscale\]' /tmp/plan4.txt \
    || fail "dropping the block plans no removal — the conffile would linger and keep the CLI locked"
$D apply main.json --target / --yes $L > /tmp/apply2.txt 2>&1 \
    || { tail -20 /tmp/apply2.txt; fail "removal apply failed"; }
if [ -e "$CONF" ]; then fail "$CONF survived the removal"; else echo "  ok: conffile gone"; fi
if [ -e "$DEFAULTS" ]; then fail "$DEFAULTS survived the removal"; else echo "  ok: EnvironmentFile restored/removed"; fi
systemctl restart tailscaled; sleep 3
out=$(tailscale set --accept-routes=false 2>&1 | head -2)
case "$out" in
    *"config file is locked"*) fail "still locked out after removal: $out" ;;
    *) echo "  ok: the CLI works again" ;;
esac

echo "TS-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
