#!/bin/bash
# A wg-quick .conf served by NetworkManager, converted by nmcli inside the target.
#
# dasik used to refuse this pair. The reasoning was half right: hand-translating
# between the formats would be a second copy of a private key nobody reviewed,
# and `nmcli connection import` needs a running daemon that no chroot has. But
# `nmcli --offline connection add` needs neither, so nmcli still writes the
# secret and the conversion happens where the install happens.
#
# This asserts the whole round trip against the LIVE host (target /), and needs
# no network: the package is already installed and --offline never talks to the
# daemon.
#
# Ends with WGNM-DONE, then powers off.
set -x
cd /root/repo || { echo "WGNM-DONE rc=91"; poweroff -f; }

D="python -m dasik"
L="--no-log"
C=config/vm-wireguard-nm.json
K=/etc/NetworkManager/system-connections/vm-nm-tunnel.nmconnection
W=/root/wgnm-work
mkdir -p "$W"
echo "WGNM: BEGIN"

echo "WGNM-A: the install converted it — a keyfile, not the .conf"
test -f "$K"; echo "WGNM-KEYFILE-RC=$?"
test -f /etc/wireguard/vm-nm-tunnel.conf; echo "WGNM-NO-WGQUICK-FILE-RC=$?"
stat -c '%a' "$K"; echo "WGNM-MODE=$(stat -c '%a' "$K")"
grep -c '^\[wireguard-peer\.' "$K"
grep -E '^(id|uuid|type|interface-name|autoconnect)=' "$K"
grep -E '^(address1|dns|method)=' "$K"

echo "WGNM-B: nmcli itself accepts what it produced"
nmcli --offline connection show "$K" >/dev/null 2>&1
python - <<'EOF'
body = open('/etc/NetworkManager/system-connections/vm-nm-tunnel.nmconnection').read()
print('WGNM-HAS-PRIVKEY=%s' % ('private-key=' in body))
print('WGNM-HAS-ENDPOINT=%s' % ('endpoint=198.51.100.7:51820' in body))
print('WGNM-ALLOWED-IPS-OK=%s' % ('allowed-ips=0.0.0.0/0;::/0;' in body))
print('WGNM-AUTOCONNECT-NOT-NO=%s' % ('autoconnect=false' not in body))
EOF

echo "WGNM-C: the plan right after the install must be SILENT"
$D plan "$C" --target / $L; echo "WGNM-PLAN-RC=$?"

echo "WGNM-D: plan/apply/plan must end in silence — the uuid is the trap here"
$D apply "$C" --target / --yes $L; echo "WGNM-APPLY-RC=$?"
$D plan "$C" --target / $L; echo "WGNM-REPLAN-RC=$?"
sha256sum "$K" > "$W/before"
$D apply "$C" --target / --yes $L
sha256sum "$K" > "$W/after"
diff -q "$W/before" "$W/after"; echo "WGNM-BYTE-STABLE-RC=$?"

echo "WGNM-E: break it on the machine — the drift must be planned and repaired"
sed -i 's/^persistent-keepalive=25/persistent-keepalive=99/' "$K"
$D plan "$C" --target / $L | grep -E "wireguard"; echo "WGNM-DRIFT-RC=$?"
$D apply "$C" --target / --yes $L; echo "WGNM-REPAIR-RC=$?"
grep -c '^persistent-keepalive=25' "$K"; echo "WGNM-REPAIRED-RC=$?"
$D plan "$C" --target / $L; echo "WGNM-AFTERFIX-RC=$?"

echo "WGNM-F: sync must keep the .conf the config declares, not the keyfile"
# `source` resolves relative to the config that names it, so the copy has to
# take wg/ with it — moving the json alone leaves the tunnel file unreachable.
mkdir -p "$W/wg" && cp config/wg/*.conf "$W/wg/"
cp "$C" "$W/captured.json"
$D sync "$W/captured.json" --target / $L; echo "WGNM-SYNC-RC=$?"
python - <<'EOF'
import json
tun = json.load(open('/root/wgnm-work/captured.json')).get('wireguard') or []
for t in tun:
    print('WGNM-CAPTURED name=%s backend=%s source=%s' % (
        t.get('name'), t.get('backend'), t.get('source')))
print('WGNM-SOURCE-STILL-CONF=%s' % any(
    str(t.get('source', '')).endswith('.conf') for t in tun))
EOF
$D check "$W/captured.json"; echo "WGNM-CHECK-RC=$?"

echo "WGNM-DONE rc=0"
[ -n "$DASIK_VM_NOPOWEROFF" ] || poweroff -f
