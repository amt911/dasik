#!/bin/bash
# SPIKE (throwaway): pin the tailscaled conffile schema empirically, inside a
# guest, as root, with no host tailscale state or TPM policy in the way.
#
#   DASIK_VM_LUKS_PASSWORD=hibpass \
#   qemu.sh drive <image> guest-tsspike.sh TSSPIKE-DONE
#
# Pass 1 (already run) established: version must be exactly "alpha0"; an unknown
# key is a HARD ERROR (json: unknown field); a wrong type likewise; the conffile
# locks `tailscale set` out; and conffile AcceptRoutes reads back as prefs
# RouteAll, AcceptDNS as CorpDNS.
#
# Pass 2 (this script) uses that hard error as an ORACLE to enumerate the key
# names, so dasik's model is built from what the binary accepts rather than from
# recollection of Go source. A parse failure exits instantly; a parse success
# hangs (the daemon runs), so a 2s timeout separates them cleanly.
#
# Output: TSSPIKE-KEY <name> <ACCEPTED|REJECTED> lines, then TSSPIKE-DONE rc=<n>.
set -u

rc=0

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

pacman -Sy --noconfirm --needed tailscale >/tmp/pac.log 2>&1 || {
    tail -5 /tmp/pac.log
    echo "TSSPIKE-DONE rc=91 (pacman failed)"
    [ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
}
echo "TSSPIKE-VERSION $(tailscale version | head -1)"

D=/tmp/ts; mkdir -p $D

# A representative value per candidate type, so a REJECTED verdict means "no
# such key" and never "right key, wrong type" — the type error has its own
# distinct message, which is reported as BADTYPE rather than swallowed.
probe_key() {   # $1 key  $2 json-value
    f=$D/k.json
    printf '{"version":"alpha0","%s":%s}' "$1" "$2" > $f
    out=$(timeout 2 tailscaled --config=$f --state=mem: --socket=$D/k.sock \
              --tun=userspace-networking 2>&1 | head -5)
    case "$out" in
        *"unknown field"*)  verdict=REJECTED ;;
        *"invalid "*|*"cannot unmarshal"*|*"parsing config file"*) verdict="BADTYPE  <- key exists, value shape wrong: $(printf '%s' "$out" | grep -oE 'invalid [^"]*|cannot unmarshal [^"]*' | head -1)" ;;
        *)                  verdict=ACCEPTED ;;
    esac
    printf 'TSSPIKE-KEY %-28s %s\n' "$1" "$verdict"
}

echo "TSSPIKE-PASS2: enumerating conffile keys"
probe_key Version                    '"alpha0"'
probe_key Locked                     '"false"'
probe_key ServerURL                  '"https://controlplane.tailscale.com"'
probe_key AuthKey                    '"tskey-fake"'
probe_key Enabled                    'true'
probe_key OperatorUser               '"andres"'
probe_key Hostname                   '"spike"'
probe_key AcceptDNS                  'true'
probe_key AcceptRoutes               'true'
probe_key ExitNode                   '"100.64.0.1"'
probe_key AllowLANWhileUsingExitNode 'true'
probe_key ExitNodeAllowLANAccess     'true'
probe_key AdvertiseRoutes            '["10.0.0.0/8"]'
probe_key AdvertiseTags              '["tag:server"]'
probe_key AdvertiseExitNode          'true'
probe_key DisableSNAT                'true'
probe_key NoSNAT                     'true'
probe_key NetfilterMode              '"on"'
probe_key NoStatefulFiltering        'true'
probe_key PostureChecking            'true'
probe_key RunSSHServer               'true'
probe_key SSH                        'true'
probe_key RunWebClient               'true'
probe_key ShieldsUp                  'true'
probe_key ForceDaemon                'true'
probe_key AutoUpdate                 '{"check":true}'
probe_key AppConnector               '{"advertise":false}'
probe_key RelayServerPort            '41641'
probe_key StaticEndpoints            '["192.0.2.1:41641"]'

echo "TSSPIKE-DONE rc=$rc"
sync
[ -n "${DASIK_VM_NOPOWEROFF:-}" ] || poweroff -f
