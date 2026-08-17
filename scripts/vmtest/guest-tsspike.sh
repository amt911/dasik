#!/bin/bash
# SPIKE (throwaway): pin the tailscaled conffile schema empirically, inside a
# guest, as root, with no host tailscale state or TPM policy in the way.
#
#   DASIK_VM_LUKS_PASSWORD=hibpass \
#   qemu.sh drive <image> guest-tsspike.sh TSSPIKE-DONE
#
# The questions dasik's future `tailscale` block depends on, and cannot be
# answered by reading an alpha schema that ships no documentation:
#
#   Q1  which "version" string does tailscaled accept?
#   Q2  what are the exact JSON key names (Pascal / camel / kebab)?
#   Q3  is an UNKNOWN key an error, or silently ignored?   <-- the important one:
#       silent means a typo in dasik's writer converges and does nothing.
#   Q4  does `tailscale debug prefs` reflect conffile values (for import_state)?
#   Q5  is `tailscale set` REFUSED for a key the conffile owns (the authority
#       claim the whole design rests on)?
#
# Output is TSSPIKE-<n>: lines plus a final TSSPIKE-DONE rc=<n>.
set -u

rc=0
say() { echo "TSSPIKE-$*"; }

# ---- network (dasik's network action writes only hostname/hosts) ------------
iface=""
for link in /sys/class/net/*; do
    case "${link##*/}" in lo) continue;; *) iface="${link##*/}"; break;; esac
done
ip link set "$iface" up
ip addr add 10.0.2.15/24 dev "$iface" 2>/dev/null
ip route add default via 10.0.2.2 2>/dev/null
printf 'nameserver 10.0.2.3\n' > /etc/resolv.conf
if ! getent hosts archlinux.org >/dev/null 2>&1; then
    echo "TSSPIKE-DONE rc=90 (no DNS in guest on $iface)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
fi

say "0: installing tailscale"
pacman -Sy --noconfirm --needed tailscale >/tmp/pac.log 2>&1 || {
    tail -5 /tmp/pac.log
    echo "TSSPIKE-DONE rc=91 (pacman failed)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
}
tailscale version | head -1

D=/tmp/ts; mkdir -p $D

# Parse-only probe: tailscaled reads the config early. We only care about what
# it says about the FILE, so a few seconds and a kill is enough.
probe() {   # $1 label  $2 json
    printf '%s' "$2" > "$D/$1.json"
    timeout 5 tailscaled --config="$D/$1.json" --state="$D/$1.state" \
        --socket="$D/$1.sock" --tun=userspace-networking >"$D/$1.out" 2>&1
    # Anything mentioning the config file is the answer; otherwise it parsed.
    verdict="$(grep -iE "config|version|unknown|json|unmarshal|field" "$D/$1.out" | head -2 | tr '\n' '~')"
    printf '  %-18s %s\n' "$1" "${verdict:-<parsed OK, no config complaint>}"
}

say "1+2+3: schema version, key names, unknown-key handling"
probe ver-alpha0   '{"version":"alpha0"}'
probe ver-v1alpha1 '{"version":"v1alpha1"}'
probe ver-bogus    '{"version":"nope"}'
probe ver-absent   '{}'
probe key-Pascal   '{"version":"alpha0","AcceptRoutes":true}'
probe key-camel    '{"version":"alpha0","acceptRoutes":true}'
probe key-kebab    '{"version":"alpha0","accept-routes":true}'
probe key-unknown  '{"version":"alpha0","totalNonsenseKey":true}'
probe key-badtype  '{"version":"alpha0","AcceptRoutes":"yes"}'

say "4+5: does a live daemon honour it, and is `tailscale set` refused?"
# Whichever key spelling did not complain, use the Pascal one for the live test;
# the transcript above is what actually decides it.
cat > $D/live.json <<'JSON'
{
  "version": "alpha0",
  "AcceptRoutes": true,
  "AcceptDNS": false,
  "Hostname": "spike-host",
  "ShieldsUp": true
}
JSON
tailscaled --config=$D/live.json --state=$D/live.state --socket=$D/live.sock \
    --tun=userspace-networking >$D/live.out 2>&1 &
dpid=$!
for _ in $(seq 1 20); do [ -S $D/live.sock ] && break; sleep 1; done

echo "  --- tailscale debug prefs (conffile-derived) ---"
timeout 10 tailscale --socket=$D/live.sock debug prefs 2>&1 \
    | grep -iE '"(RouteAll|CorpDNS|ShieldsUp|Hostname)"' | sed 's/^/  /' \
    || { echo "  (prefs unreadable)"; rc=1; }

echo "  --- tailscale set on a conffile-owned key ---"
timeout 10 tailscale --socket=$D/live.sock set --accept-routes=false 2>&1 \
    | head -3 | sed 's/^/  /'

echo "  --- daemon log lines mentioning the config ---"
grep -iE "config" $D/live.out | head -5 | sed 's/^/  /' || true

kill $dpid 2>/dev/null

echo "TSSPIKE-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
