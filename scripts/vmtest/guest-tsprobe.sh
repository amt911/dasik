#!/bin/bash
# MINIMAL EXPERIMENT (throwaway): pick the mechanism that actually delivers
# --config to the running tailscaled.
#
# Already measured, on this image:
#   * a drop-in with `Environment=FLAGS=...` does NOT reach the daemon, with or
#     without `systemctl daemon-reload`. The EnvironmentFile wins.
#
# So two candidates remain, and this measures both rather than assuming:
#   M1  own /etc/default/tailscaled — pacman marks it a BACKUP file, i.e. the
#       vendor's designated knob, preserved across upgrades with a .pacnew
#   M2  a drop-in that clears and restates ExecStart — guaranteed to work, but
#       duplicates a command line that can change with any tailscale release
#
# dasik is deliberately not involved. Ends with TSPROBE-DONE rc=<n>.
set -u

CONF=/etc/tailscale/tailscaled.conf
DEFAULTS=/etc/default/tailscaled
DROPIN_DIR=/etc/systemd/system/tailscaled.service.d
DROPIN=$DROPIN_DIR/10-dasik.conf

if ! command -v tailscaled >/dev/null 2>&1; then
    iface=""
    for link in /sys/class/net/*; do
        case "${link##*/}" in lo) continue;; *) iface="${link##*/}"; break;; esac
    done
    ip link set "$iface" up
    ip addr add 10.0.2.15/24 dev "$iface" 2>/dev/null
    ip route add default via 10.0.2.2 2>/dev/null
    printf 'nameserver 10.0.2.3\n' > /etc/resolv.conf
    pacman -Sy --noconfirm --needed tailscale >/tmp/pac.log 2>&1 || {
        tail -5 /tmp/pac.log
        echo "TSPROBE-DONE rc=91 (could not install tailscale)"
        [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f; }
fi

mkdir -p "$(dirname "$CONF")"
printf '{"version":"alpha0","AcceptRoutes":true,"AcceptDNS":false}\n' > "$CONF"
cp "$DEFAULTS" /tmp/defaults.orig

running_cmdline() {
    pid=$(systemctl show -p MainPID --value tailscaled)
    if [ -z "$pid" ] || [ "$pid" = "0" ]; then echo "<not running>"; return; fi
    tr '\0' ' ' < /proc/"$pid"/cmdline
}

verdict_for() {   # $1 label
    systemctl daemon-reload
    systemctl restart tailscaled; sleep 3
    cmd=$(running_cmdline)
    echo "  cmdline: $cmd"
    prefs=$(tailscale debug prefs 2>/dev/null | grep -E '"(RouteAll|CorpDNS)"' | tr -d ' \n')
    echo "  prefs:   $prefs"
    case "$cmd" in
        *"--config=$CONF"*)
            case "$prefs" in
                *'"RouteAll":true'*) echo "  RESULT $1: WORKS — flag delivered and honoured"; return 0 ;;
                *) echo "  RESULT $1: flag delivered but prefs NOT applied"; return 1 ;;
            esac ;;
        *) echo "  RESULT $1: flag NOT delivered"; return 1 ;;
    esac
}

echo "TSPROBE-M1: own /etc/default/tailscaled (the pacman BACKUP file)"
rm -rf "$DROPIN_DIR"
cat > "$DEFAULTS" <<'ENV'
# Managed by dasik.
PORT="41641"
FLAGS="--config=/etc/tailscale/tailscaled.conf"
ENV
m1=0; verdict_for M1 || m1=1

echo "TSPROBE-M2: a drop-in that clears and restates ExecStart"
cp /tmp/defaults.orig "$DEFAULTS"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<'INI'
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=${PORT} --config=/etc/tailscale/tailscaled.conf
INI
m2=0; verdict_for M2 || m2=1

echo "TSPROBE-VERDICT M1=$([ $m1 -eq 0 ] && echo WORKS || echo FAILS) M2=$([ $m2 -eq 0 ] && echo WORKS || echo FAILS)"

# Leave the machine as found.
cp /tmp/defaults.orig "$DEFAULTS"
rm -rf "$DROPIN_DIR" "$CONF"
systemctl daemon-reload; systemctl restart tailscaled 2>/dev/null

rc=1; [ $m1 -eq 0 ] || [ $m2 -eq 0 ] && rc=0
echo "TSPROBE-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
